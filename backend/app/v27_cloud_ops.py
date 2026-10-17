"""V27 cloud operations and durable Testnet evidence ledger.

The V26 Testnet engine intentionally keeps exchange credentials outside the
application state.  V27 persists only non-secret Testnet plans, decisions and
evidence to PostgreSQL so a Render restart cannot erase the operating record.
No endpoint in this module can create an exchange order.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .binance_demo import credentials_configured
from .binance_demo import state_for as demo_state_for
from .v21_demo import certificate_payload
from .v21_demo import state_for as v21_state_for
from .v27_cloud_core import VERSION, durable_payload, evidence_rows, json_safe, now_iso, restore_payload


router = APIRouter(prefix="/api/v27", tags=["V27 Cloud Operations"])
logger = logging.getLogger(__name__)
STATE_KEY = "testnet-primary"
SYNC_SECONDS = 20
DEPLOYMENT_TIER = os.getenv("PROTREBOT_DEPLOYMENT_TIER", "LOCAL").strip().upper() or "LOCAL"


async def ensure_schema(pool: Any) -> None:
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS protrebot_cloud_state (
          state_key TEXT PRIMARY KEY,
          version TEXT NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          payload JSONB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS protrebot_cloud_evidence (
          event_key TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          symbol TEXT,
          event_time TIMESTAMPTZ NOT NULL,
          payload JSONB NOT NULL,
          stored_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_protrebot_cloud_evidence_time
          ON protrebot_cloud_evidence (event_time DESC);
        """
    )


async def restore_cloud_state(application: Any) -> bool:
    pool = application.state.db_pool
    if pool is None:
        return False
    await ensure_schema(pool)
    row = await pool.fetchrow(
        "SELECT payload FROM protrebot_cloud_state WHERE state_key = $1",
        STATE_KEY,
    )
    if row is None:
        return False
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return restore_payload(application, payload)


async def sync_cloud_state(application: Any) -> dict[str, Any]:
    state = application.state.v27_cloud
    pool = application.state.db_pool
    if pool is None:
        state.update({"status": "VERİTABANI BEKLİYOR", "persistent": False, "last_error": "PostgreSQL bağlantısı hazır değil."})
        return state
    try:
        await ensure_schema(pool)
        payload = durable_payload(application)
        await pool.execute(
            """
            INSERT INTO protrebot_cloud_state (state_key, version, updated_at, payload)
            VALUES ($1, $2, NOW(), $3::jsonb)
            ON CONFLICT (state_key) DO UPDATE
              SET version = EXCLUDED.version, updated_at = NOW(), payload = EXCLUDED.payload
            """,
            STATE_KEY,
            VERSION,
            json.dumps(payload, ensure_ascii=False),
        )
        rows = evidence_rows(payload)
        if rows:
            postgres_rows = []
            for row in rows:
                event_time = row[3]
                if isinstance(event_time, datetime):
                    parsed_event_time = event_time
                elif isinstance(event_time, date):
                    parsed_event_time = datetime.combine(event_time, datetime.min.time(), tzinfo=timezone.utc)
                elif isinstance(event_time, str):
                    parsed_event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                else:
                    raise TypeError("event_time must be a date, datetime, or ISO-8601 string")
                if parsed_event_time.tzinfo is None:
                    raise ValueError("event_time must include timezone information")
                postgres_rows.append((*row[:3], parsed_event_time, row[4]))
            await pool.executemany(
                """
                INSERT INTO protrebot_cloud_evidence (event_key, kind, symbol, event_time, payload)
                VALUES ($1, $2, $3, $4::timestamptz, $5::jsonb)
                ON CONFLICT (event_key) DO NOTHING
                """,
                postgres_rows,
            )
        count = int(await pool.fetchval("SELECT COUNT(*) FROM protrebot_cloud_evidence") or 0)
        state.update({
            "status": "KALICI",
            "persistent": True,
            "last_sync": now_iso(),
            "last_error": None,
            "evidence_count": count,
        })
    except Exception as exc:
        logger.exception("v27 cloud evidence sync failed")
        state.update({"status": "YENİDEN DENİYOR", "persistent": False, "last_error": str(exc)[:240]})
    return state


async def cloud_loop(application: Any) -> None:
    while True:
        try:
            state = application.state.v27_cloud
            if not state.get("restore_attempted") and application.state.db_pool is not None:
                restored = await restore_cloud_state(application)
                state.update({"restore_attempted": True, "restored": restored})
            await sync_cloud_state(application)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            application.state.v27_cloud.update({"status": "YENİDEN DENİYOR", "last_error": str(exc)[:240]})
        await asyncio.sleep(SYNC_SECONDS)


async def init_v27_cloud(application: Any) -> None:
    application.state.v27_cloud = {
        "version": VERSION,
        "status": "BAŞLIYOR",
        "persistent": False,
        "restore_attempted": False,
        "restored": False,
        "last_sync": None,
        "last_error": None,
        "evidence_count": 0,
        "started_epoch": time.time(),
    }
    if application.state.db_pool is not None:
        try:
            restored = await restore_cloud_state(application)
            application.state.v27_cloud.update({"restore_attempted": True, "restored": restored})
        except Exception as exc:
            application.state.v27_cloud.update({"last_error": str(exc)[:240]})
    await sync_cloud_state(application)
    application.state.v27_cloud_task = asyncio.create_task(cloud_loop(application))


async def shutdown_v27_cloud(application: Any) -> None:
    task = getattr(application.state, "v27_cloud_task", None)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    try:
        await asyncio.wait_for(sync_cloud_state(application), timeout=3)
    except Exception:
        pass


