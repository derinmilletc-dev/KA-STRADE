"""Deterministic member information answers without a model or message quota."""
from __future__ import annotations

import re
import logging
import sqlite3
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import asyncpg
from fastapi import HTTPException

from .assistant_prompt import IDENTITY_REPLIES
from .assistant_storage import AssistantStorageError
from .assistant_llm import summary
from .assistant_tools import AssistantTools
from .assistant_response import safe_response

Language = Literal["tr", "en"]
logger = logging.getLogger(__name__)


def words_of(message: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", message.casefold().replace("ı", "i"))
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    return set(re.findall(r"\w+", normalized))


def analysis_targets(message: str) -> tuple[list[str], str]:
    words = words_of(message)
    aliases = {"bitcoin": "BTC", "btc": "BTC", "eth": "ETH", "ethereum": "ETH", "sol": "SOL",
               "bnb": "BNB", "xrp": "XRP", "ada": "ADA", "doge": "DOGE", "avax": "AVAX", "dot": "DOT", "link": "LINK"}
    symbols = sorted({(aliases[word] + "USDT") if word in aliases else word.upper()
                      for word in words if word in aliases or re.fullmatch(r"[a-z0-9]{2,20}usdt", word)})
    intervals = sorted(words & {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"})
    return symbols, intervals[0] if len(intervals) == 1 else "15m" if not intervals else ""


def detect_language(message: str) -> Language:
    words = words_of(message)
    turkish = {"merhaba", "selam", "nasil", "nasıl", "nedir", "neden", "ben", "bana", "icin", "kredi", "kredim", "krediler", "kredilerim", "hesap", "yardim", "planim", "aboneligim", "benim", "fiyat", "ucret", "deneme", "abonelik", "kalan", "ne",
               "sistem", "yaz", "göster", "goster", "kaç", "kac", "var", "mı", "mi", "sen", "kimsin"}
    return "tr" if re.search("[çğıöşüÇĞİÖŞÜ]", message) or words & turkish or any(
        word.startswith(("anahtar", "uyelik", "gunluk", "kullanim", "mesaj", "dakikalik", "analiz", "durum", "sonuc"))
        for word in words
    ) else "en"


SUPPORT_PROBLEM = re.compile(
    r"\b(yukselmedi|yukseltilmedi|yenilenmedi|aktif olmadi|aktiflesmedi|aktif degil|acilmadi|gecmedi|tanimlanmadi|yansimadi"
    r"|odeme yaptim|odedim|parami|para cekildi|cekildi ama|kesildi|iade|geri odeme|aldim ama|satin aldim ama"
    r"|giris yapamiyorum|hesabima giremiyorum|hesabim kilitlendi|sifremi unuttum ve|e ?posta gelmedi|mail gelmedi"
    r"|canli destek|destek ekib|destege|destekle iletisim|musteri hizmet"
    r"|did ?n ?t (renew|upgrade|activate)|not (activated|upgraded|renewed)|was charged|refund|payment (failed|issue|problem)"
    r"|can ?t (log ?in|sign ?in|access my account)|contact support|customer support|support team)\b"
)
SUPPORT_REPLIES = {
    "tr": "Bunu destek ekibimiz kontrol edip çözebilir. Ana sayfadaki DESTEK bölümünden (telefonda Menü → Destek) talebini gönderebilirsin; "
          "varsa ödeme bilgisini ve ekran görüntüsünü de ekle. Talep yoğunluğuna bağlı olarak genellikle birkaç saat içinde cevabın "
          "Destek ekranında görünür; bildirimleri açarsan cevap gelince haberdar olursun.",
    "en": "Our support team can check and fix this. Send a request from SUPPORT on the home page (mobile: Menu → Support); "
          "include any payment details and a screenshot if you have them. Depending on request volume, your answer usually "
          "appears on the Support screen within a few hours; turn on notifications to be told when it arrives.",
}
STATUS_TR = {"ACTIVE": "aktif", "EXPIRED": "süresi dolmuş", "TRIALING": "deneme sürecinde", "FREE_TRIAL": "deneme sürecinde",
             "CANCELED": "iptal edilmiş", "CANCELLED": "iptal edilmiş", "PAST_DUE": "ödemesi gecikmiş", "NONE": "yok", "FREE": "ücretsiz"}


# Hızlı yol YALNIZCA salt-okunur veri listesi isteklerini yakalar. Eylem (kapat/sat/iptal...), eğitim (nasıl/neden/açıkla/strateji...)
# ve evet-hayır soruları (… mu?) modele bırakılır; belirsizlikte varsayılan "hızlı yol yok"tur.
ACTION_STEMS = ("kapat", "kapa", "kapan", "sat", "iptal", "sil", "bosalt", "tasfiye", "azalt", "artir", "duzenle", "degistir", "acil",
                "close", "sell", "cancel", "liquidat", "flatten", "reduc", "increas", "modify", "delete", "remove")
ACTION_WORDS = {"ac", "acin", "acar", "acsin", "al", "alsana", "buy", "exit", "execute"}
EDUCATIONAL_STEMS = ("nasil", "neden", "niye", "nedir", "nelerdir", "acikla", "anlat", "ogret", "strateji", "yontem", "mantik", "ornek",
                     "how", "why", "explain", "teach", "strateg", "method", "example", "tutorial", "guide", "meaning", "mean", "learn")
YES_NO_PARTICLES = {"mi", "mu"}
EN_QUESTION_OPENERS = {"is", "are", "does", "do", "should", "can", "will", "would", "could"}
MARKET_ASK = re.compile(
    r"\b(en cok (yukselen|artan|dusen|kaybeden)|en iyi (long|short)|en yuksek guven|guveni en yuksek|guven skoru en yuksek|en iyi firsat"
    r"|top (gainer|loser)s?|best (long|short)|highest confidence)\b")


POSITION_READ_STEMS = ("durum", "goster", "listele")
POSITION_READ_WORDS = {"var", "kac", "neler", "hangileri", "show", "list", "what", "whats", "any", "many", "much", "which"}


def is_read_request(words: set[str]) -> bool:
    return bool(words & POSITION_READ_WORDS or any(word.startswith(POSITION_READ_STEMS) for word in words))


def is_action_or_educational(words: set[str]) -> bool:
    if words & {"many", "much"}:  # "how many / how much" bir sayım sorusudur, nasıl-yapılır sorusu değil
        words = words - {"how"}
    return bool(words & ACTION_WORDS
                or any(word.startswith(ACTION_STEMS) for word in words) or any(word.startswith(EDUCATIONAL_STEMS) for word in words))


# Önceki konuşmaya bağlı kısa takip soruları ("peki ya ETH?", "ya 4 saatlik?", "what about that?") bağlamsız hızlı yola düşmesin.
FOLLOWUP_OPENERS = {"peki", "ya", "yani", "ama", "and", "but", "so", "then", "same", "aynisi"}
FOLLOWUP_PRONOUNS = {"o", "bu", "bunu", "bunun", "sunu", "sunun", "onun", "onu", "digeri", "baska", "it", "that", "this", "those", "other", "another"}
FOLLOWUP_MAX_WORDS = 8
FOLLOWUP_PRONOUN_MAX_WORDS = 4


def is_followup(message: str) -> bool:
    ordered = re.findall(r"\w+", unicodedata.normalize("NFKD", message.casefold().replace("ı", "i")).replace("\u0307", ""))
    ordered = ["".join(c for c in w if not unicodedata.combining(c)) for w in ordered]
    if not ordered or len(ordered) > FOLLOWUP_MAX_WORDS:
        return False
    if ordered[0] in FOLLOWUP_OPENERS or ordered[:2] in (["what", "about"], ["how", "about"]):
        return True
    return len(ordered) <= FOLLOWUP_PRONOUN_MAX_WORDS and bool(set(ordered) & FOLLOWUP_PRONOUNS)


def intent(message: str) -> str | None:
    words = words_of(message)
    normalized = " ".join(re.findall(r"\w+|[?!.]", unicodedata.normalize("NFKD", message.casefold().translate(str.maketrans("çğıöşü", "cgiosu"))).replace("\u0307", "")))
    # Hesap/abonelik/ödeme sorunu bildiren mesajlar destek ekibine yönlendirilir.
    if SUPPORT_PROBLEM.search(normalized):
        return "support"
    if words & {"trade", "order", "execute", "arm", "consent", "emir", "islem", "buy", "sell"}:
        return None
    if len(words) <= 12 and not is_action_or_educational(words):
        # Piyasa özeti: yalnızca sembol içermeyen, evet/hayır olmayan, açık liste isteyen sorular (yalnız okuma).
        if (MARKET_ASK.search(normalized) and not analysis_targets(message)[0] and not words & YES_NO_PARTICLES
                and not (words & EN_QUESTION_OPENERS and normalized.split()[0] in EN_QUESTION_OPENERS)):
            return "get_market_overview"
        # Demo hesap pozisyonları / kâr-zarar: kişisel bir pozisyon ifadesi VE açık bir okuma ifadesi (durum/var mı/göster/kaç,
        # show/what/how many...) birlikte olmalı. Okuma ifadesi olmayan her cümle (bitir, durdur, stop, end...) modele gider.
        about_positions = (any(word.startswith(("pozisyonlarim", "pozisyonum", "islemlerim", "islemim")) for word in words)
                           or (words & {"my", "i"} and words & {"positions", "position", "trades"})
                           or ("acik" in words and any(word.startswith(("pozisyon", "islem")) for word in words))
                           or words & {"kar", "zarar", "pnl"})
        if about_positions and is_read_request(words):
            return "get_demo_positions"
    if re.fullmatch(r"(?:sen kimsin|kimsin|adin ne|senin adin ne|who are you|what is your name)(?:\s*[?!.])*", normalized.strip()):
        return "identity"
    if any(word.startswith(("analiz", "analysis")) for word in words) and any(
        word.startswith(("durum", "sonuc", "status", "result", "available", "mevcut")) or word == "var" for word in words
    ) and analysis_targets(message)[0]:
        return "get_analysis"
    if words & {"bitcoin", "btc", "eth", "pricefeed"}:
        return None
    if (words & {"gunluk", "daily", "dakikalik"} and any(
        word.startswith(("limit", "kullanim", "mesaj", "usage", "message", "allowance")) for word in words
    )) or (any(word.startswith(("mesaj", "message")) for word in words) and
           any(word.startswith(("hak", "kalan", "remaining", "limit")) for word in words)):
        return "usage"
    if any(word == "api" or word.startswith("apikey") for word in words) and any(
        word.startswith(("anahtar", "key", "apikey", "credential")) for word in words
    ) and any(word.startswith(("gir", "ekle", "kaydet", "nasil", "nere", "how", "where", "enter", "input", "setup", "configure", "add")) for word in words):
        return "search_help"
    if not words & {"price", "cost", "fee", "much", "fiyat", "ucret"} and ("planim" in words or "aboneligim" in words
        or ("my" in words and words & {"plan", "subscription", "access", "membership"})
        or ("benim" in words and words & {"plan", "abonelik"})):
        return "get_my_access"
    if words & {"credit", "credits", "kredi", "kredim", "krediler", "kredilerim"}:
        return "get_my_credits"
    if words & {"trial", "deneme", "fiyat", "ucret", "fiyati", "plans", "pricing"}:
        return "get_plans"
    if any(word.startswith(("plan", "subscription", "abonelik", "premium", "membership", "uyelik")) for word in words) and (
        any(word.startswith(("price", "cost", "fee", "much", "fiyat", "ucret")) for word in words) or words & {"kadar", "kac", "para"}
    ):
        return "get_plans"
    if words & {"price", "cost", "much"} and words <= {"what", "is", "the", "price", "how", "much", "does", "it", "cost"}:
        return "get_plans"
    return None


def money(value: Any) -> str:
    try:
        return f"{float(value):+.2f}"
    except (TypeError, ValueError):
        return "?"


async def demo_positions_answer(tools: AssistantTools, user_id: str, language: Language) -> dict | None:
    """Binance Demo açık pozisyonları (salt okunur). Doğrulanamayan her durum açıkça söylenir; veri uydurulmaz."""
    tr = language == "tr"
    try:
        result = await tools.dispatch(user_id, "get_demo_positions", {})
    except Exception:  # noqa: BLE001 - hızlı yol hiçbir zaman hata cevabı üretmez
        return None
    data = result.get("data") or {}
    reason = data.get("reason")
    if reason == "DEMO_OWNER_ONLY":
        # Demo özeti yalnız OWNER için: diğer üyeler önceki gibi normal akışa (model + kendi get_my_positions) gider.
        return None
    if reason or result.get("stale"):
        text = ("Demo hesabındaki işlemleri şu an doğrulayamadım (bağlantı veya anahtar sorunu olabilir); açık işlem olmadığını söyleyemem."
                if tr else "I could not verify your Demo positions right now (a connection or key problem is possible), so I cannot say there are none.")
        return {"reply": text, "language": language, "sources": ["get_demo_positions"], "fetched_at": result.get("fetched_at"), "stale": True}
    positions = data.get("positions") or []
    if not positions:
        reply = ("Şu an açık Demo işleminiz yok; dolayısıyla kâr/zarar durumunuz da yok." if tr
                 else "You have no open Demo positions right now, so there is no profit or loss.")
    else:
        rows = []
        for row in positions:
            pnl = money(row.get("unrealized_pnl"))
            side = row.get("direction") or ""
            rows.append((f"{row.get('symbol')} {side}: {pnl} USDT" + (" (kâr)" if pnl.startswith("+") and pnl != "+0.00" else " (zarar)" if pnl.startswith("-") else "")) if tr
                        else f"{row.get('symbol')} {side}: {pnl} USDT")
        reply = ("Açık Demo işlemleriniz: " if tr else "Your open Demo positions: ") + "; ".join(rows) + "."
    return {"reply": reply, "language": language, "sources": ["get_demo_positions"],
            "fetched_at": result.get("fetched_at"), "stale": False}


def market_focus(message: str) -> set[str]:
    text = " ".join(re.findall(r"\w+", unicodedata.normalize("NFKD", message.casefold().translate(str.maketrans("çğıöşü", "cgiosu"))).replace("\u0307", "")))
    focus = set()
    if re.search(r"yuksel|artan|gainer", text): focus.add("gainer")
    if re.search(r"dusen|kaybeden|loser", text): focus.add("loser")
    if re.search(r"\blong\b", text): focus.add("long")
    if re.search(r"\bshort\b", text): focus.add("short")
    if re.search(r"guven|confidence", text): focus.add("confidence")
    if re.search(r"firsat|opportunity", text): focus.add("opportunity")
    return focus


async def market_overview_answer(tools: AssistantTools, user_id: str, language: Language, message: str = "") -> dict | None:
    """Tarayıcı adayları + 24s hareketliler. Eski/yok/erişilemeyen veri açıkça söylenir; sıralama ASLA uydurulmaz."""
    tr = language == "tr"
    try:
        result = await tools.dispatch(user_id, "get_market_overview", {})
    except Exception:  # noqa: BLE001
        return None
    data = result.get("data") or {}
    base = {"language": language, "sources": ["get_market_overview"], "fetched_at": result.get("fetched_at")}
    if data.get("reason") == "PREMIUM_REQUIRED":
        return {**base, "stale": False, "reply": ("Piyasa özeti Master Mode (premium) üyelik gerektirir." if tr
                                                  else "The market overview requires a Master Mode (premium) membership.")}
    scan, movers = data.get("scan") or {}, data.get("movers") or {}
    focus = market_focus(message) or {"gainer", "long", "short", "confidence"}
    parts: list[str] = []
    unverified = False
    if focus & {"gainer", "loser"}:
        if movers.get("status") != "fresh":
            unverified = True
            parts.append("24 saatlik piyasa verisini şu an alamadım." if tr else "I could not fetch the 24h market data right now.")
        else:
            if "gainer" in focus:
                rows = movers.get("top_gainers") or []
                parts.append((f"24 saatte en çok yükselen: {rows[0]['symbol']} %{rows[0]['change_24h_percent']}" if tr else f"Top 24h gainer: {rows[0]['symbol']} {rows[0]['change_24h_percent']}%")
                             if rows else ("Şu an 24 saatte yükselişte olan coin yok (değişimi pozitif olan yok)." if tr else "No coin is up over 24h right now (none has a positive change)."))
            if "loser" in focus:
                rows = movers.get("top_losers") or []
                parts.append((f"24 saatte en çok düşen: {rows[0]['symbol']} %{rows[0]['change_24h_percent']}" if tr else f"Top 24h loser: {rows[0]['symbol']} {rows[0]['change_24h_percent']}%")
                             if rows else ("Şu an 24 saatte düşüşte olan coin yok." if tr else "No coin is down over 24h right now."))
    wanted = [(k, tr_l, en_l) for k, tr_l, en_l in (("long", "En iyi long adayı", "Best long candidate"), ("short", "En iyi short adayı", "Best short candidate"),
                                                    ("confidence", "Güveni en yüksek", "Highest confidence"), ("opportunity", "En yüksek fırsat", "Highest opportunity"))
              if k in focus]
    if wanted:
        status = scan.get("status")
        if status == "fresh":
            keys = {"long": "best_long", "short": "best_short", "confidence": "highest_confidence", "opportunity": "highest_opportunity"}
            for key, label_tr, label_en in wanted:
                row = scan.get(keys[key])
                if row:
                    detail = (f"(fırsat {row.get('opportunity_score')}, güven {row.get('confidence_label')})" if tr
                              else f"(opportunity {row.get('opportunity_score')}, confidence {row.get('confidence_label')})")
                    parts.append(f"{label_tr if tr else label_en}: {row.get('symbol')} {detail}")
                elif key in ("long", "short"):
                    parts.append(f"Şu an kalite kurallarını geçen {key} adayı yok." if tr else f"No {key} candidate currently passes the quality rules.")
                else:
                    parts.append("Şu an kalite kurallarını geçen aday yok." if tr else "No candidate currently passes the quality rules.")
            parts.append(f"Tarama zamanı: {scan.get('last_scan_at')}" if tr else f"Scan time: {scan.get('last_scan_at')}")
        else:
            unverified = True
            if status == "stale":
                minutes = int((scan.get("age_seconds") or 0) // 60)
                parts.append(f"Son tarama {minutes} dk önceydi ve güncel sayılmıyor (en fazla {int(scan.get('max_age_seconds') or 0) // 60} dk); bu yüzden sıralama vermiyorum. Analyst sayfasından yeni tarama başlatabilirsin."
                             if tr else f"The last scan is {minutes} min old and no longer counts as current (max {int(scan.get('max_age_seconds') or 0) // 60} min), so I will not give a ranking. Start a new scan from the Analyst page.")
            elif status == "no_scan":
                parts.append("Henüz tarama sonucu yok; sıralama veremem. Analyst sayfasından tarama başlatabilirsin." if tr
                             else "There are no scan results yet, so I cannot rank anything. You can start a scan from the Analyst page.")
            else:
                parts.append("Tarama verisine şu an erişemedim; sıralama veremem." if tr else "I could not access the scan data right now, so I cannot rank anything.")
    suffix = " Bunlar analiz çıktısıdır, al/sat talimatı değildir." if tr else " These are analysis outputs, not trading instructions."
    return {**base, "stale": unverified, "reply": ". ".join(parts) + ("" if parts[-1].endswith(".") else ".") + suffix}


async def answer(
    tools: AssistantTools, user_id: str, message: str, language: Language,
    usage_reader: Callable[[], Awaitable[dict[str, Any]]] | None = None, has_history: bool = False,
) -> dict | None:
    name = intent(message)
    if name is None:
        return None
    # Destek yönlendirmesi dışında, önceki konuşmaya bağlı kısa takip soruları bağlamı gören modele gider.
    if has_history and name != "support" and is_followup(message):
        return None
    if name == "identity":
        return {"reply": IDENTITY_REPLIES[language], "language": language, "sources": []}
    if name == "support":
        return {"reply": SUPPORT_REPLIES[language], "language": language, "sources": []}
    if name == "usage":
        try:
            if usage_reader is None:
                raise AssistantStorageError("Usage reader unavailable")
            data = await usage_reader()
        except (AssistantStorageError, asyncpg.PostgresError, asyncpg.InterfaceError, sqlite3.Error, OSError, TimeoutError):
            logger.warning("assistant_fastpath_usage_unavailable user_id=%s", user_id)
            return {"reply": "Kullanım bilgini şu an alamadım; ekrandaki günlük mesaj hakkı sayacını kontrol et." if language == "tr" else
                    "I could not fetch your usage right now; check the daily message counter on screen.",
                    "language": language, "sources": []}
        used = data["total"] - data["remaining"]
        return {"reply": (f"Bugün kullanılan: {used}. Kalan: {data['remaining']}. Günlük limit: {data['total']} mesaj. "
                          f"Dakikalık limit: {tools.config.per_minute_limit} mesaj.") if language == "tr" else
                         (f"Used today: {used}. Remaining: {data['remaining']}. Daily limit: {data['total']} messages. "
                          f"Per-minute limit: {tools.config.per_minute_limit} messages."),
                "language": language, "sources": ["usage"]}
    if name == "search_help":
        result = await tools.dispatch(user_id, name, {"query": "API connection demo save verify" if language == "en" else
                                                    "API bağlantı Demo kaydet doğrula", "language": language})
        article = next((row for row in result["data"]["results"] if row["id"] == "api-connection"), None)
        if article is None:
            raise HTTPException(503, "API connection help unavailable")
        return {"reply": article["content"], "language": language, "sources": [name],
                "fetched_at": result["fetched_at"], "stale": result["stale"]}
    if name == "get_analysis":
        symbols, timeframe = analysis_targets(message)
        if len(symbols) != 1 or not timeframe:
            return {"reply": "Tek bir sembol ve zaman dilimi belirt." if language == "tr" else
                    "Please specify one symbol and timeframe.", "language": language, "sources": []}
        try:
            result = await tools.dispatch(user_id, name, {"symbol": symbols[0], "timeframe": timeframe})
        except (HTTPException, asyncpg.PostgresError, asyncpg.InterfaceError, sqlite3.Error, OSError, TimeoutError):
            logger.warning("assistant_fastpath_analysis_unavailable user_id=%s", user_id)
            result = None
        if result is None or "needs_confirmation" in result["data"] or "direction" not in result["data"]:
            return {"reply": "Analiz bulunamadı, istersen yeni analiz isteyebilirsin." if language == "tr" else
                    "No analysis was found; you can request a new analysis if you wish.",
                    "language": language, "sources": [name]}
        reply = safe_response(summary(name, result, language), language, free=not tools.premium(user_id))
        if result["stale"]:
            reply = ("Bayat/doğrulanmamış veri. " if language == "tr" else "Stale/unverified data. ") + reply
        return {"reply": reply, "language": language, "sources": [name],
                "fetched_at": result["fetched_at"], "stale": result["stale"]}
    if name == "get_demo_positions":
        return await demo_positions_answer(tools, user_id, language)
    if name == "get_market_overview":
        return await market_overview_answer(tools, user_id, language, message)
    result = await tools.dispatch(user_id, name, {})
    data = result["data"]
    if name == "get_plans":
        plan = next(row for row in data["plans"] if row["id"] == "MASTER_MODE")
        price = f"{plan['monthly_price']:.2f}"
        if language == "tr":
            reply = f"{plan['name']}: {price.replace('.', ',')} {plan['currency']}/ay. Deneme süresi {data['trial_days']} gün."
        else:
            reply = f"{plan['name']}: {price} {plan['currency']}/month. Trial: {data['trial_days']} days."
        cancellation = data["cancellation"]
        if cancellation["cancel_anytime"]:
            reply += " İstediğin zaman iptal edebilirsin." if language == "tr" else " You can cancel anytime."
        if cancellation["default_effective"] == "period_end":
            reply += " Varsayılan iptal dönem sonunda gerçekleşir." if language == "tr" else " Cancellation defaults to the end of the billing period."
        if cancellation["immediate_cancellation_supported"]:
            reply += " Anında iptal seçeneği de vardır." if language == "tr" else " Immediate cancellation is also supported."
    elif name == "get_my_access":
        plan = data["plan"] or ("Ücretsiz" if language == "tr" else "Free")
        premium = ("evet" if data["isPremium"] else "hayır") if language == "tr" else ("yes" if data["isPremium"] else "no")
        status_tr = STATUS_TR.get(str(data["status"]).upper(), str(data["status"]).lower())
        reply = (f"Planın: {plan}, durumu: {status_tr}. Premium erişimin {'var' if data['isPremium'] else 'yok'}." if language == "tr"
                 else f"Your plan: {plan}. Status: {data['status']}. Premium: {premium}.")
    elif data["unlimited"]:
        reply = "Premium üyeliğinde analiz sınırsızdır; kredi düşmez." if language == "tr" else "Premium analysis is unlimited; no credits are spent."
    else:
        reply = (
            f"Kalan kredi: {data['remaining']}/{data['total']}. Taze analiz {data['analysis_cost']} kredi. "
            f"Pencere ilk harcamada başlar ve {data['window_hours']} saat sürer. "
            f"Aynı analiz {data['cache_minutes']} dakika içinde cache'ten ücretsizdir; hata durumunda kredi bir kez iade edilir."
        ) if language == "tr" else (
            f"Credits remaining: {data['remaining']}/{data['total']}. Fresh analysis costs {data['analysis_cost']} credits. "
            f"The window starts on the first spend and lasts {data['window_hours']} hours. "
            f"The same cached analysis is free within {data['cache_minutes']} minutes; failed analysis is refunded once."
        )
        if data["resets_in_seconds"] is not None:
            remaining = data["resets_in_seconds"]
            reply += (f" Yenilenmeye {remaining} saniye kaldı." if language == "tr"
                      else f" Resets in {remaining} seconds.")
    return {"reply": reply, "language": language, "sources": [name],
            "fetched_at": result["fetched_at"], "stale": result["stale"]}
