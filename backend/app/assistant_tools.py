"""Identity-bound assistant reads; confirmed analysis purchases are separate."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import json
import math
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import assistant_help, subscription_core, v25_execution
from .analyst_credits import ANALYSIS_COST, AnalystCredits, ConsumeAnalysis, CreditConfig, CreditTransaction
from .analyst_credits import service as credit_service
from .assistant_config import AssistantConfig, load_assistant_config
from .member_entitlements import access_for_request, subscription_for_request
from .premium_access import public_projection
from .subscription_core import parse_datetime
from .v22_commercial import authenticated_user

Language = Literal["tr", "en"]


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnalysisArguments(NoArguments):
    symbol: str = ConsumeAnalysis.model_fields["symbol"]
    timeframe: str = ConsumeAnalysis.model_fields["timeframe"]


class HelpArguments(NoArguments):
    query: str = Field(min_length=1)
    language: Language = "tr"


class ProtectionArguments(NoArguments):
    symbol: str = ConsumeAnalysis.model_fields["symbol"]
    direction: Literal["LONG", "SHORT"] | None = None
    language: Language = "tr"


class TradesArguments(NoArguments):
    days: int = Field(default=7, ge=1, le=30)


class EventsArguments(NoArguments):
    limit: int = Field(default=15, ge=1, le=30)


class ConfirmAnalysis(AnalysisArguments):
    confirm: StrictBool
    confirmation_token: str = Field(min_length=1)


ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "get_plans": NoArguments,
    "get_my_access": NoArguments,
    "get_my_credits": NoArguments,
    "search_help": HelpArguments,
    "get_analysis": AnalysisArguments,
    "get_my_positions": NoArguments,
    "get_demo_positions": NoArguments,
    "get_market_overview": NoArguments,
    "get_protection_status": ProtectionArguments,
    "get_live_strategy": NoArguments,
    "get_my_account_summary": NoArguments,
    "get_my_settings": NoArguments,
    "get_my_trades": TradesArguments,
    "get_my_events": EventsArguments,
    "get_my_position_details": NoArguments,
}
# Üyenin kendi hesabını okuyan araçlar: profildeki "Kais hesabımı görebilir" izni kapalıysa hiçbiri veri döndürmez.
ACCOUNT_TOOLS = frozenset({"get_my_account_summary", "get_my_settings", "get_my_trades", "get_my_events", "get_my_position_details"})
ACCOUNT_ACCESS_OFF = "ACCOUNT_ACCESS_OFF"
MAX_TRADE_ROWS = 50
TOOL_DEFINITIONS = [
    {"name": name, "input_schema": model.model_json_schema()}
    for name, model in ARGUMENT_MODELS.items()
]


class AssistantTools:
    def __init__(self, request: Request, *, config: AssistantConfig | None = None, clock: Callable[[], float] = time.time):
        self.request = request
        self.config = config or load_assistant_config()
        self.clock = clock

    def user(self, user_id: str) -> dict[str, Any]:
        user = authenticated_user(self.request)
        if str(user["id"]) != user_id:
            raise HTTPException(403, "Assistant identity does not match the authenticated member")
        return user

    def premium(self, user_id: str) -> bool:
        return bool(access_for_request(self.request, self.user(user_id))["isPremium"])

    def credits(self) -> AnalystCredits:
        try:
            return credit_service(self.request)
        except ValueError:
            raise HTTPException(503, "Analyst credit configuration is unavailable") from None

    def result(self, data: dict[str, Any], *, stale: bool = False) -> dict[str, Any]:
        return {"data": data, "fetched_at": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(), "stale": stale}

    async def get_plans(self, user_id: str) -> dict[str, Any]:
        self.user(user_id)
        return self.result({
            "plans": [
                {"id": key, "name": row["name"], "monthly_price": row["monthly_price"], "currency": "USD"}
                for key, row in subscription_core.PLAN_CATALOG.items()
            ],
            "trial_days": subscription_core.TRIAL_DAYS,
            "cancellation": dict(subscription_core.CANCELLATION_RULES),
        })

    async def get_my_access(self, user_id: str) -> dict[str, Any]:
        user = self.user(user_id)
        access = access_for_request(self.request, user)
        subscription = subscription_for_request(self.request, user)
        return self.result({"plan": subscription["plan"], "status": subscription["status"], "isPremium": bool(access["isPremium"])})

    async def get_my_credits(self, user_id: str) -> dict[str, Any]:
        premium = self.premium(user_id)
        credits = self.credits()
        snapshot = await credits.credits(user_id, premium)
        reset = parse_datetime(snapshot["resetsAt"])
        return self.result({
            **snapshot,
            "resets_in_seconds": max(0, math.ceil(reset.timestamp() - credits.clock())) if reset else None,
            "window_hours": credits.config.window_hours, "cache_minutes": credits.config.cache_minutes,
        })

    async def search_help(self, user_id: str, query: str, language: Language = "tr") -> dict[str, Any]:
        self.user(user_id)
        if len(query) > self.config.max_input_chars:
            raise HTTPException(422, "Help query exceeds the configured message limit")
        try:
            results = assistant_help.search(query, language, self.credits().config)
        except assistant_help.KnowledgeBaseError:
            raise HTTPException(503, "Help knowledge base is unavailable") from None
        return self.result({"results": results})

    def confirmation_token(self, user_id: str, symbol: str, timeframe: str, cost: int) -> str:
        self.user(user_id)
        credits = self.credits()
        claims = json.dumps({
            "symbol": symbol, "timeframe": timeframe, "cost": cost, "nonce": uuid.uuid4().hex,
            "expires_at": self.clock() + credits.config.cache_minutes * 60,
        }, sort_keys=True, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(claims.encode()).decode()
        secret = self.request.app.state.v22_commercial["secret"]
        signature = hmac.new(secret, f"assistant-analysis:{user_id}:{encoded}".encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def verify_confirmation(self, user_id: str, payload: ConfirmAnalysis) -> str:
        if not payload.confirm:
            raise HTTPException(422, "Explicit analysis confirmation is required")
        try:
            encoded, signature = payload.confirmation_token.split(".")
            secret = self.request.app.state.v22_commercial["secret"]
            expected = hmac.new(secret, f"assistant-analysis:{user_id}:{encoded}".encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise HTTPException(403, "Invalid analysis confirmation")
            claims = json.loads(base64.urlsafe_b64decode(encoded))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(403, "Invalid analysis confirmation") from None
        credits = self.credits()
        cost = 0 if self.premium(user_id) else credits.config.cost
        if (
            claims["symbol"] != payload.symbol or claims["timeframe"] != payload.timeframe
            or claims["expires_at"] <= self.clock() or claims["cost"] != cost
        ):
            raise HTTPException(409, "Analysis confirmation expired or its target/cost changed")
        return hashlib.sha256(payload.confirmation_token.encode()).hexdigest()

    def analysis_summary(self, raw: dict[str, Any], user_id: str, symbol: str, timeframe: str, *, cached: bool) -> dict[str, Any]:
        timestamp = parse_datetime(raw.get("updated_at") or raw.get("timestamp"))
        delta = self.clock() - timestamp.timestamp() if timestamp else None
        age = math.ceil(delta) if delta is not None and delta >= 0 else None
        data = {
            "symbol": symbol, "timeframe": timeframe, "cached": cached,
            "direction": raw.get("direction"),
            "final_decision_score": raw.get("final_decision_score"),
            "confidence": raw.get("confidence"), "opportunity_score": raw.get("opportunity_score"),
            "mtf_alignment": raw.get("mtf_alignment"), "data_age_seconds": age,
            "data_age_reason": "ANALYSIS_TIMESTAMP_UNAVAILABLE" if age is None else None,
        }
        if not self.premium(user_id):
            data = public_projection(data)
        return self.result(data, stale=age is None or age >= self.credits().config.cache_minutes * 60)

    async def get_analysis(self, user_id: str, symbol: str, timeframe: str) -> dict[str, Any]:
        premium = self.premium(user_id)
        AnalysisArguments(symbol=symbol, timeframe=timeframe)
        credits = self.credits()
        cached = await credits.cached_analysis(user_id, symbol, timeframe)
        if cached is not None:
            return self.analysis_summary(cached["result"], user_id, symbol, timeframe, cached=True)
        cost = 0 if premium else credits.config.cost
        return self.result({"needs_confirmation": {
            "action": "get_analysis", "symbol": symbol, "timeframe": timeframe,
            "cost": cost, "analysis_cost": credits.config.cost,
            "confirmation_token": self.confirmation_token(user_id, symbol, timeframe, cost),
        }})

    async def confirm_analysis(self, user_id: str, payload: ConfirmAnalysis) -> dict[str, Any]:
        self.user(user_id)
        key = self.verify_confirmation(user_id, payload)
        producer = self.request.app.state.analyst_analysis
        response, status = await self.credits().consume(
            user_id, self.premium(user_id), payload.symbol, payload.timeframe, key,
            lambda: producer(payload.symbol, payload.timeframe),
        )
        if status != 200:
            raise HTTPException(status, response)
        return self.analysis_summary(response["result"], user_id, payload.symbol, payload.timeframe, cached=response["cached"])

    async def account_state(self, user_id: str) -> dict[str, Any] | None:
        member_id = str(self.user(user_id)["id"])
        state = getattr(self.request.app.state, "v25_execution", None)
        if not isinstance(state, dict) or not isinstance(state.get("lock"), asyncio.Lock):
            return None
        lock = state["lock"]
        await asyncio.wait_for(lock.acquire(), timeout=self.config.request_timeout_seconds)
        try:
            return v25_execution.read_owned_account_state(
                state, member_id, f"owner:{member_id}",
            )
        finally:
            lock.release()

    def account_age(self, state: dict[str, Any] | None) -> tuple[int | None, bool]:
        connection = (state or {}).get("connection")
        checked = parse_datetime(connection.get("last_checked")) if isinstance(connection, dict) else None
        delta = self.clock() - checked.timestamp() if checked else None
        threshold = max(self.config.protection_stale_seconds, 2 * v25_execution.RECONCILE_SECONDS)
        return (math.ceil(delta) if delta is not None and delta >= 0 else None,
                delta is None or delta < 0 or delta > threshold)

    async def get_my_positions(self, user_id: str) -> dict[str, Any]:
        state = await self.account_state(user_id)
        age, stale = self.account_age(state)
        if state is None:
            return self.result({"positions": [], "reason": "ACCOUNT_NOT_VERIFIED", "data_age_seconds": None}, stale=True)
        stale = stale or not state.get("connected") or bool(state["connection"].get("last_error")) or not state.get("recovery_ready")
        positions = [
            {key: row.get(key) for key in ("symbol", "direction", "quantity", "unrealized_pnl")}
            for row in state["snapshot"].get("positions", [])
            if row.get("quantity")
        ]
        if not self.premium(user_id):
            positions = public_projection(positions)
        return self.result({"positions": positions, "data_age_seconds": age}, stale=stale)

    async def get_demo_positions(self, user_id: str) -> dict[str, Any]:
        """Üyenin KENDİ Binance Demo hesabındaki açık pozisyonlar (salt okunur; gerçek hesap değil). Şimdilik yalnızca OWNER.
        Veri dar servis arayüzünden (readonly_demo_service) gelir; seviye (entry/SL/TP) döndürülmez."""
        user = self.user(user_id)
        if user.get("role") != "OWNER":
            return self.result({"positions": [], "reason": "DEMO_OWNER_ONLY", "account": "demo"}, stale=True)
        from .readonly_demo_service import read_demo_positions
        try:
            positions = await read_demo_positions(self.request, self.config.request_timeout_seconds)
        except Exception:  # borsa/anahtar/zaman aşımı: ayrıntıyı sızdırma, doğrulanamadı de
            return self.result({"positions": [], "reason": "DEMO_UNAVAILABLE", "account": "demo"}, stale=True)
        return self.result({"positions": positions, "account": "demo", "data_age_seconds": 0})

    async def get_market_overview(self, user_id: str) -> dict[str, Any]:
        """Piyasa özeti (salt okunur, yalnızca premium). Tarama sıralaması yalnızca TAZE taramada verilir; eski/yok ise sıralama uydurulmaz.
        scan.status: fresh | stale | no_scan | unavailable. Gainer/loser yalnızca kesin pozitif/negatif 24s değişimle."""
        premium = self.premium(user_id)
        free_limit = int(getattr(self.config, "free_market_daily_limit", 0) or 0)
        if not premium and free_limit <= 0:
            return self.result({"reason": "PREMIUM_REQUIRED"}, stale=True)
        from .readonly_demo_service import (
            MAX_SIGNAL_AGE_SECONDS,
            MIN_MOVER_VOLUME_USDT,
            read_market_movers,
            read_scanner_candidates,
        )
        data: dict[str, Any] = {}
        stale = False
        try:
            raw = read_scanner_candidates(self.request)
            scanned = parse_datetime(raw.get("last_scan_at")) if raw.get("last_scan_at") else None
            age = self.clock() - scanned.timestamp() if scanned else None
            scan: dict[str, Any] = {"last_scan_at": raw.get("last_scan_at"), "timeframe": raw.get("timeframe"), "coins_scanned": raw.get("coins_scanned"),
                                    "max_age_seconds": MAX_SIGNAL_AGE_SECONDS}
            if raw.get("last_scan_at") and (age is None or age < 0):  # okunamayan/gelecekteki zaman (analysis_summary ile aynı kural)
                scan.update({"status": "unavailable", "note": "SCAN_TIMESTAMP_INVALID"}); stale = True
            elif age is None or not raw.get("candidate_count"):
                scan.update({"status": "no_scan", "note": "NO_SCAN_RESULTS_YET"}); stale = True
            elif age > MAX_SIGNAL_AGE_SECONDS:
                scan.update({"status": "stale", "age_seconds": math.floor(age)}); stale = True  # eski taramadan sıralama verilmez
            else:
                eligible = raw["eligible"]
                def brief(row: dict[str, Any]) -> dict[str, Any]:
                    return {key: row.get(key) for key in ("symbol", "direction", "opportunity_score", "analysis_score", "confidence_value", "confidence_label")}
                def best(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
                    # Aynı skorda güven değeri ve sembol ile deterministik sıralama.
                    return brief(max(rows, key=lambda row: (float(row.get(key) or 0), float(row.get("confidence_value") or 0), str(row.get("symbol"))))) if rows else None
                scan.update({"status": "fresh", "age_seconds": math.floor(age), "eligible_count": len(eligible),
                             "best_long": best([r for r in eligible if r["direction"] == "LONG"], "opportunity_score"),
                             "best_short": best([r for r in eligible if r["direction"] == "SHORT"], "opportunity_score"),
                             "highest_confidence": best(eligible, "confidence_value"),
                             "highest_opportunity": best(eligible, "opportunity_score")})
            data["scan"] = scan
        except Exception:
            data["scan"] = {"status": "unavailable", "note": "SCAN_UNAVAILABLE"}; stale = True
        try:
            rows = await read_market_movers(self.request, self.config.request_timeout_seconds)
            pick = lambda row: {"symbol": row["symbol"], "change_24h_percent": round(row["change"], 2), "price": row["price"]}
            data["movers"] = {"status": "fresh", "min_volume_usdt": MIN_MOVER_VOLUME_USDT,
                              "top_gainers": [pick(r) for r in sorted((r for r in rows if r["change"] > 0), key=lambda r: r["change"], reverse=True)[:3]],
                              "top_losers": [pick(r) for r in sorted((r for r in rows if r["change"] < 0), key=lambda r: r["change"])[:3]]}
        except Exception:
            data["movers"] = {"status": "unavailable", "note": "MARKET_DATA_UNAVAILABLE"}; stale = True
        if not premium:
            # Gösterilecek hiçbir şey yoksa kredi düşülmez; varsa günlük hak + kredi tek işlemde kontrol edilip düşülür.
            if data["scan"].get("status") != "fresh" and data["movers"].get("status") != "fresh":
                return self.result(data, stale=True)
            day = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
            usage = await self.credits().charge_market_overview(user_id, day, free_limit)
            allowed = usage.pop("allowed")
            if not allowed:
                return self.result({"reason": usage.pop("reason"), "free_usage": usage}, stale=True)
            data["free_usage"] = usage
        return self.result(data, stale=stale)

    async def get_live_strategy(self, user_id: str) -> dict[str, Any]:
        """Kais Original V2 canlı stratejisinin özeti (salt okunur): çalışıyor mu, oturum, risk ayarı, günlük sınır, son tarama ve
        işlem açılmama sebepleri. Yalnız canlı oturumun sahibine; seviye (entry/SL/TP), anahtar veya başka üye verisi döndürmez."""
        member_id = str(self.user(user_id)["id"])
        state = getattr(self.request.app.state, "v25_execution", None)
        if not isinstance(state, dict) or not isinstance(state.get("lock"), asyncio.Lock):
            return self.result({"reason": "LIVE_NOT_VERIFIED"}, stale=True)
        from . import original_v2_live
        lock = state["lock"]
        await asyncio.wait_for(lock.acquire(), timeout=self.config.request_timeout_seconds)
        try:
            if (state.get("live_session_authorization") or {}).get("user_id") != member_id:
                return self.result({"reason": "LIVE_NOT_OWNER"}, stale=True)
            config = original_v2_live.public_config(state)
        finally:
            lock.release()
        scanner = config.get("scanner") if isinstance(config.get("scanner"), dict) else {}
        counts = scanner.get("rejection_counts") if isinstance(scanner.get("rejection_counts"), dict) else {}
        top = sorted(((str(key), value) for key, value in counts.items() if isinstance(value, int) and value > 0), key=lambda row: -row[1])[:3]
        near = [{key: row.get(key) for key in ("symbol", "reason_code", "confidence", "confidence_threshold")}
                for row in (scanner.get("near_misses") or [])[:3] if isinstance(row, dict)]
        daily = config.get("daily") if isinstance(config.get("daily"), dict) else {}
        failure = scanner.get("last_failure") if isinstance(scanner.get("last_failure"), dict) else None
        return self.result({
            "enabled": bool(config.get("enabled")), "paused": bool(config.get("paused")), "pause_reason": config.get("pause_reason"),
            "session_remaining_seconds": (config.get("session") or {}).get("remaining_seconds", 0),
            "risk_percent": config.get("risk_percent"), "daily_loss_percent": config.get("daily_loss_percent"),
            "daily": {key: daily.get(key) for key in ("loss", "limit", "locked")},
            "fill_lock_active": bool((config.get("fill_lock") or {}).get("active")),
            "scanner": {
                "stage": scanner.get("stage"), "last_scan_at": scanner.get("last_scan_at"),
                "scanned_symbol_count": scanner.get("scanned_symbol_count"), "candidate_count": scanner.get("candidate_count"),
                "last_24h": scanner.get("last_24h"), "rejections_24h": scanner.get("rejections_24h"),
                "top_rejections": [{"reason_code": key, "count": value} for key, value in top],
                "near_misses": near, "last_failure": failure.get("message") if failure else None,
            },
        })

    def account_context(self, user_id: str, language: Language) -> str:
        """Sunucunun doğruladığı hesap durumu (model premium/admin durumunu tahmin etmesin, uydurmasın). Doğrulanamazsa boş."""
        try:
            user = self.user(user_id)
            rights = access_for_request(self.request, user)
        except Exception:  # noqa: BLE001 - yalnız yardımcı bağlam; doğrulanamazsa model bu bilgiyi almaz, sohbet sürer
            return ""
        owner = user.get("role") == "OWNER"
        premium = bool(rights.get("isPremium"))
        unlimited = bool((rights.get("entitlements") or {}).get("unlimitedAnalyst"))
        trial = bool(rights.get("freeTrial"))
        limit = int(getattr(self.config, "free_market_daily_limit", 0) or 0)
        try:  # yalnız yapılandırma okunur; kredi servisi (uygulama durumu) oluşturulmaz
            cost = CreditConfig.from_environment().cost
        except ValueError:
            cost = ANALYSIS_COST
        tr = language == "tr"
        if owner:
            status = ("Bu kullanıcı sitenin yöneticisi (admin). Premium erişimi VAR ve tüm analizleri sınırsız."
                      if tr else "This user is the site administrator (admin). They HAVE premium access and unlimited analyses.")
        elif premium:
            status = (("Bu kullanıcının premium erişimi VAR" + (" ve analizleri sınırsız." if unlimited else " (deneme süresinde).")) if tr
                      else ("This user HAS premium access" + (" with unlimited analyses." if unlimited else " (free trial).")))
        else:
            status = (f"Bu kullanıcı premium DEĞİL; kendi analiz kredileri var (bir analiz {cost} kredi). "
                      f"Piyasa özeti (en iyi/en mantıklı coin, en iyi long/short) ona günde en fazla {limit} kez, her seferinde {cost} kredi düşerek verilir; "
                      "get_market_overview sonucundaki free_usage/reason ne diyorsa onu söyle." if tr else
                      f"This user is NOT premium; they have their own analysis credits (one analysis costs {cost} credits). "
                      f"The market overview (best coin, best long/short) is available to them at most {limit} times a day, {cost} credits each; "
                      "report exactly what free_usage/reason in the get_market_overview result says.")
        rules = ("Premium veya yetki eksikliğinden YALNIZ bir araç sonucu açıkça PREMIUM_REQUIRED, FREE_DAILY_LIMIT veya CREDITS_EXHAUSTED dediğinde bahset. "
                 "Bir araç hata verirse veya veri gelmezse bunu yetki sorunu sanma; kısaca 'şu an doğrulayamadım' de, hata uydurma."
                 if tr else
                 "Mention missing premium or rights ONLY when a tool result explicitly says PREMIUM_REQUIRED, FREE_DAILY_LIMIT or CREDITS_EXHAUSTED. "
                 "If a tool fails or returns no data, do not treat it as a permissions problem; briefly say you could not verify it right now and never invent an error.")
        header = "SUNUCU DOĞRULAMALI HESAP DURUMU (güvenilir sistem bilgisi):" if tr else "SERVER-VERIFIED ACCOUNT STATUS (trusted system information):"
        trial_note = (" Ücretsiz deneme üyesi." if tr else " Free-trial member.") if trial and not owner else ""
        return f"{header} {status}{trial_note} {rules}"

    async def get_my_activity(self, user_id: str, transaction: CreditTransaction) -> dict[str, Any]:
        # Internal check-in read only; not exposed as an LLM tool.
        self.user(user_id)
        row = await transaction.row("SELECT last_seen_at FROM assistant_proactive_state WHERE user_id=$1", user_id)
        seen = row["last_seen_at"] if row else None
        elapsed = self.clock() - seen if seen is not None else None
        valid = elapsed is not None and math.isfinite(elapsed) and elapsed >= 0
        return self.result({"inactive_seconds": math.floor(elapsed) if valid else None}, stale=not valid)

    async def get_protection_status(self, user_id: str, symbol: str, direction: str | None = None, language: Language = "tr") -> dict[str, Any]:
        state = await self.account_state(user_id)
        age, stale = self.account_age(state)
        reason = "ACCOUNT_NOT_VERIFIED"
        verified, protected = False, None
        protection_state = "UNKNOWN"
        if state is not None:
            snapshot = state["snapshot"]
            positions = [
                row for row in snapshot.get("positions", [])
                if row.get("symbol") == symbol and (direction is None or row.get("direction") == direction)
            ]
            plans = [
                row for row in (state.get("plans") or {}).values()
                if len(positions) == 1 and row.get("symbol") == symbol and row.get("direction") == positions[0].get("direction")
                and row.get("status") not in {"KAPANDI", "İPTAL"}
            ]
            if stale:
                reason = "STALE_SNAPSHOT"
            elif not state.get("connected") or not state.get("recovery_ready") or state.get("recovery_error") or state.get("reconciliation_required") or state["connection"].get("last_error"):
                reason = "RECONCILIATION_UNVERIFIED"
            elif snapshot.get("open_algo_orders_available") is not True or not isinstance(snapshot.get("open_algo_orders"), list) or not all(isinstance(row, dict) for row in snapshot["open_algo_orders"]):
                reason = "PROTECTION_SNAPSHOT_UNAVAILABLE"
            elif len(positions) != 1 or len(plans) != 1:
                reason = "POSITION_OR_PLAN_AMBIGUOUS"
            else:
                plan = plans[0]
                classified, _, match_reason = v25_execution.classify_plan_protection(plan, snapshot.get("open_algo_orders", []))
                protection_state = plan.get("protection_state", "UNKNOWN")
                verified = classified == protection_state and classified in {"MATCHED", "MISSING"}
                if classified == "MATCHED":
                    verified = verified and plan.get("protection_match_confidence") == "EXACT" and match_reason == "EXACT_IDENTITY"
                reason = match_reason if verified else "PROTECTION_NOT_VERIFIED"
                protected = classified == "MATCHED" if verified else None
        if not verified:
            protection_state = "UNKNOWN"
        age_text = (
            f"Veri {age} saniye önce alındı." if language == "tr" else f"Data was fetched {age} seconds ago."
        ) if age is not None else ("Veri yaşı doğrulanamadı." if language == "tr" else "Data age could not be verified.")
        status_text = (
            ("Koruma doğrulandı." if protected else "Doğrulanmış stop bulunmuyor.") if language == "tr"
            else ("Protection verified." if protected else "No verified stop was found.")
        ) if verified else ("Koruma doğrulanamadı." if language == "tr" else "Protection could not be verified.")
        data = {"symbol": symbol, "verified": verified, "protected": protected, "protection_state": protection_state,
                "verification_reason": reason, "data_age_seconds": age, "message": f"{status_text} {age_text}"}
        if not self.premium(user_id):
            data = public_projection(data)
        return self.result(data, stale=stale)

    # ---- Kendi hesabını okuma (salt okunur, yalnız canlı oturum sahibine; anahtar ve başka üye verisi asla dönmez) ----
    async def account_access(self, user_id: str) -> bool:
        """Profil izni. Kayıt yoksa varsayılan AÇIK; izin okunamazsa güvenli tarafta kalınır (kapalı sayılır)."""
        self.user(user_id)
        from .assistant_storage import AssistantStore
        service = getattr(self.request.app.state, "assistant_service", None)
        store = getattr(service, "store", None) or AssistantStore(self.request.app)
        try:
            async with store.connection(self.config.request_timeout_seconds) as transaction:
                row = await transaction.row("SELECT account_access FROM assistant_proactive_state WHERE user_id=$1", user_id)
        except Exception:  # noqa: BLE001 - izin doğrulanamazsa hesap verisi verilmez
            return False
        return True if row is None or row["account_access"] is None else bool(row["account_access"])

    async def live_read(self, user_id: str, reader: Callable[[dict[str, Any]], Any]) -> tuple[Any, str | None]:
        member_id = str(self.user(user_id)["id"])
        state = getattr(self.request.app.state, "v25_execution", None)
        if not isinstance(state, dict) or not isinstance(state.get("lock"), asyncio.Lock):
            return None, "LIVE_NOT_VERIFIED"
        lock = state["lock"]
        await asyncio.wait_for(lock.acquire(), timeout=self.config.request_timeout_seconds)
        try:
            if (state.get("live_session_authorization") or {}).get("user_id") != member_id:
                return None, "LIVE_NOT_OWNER"
            return copy.deepcopy(reader(state)), None
        finally:
            lock.release()

    def own(self, user_id: str, data: dict[str, Any], *, stale: bool = False) -> dict[str, Any]:
        return self.result(data if self.premium(user_id) else public_projection(data), stale=stale)

    @staticmethod
    def number(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    async def get_my_account_summary(self, user_id: str) -> dict[str, Any]:
        """Bakiye, kullanılabilir bakiye, açık kâr/zarar ve günlük kayıp sınırı kullanımı."""
        if not await self.account_access(user_id):
            return self.result({"reason": ACCOUNT_ACCESS_OFF}, stale=True)
        state = await self.account_state(user_id)
        if state is None:
            return self.result({"reason": "ACCOUNT_NOT_VERIFIED"}, stale=True)
        age, stale = self.account_age(state)
        snapshot = state["snapshot"]
        from . import original_v2_live
        config, _ = await self.live_read(user_id, original_v2_live.public_config)
        daily = (config or {}).get("daily") if isinstance((config or {}).get("daily"), dict) else {}
        loss, limit = self.number(daily.get("loss")), self.number(daily.get("limit"))
        return self.own(user_id, {
            "wallet_balance": self.number(snapshot.get("wallet_balance")),
            "available_balance": self.number(snapshot.get("available_balance")),
            "unrealized_pnl": self.number(snapshot.get("unrealized_pnl")),
            "open_positions": sum(1 for row in snapshot.get("positions", []) if self.number(row.get("quantity"))),
            "daily_loss": {"loss": loss, "limit": limit, "locked": bool(daily.get("locked")),
                           "remaining": round(max(0.0, limit - loss), 4) if loss is not None and limit is not None else None},
            "data_age_seconds": age,
        }, stale=stale)

    async def get_my_settings(self, user_id: str) -> dict[str, Any]:
        """Kayıtlı risk, günlük sınır, teminat modu, akıllı koruma ayarları ve oturum."""
        if not await self.account_access(user_id):
            return self.result({"reason": ACCOUNT_ACCESS_OFF}, stale=True)
        from . import original_v2_live
        config, reason = await self.live_read(user_id, original_v2_live.public_config)
        if reason:
            return self.result({"reason": reason}, stale=True)
        mode = config.get("margin_mode")
        row = next((item for item in config.get("margin_modes") or [] if isinstance(item, dict) and item.get("mode") == mode), {})
        smart = (config.get("smart_profit") or {}).get("settings") or {}
        session = config.get("session") or {}
        return self.result({
            "running": bool(config.get("enabled")), "paused": bool(config.get("paused")),
            "risk_percent": config.get("risk_percent"), "daily_loss_percent": config.get("daily_loss_percent"),
            "margin_mode": {"mode": mode, "label": row.get("label"), "per_trade_percent": row.get("per_trade_percent")},
            "smart_protection": {key: smart.get(key) for key in (
                "enabled", "emergency_off", "threshold", "wait_minutes", "protect_stop_enabled", "smart_exit_enabled", "direction_mismatch_action")},
            "session": {"hours": session.get("hours"), "remaining_seconds": session.get("remaining_seconds")},
            "coins": len(config.get("symbols") or []),
        })

    async def get_my_trades(self, user_id: str, days: int = 7) -> dict[str, Any]:
        """Son N günde kapanan ve kâr/zararı Binance'te doğrulanmış işlemler ve özet."""
        if not await self.account_access(user_id):
            return self.result({"reason": ACCOUNT_ACCESS_OFF}, stale=True)
        state = await self.account_state(user_id)
        if state is None:
            return self.result({"reason": "ACCOUNT_NOT_VERIFIED"}, stale=True)
        since = self.clock() - days * 86400
        rows = []
        for row in v25_execution.verified_live_journal({"plans": state.get("plans") or {}}):
            closed = parse_datetime(row.get("created_at")) if row.get("created_at") else None
            if closed is None or closed.timestamp() < since:
                continue
            rows.append({"symbol": row.get("symbol"), "direction": row.get("direction"),
                         "opened_at": row.get("opened_at"), "closed_at": row.get("created_at"),
                         "close_reason": row.get("reason"), "realized_pnl": self.number(row.get("realized_pnl")),
                         "r_multiple": self.number(row.get("r_multiple")),
                         "entry_price": row.get("planned_entry_price"), "exit_price": self.number(row.get("price"))})
        rows.sort(key=lambda item: str(item["closed_at"]), reverse=True)
        pnls = [item["realized_pnl"] for item in rows if item["realized_pnl"] is not None]
        wins = sum(1 for value in pnls if value > 0)
        return self.own(user_id, {
            "days": days, "count": len(rows), "wins": wins, "losses": sum(1 for value in pnls if value < 0),
            "win_rate_percent": round(wins / len(pnls) * 100, 1) if pnls else None,
            "total_realized_pnl": round(sum(pnls), 4) if pnls else 0.0,
            "trades": rows[:MAX_TRADE_ROWS], "note": "Yalnız kâr/zararı doğrulanmış kapanışlar; funding hariç.",
        })

    async def get_my_events(self, user_id: str, limit: int = 15) -> dict[str, Any]:
        """Canlı işlem olay kaydının son satırları (emir, stop, kapanış, uyarı)."""
        if not await self.account_access(user_id):
            return self.result({"reason": ACCOUNT_ACCESS_OFF}, stale=True)
        member_id = str(self.user(user_id)["id"])
        events, reason = await self.live_read(user_id, lambda state: [row for row in (state.get("events") or [])[:200] if isinstance(row, dict)])
        if reason:
            return self.result({"reason": reason}, stale=True)
        rows = [{"created_at": row.get("created_at"), "kind": row.get("kind"), "symbol": row.get("symbol"),
                 "message": str(row.get("message") or "")[:200]}
                for row in events if row.get("recipient_user_id") in (None, member_id)][:limit]
        return self.result({"events": rows})

    async def get_my_position_details(self, user_id: str) -> dict[str, Any]:
        """Açık pozisyonların giriş, anlık fiyat, stop, TP seviyeleri ve akıllı koruma durumu (kendi hesabı)."""
        if not await self.account_access(user_id):
            return self.result({"reason": ACCOUNT_ACCESS_OFF}, stale=True)
        state = await self.account_state(user_id)
        if state is None:
            return self.result({"positions": [], "reason": "ACCOUNT_NOT_VERIFIED"}, stale=True)
        age, stale = self.account_age(state)
        stale = stale or not state.get("connected") or bool(state["connection"].get("last_error")) or not state.get("recovery_ready")
        from . import original_v2_live
        smart, _ = await self.live_read(user_id, original_v2_live.smart_profit_public)
        guards = {row.get("plan_id"): row for row in (smart or {}).get("positions") or [] if isinstance(row, dict)}
        open_plans = [plan for plan in (state.get("plans") or {}).values()
                      if str(plan.get("status") or "").upper() not in {"KAPANDI", "İPTAL", "IPTAL"}]
        positions = []
        for row in state["snapshot"].get("positions", []):
            if not self.number(row.get("quantity")):
                continue
            matches = [plan for plan in open_plans if plan.get("symbol") == row.get("symbol") and plan.get("direction") == row.get("direction")]
            plan = matches[0] if len(matches) == 1 else None
            targets = [self.number(item.get("price") if isinstance(item, dict) else item) for item in (plan or {}).get("targets") or []][:3]
            guard = guards.get((plan or {}).get("id")) or {}
            positions.append({
                "symbol": row.get("symbol"), "direction": row.get("direction"), "quantity": self.number(row.get("quantity")),
                "entry_price": self.number(row.get("entry_price")), "mark_price": self.number(row.get("mark_price")),
                "unrealized_pnl": self.number(row.get("unrealized_pnl")),
                "managed_by_bot": plan is not None,
                "stop_loss": self.number((plan or {}).get("stop_loss")), "targets": targets,
                "tp1_done": bool((plan or {}).get("original_tp1_complete") or (plan or {}).get("tp1_fill_confirmed")),
                "breakeven_at": (plan or {}).get("breakeven_at"), "opened_at": (plan or {}).get("opened_at"),
                "smart_protection": {key: guard.get(key) for key in ("mode", "last_score", "timer_end", "temp_stop_price")} if guard else None,
            })
        return self.own(user_id, {"positions": positions, "data_age_seconds": age}, stale=stale)

    async def dispatch(self, user_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.user(user_id)
        model = ARGUMENT_MODELS.get(name)
        if model is None:
            raise HTTPException(404, "Unknown assistant read tool")
        parsed = model.model_validate(arguments)
        return await getattr(self, name)(user_id, **parsed.model_dump())