def operations_payload(application: Any) -> dict[str, Any]:
    v21 = application.state.v21_demo
    demo = application.state.binance_demo
    cloud = application.state.v27_cloud
    snapshot = v21.get("snapshot") or {}
    certificate = certificate_payload(v21)
    auto = v21.get("auto", {})
    return {
        "version": VERSION,
        "mode": "TESTNET_FIRST_CLOUD_DURABLE",
        "scope": "admin",
        "generated_at": now_iso(),
        "deployment": {
            "tier": DEPLOYMENT_TIER,
            "always_on": DEPLOYMENT_TIER in {"ALWAYS_ON", "PAID", "PRODUCTION"},
            "database": "KALICI" if cloud.get("persistent") else "BEKLİYOR",
            "uptime_seconds": max(0, int(time.time() - float(cloud.get("started_epoch", time.time())))),
        },
        "testnet": {
            "configured": credentials_configured(),
            "connected": bool(demo.get("connected")),
            "armed": float(demo.get("armed_until", 0)) > time.time(),
            "stream": json_safe(v21.get("stream", {})),
            "auto": json_safe(auto),
            "account": json_safe({
                "wallet_balance": snapshot.get("wallet_balance"),
                "available_balance": snapshot.get("available_balance"),
                "unrealized_pnl": snapshot.get("unrealized_pnl"),
                "positions": snapshot.get("positions", []),
                "open_orders": snapshot.get("open_orders", []),
                "open_algo_orders": snapshot.get("open_algo_orders", []),
            }),
            "daily": json_safe({
                "entries": sum(1 for event in v21.get("journal", []) if event.get("kind") == "AUTO_ORDER" and str(event.get("created_at", ""))[:10] == now_iso()[:10]),
                "last_decision": auto.get("last_decision"),
                "last_scan": auto.get("last_scan"),
            }),
        },
        "evidence": {
            "status": cloud.get("status"),
            "persistent": bool(cloud.get("persistent")),
            "restored": bool(cloud.get("restored")),
            "count": int(cloud.get("evidence_count", 0)),
            "last_sync": cloud.get("last_sync"),
            "last_error": cloud.get("last_error"),
            "events": json_safe(v21.get("journal", [])[:60]),
            "certificate": json_safe(certificate),
        },
        "safety": {
            "testnet_only": True,
            "paper_enabled": False,
            "real_trading_locked": True,
            "auto_resumes_after_restart": False,
            "profit_guaranteed": False,
        },
    }


def member_operations_payload(request: Request) -> dict[str, Any]:
    """Only the signed-in member's own Demo account. Nothing platform-wide is included."""
    v21 = v21_state_for(request)
    demo = demo_state_for(request)
    snapshot = v21.get("snapshot") or {}
    auto = v21.get("auto", {})
    journal = [event for event in v21.get("journal", []) if isinstance(event, dict)]
    today = now_iso()[:10]
    return {
        "version": VERSION,
        "mode": "MEMBER_DEMO",
        "scope": "member",
        "generated_at": now_iso(),
        "deployment": {"tier": "", "always_on": True, "database": "", "uptime_seconds": 0},
        "testnet": {
            "configured": credentials_configured(request),
            "connected": bool(demo.get("connected")),
            "armed": float(demo.get("armed_until", 0) or 0) > time.time(),
            "stream": json_safe(v21.get("stream", {})),
            "auto": json_safe({key: auto.get(key) for key in ("enabled", "busy", "cycles", "last_scan", "last_decision", "last_error")}),
            "account": json_safe({
                "wallet_balance": snapshot.get("wallet_balance"),
                "available_balance": snapshot.get("available_balance"),
                "unrealized_pnl": snapshot.get("unrealized_pnl"),
                "positions": snapshot.get("positions", []),
                "open_orders": snapshot.get("open_orders", []),
                "open_algo_orders": snapshot.get("open_algo_orders", []),
            }),
            "daily": json_safe({
                "entries": sum(1 for event in journal if event.get("kind") == "AUTO_ORDER" and str(event.get("created_at", ""))[:10] == today),
                "last_decision": auto.get("last_decision"),
                "last_scan": auto.get("last_scan"),
            }),
        },
        "evidence": {
            "status": "KAYITLI",
            "persistent": True,
            "restored": False,
            "count": len(journal),
            "last_sync": journal[0].get("created_at") if journal else None,
            "last_error": None,
            "events": json_safe(journal[:60]),
            "certificate": {"status": "", "score": 0, "passed_gates": 0, "total_gates": 0, "gates": []},
        },
        "safety": {
            "testnet_only": True,
            "paper_enabled": False,
            "real_trading_locked": True,
            "auto_resumes_after_restart": False,
            "profit_guaranteed": False,
        },
    }


def is_owner(request: Request) -> bool:
    return (getattr(request.state, "member", None) or {}).get("role") == "OWNER"


@router.get("/operations")
async def v27_operations(request: Request) -> dict[str, Any]:
    if is_owner(request):
        return operations_payload(request.app)
    if getattr(request.state, "member", None) is None:
        raise HTTPException(401, "Giriş gerekli")
    return member_operations_payload(request)


@router.post("/evidence/sync")
async def v27_evidence_sync(request: Request) -> dict[str, Any]:
    if not is_owner(request):
        raise HTTPException(403, "Bu işlem yalnızca yönetici içindir")
    result = await sync_cloud_state(request.app)
    if not result.get("persistent"):
        raise HTTPException(503, result.get("last_error") or "Kalıcı veritabanı henüz hazır değil.")
    return operations_payload(request.app)
