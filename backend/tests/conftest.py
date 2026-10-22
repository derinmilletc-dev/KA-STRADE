from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_binance_global_ban(tmp_path: Path):
    from app.binance_rate_limit import BINANCE_GLOBAL_BAN

    previous_path = BINANCE_GLOBAL_BAN.path
    BINANCE_GLOBAL_BAN.path = tmp_path / "binance_global_ban.json"
    BINANCE_GLOBAL_BAN.reset(clear_persistent=True)
    try:
        yield
    finally:
        BINANCE_GLOBAL_BAN.reset(clear_persistent=True)
        BINANCE_GLOBAL_BAN.path = previous_path


@pytest.fixture(autouse=True)
def isolate_live_shared_clock():
    """Süreç genelindeki canlı saat ölçümü testler arasında taşınmasın."""
    from app import v25_execution

    previous = dict(v25_execution._SHARED_CLOCK)
    v25_execution._SHARED_CLOCK.update(offset_ms=0, synced_at=0.0)
    try:
        yield
    finally:
        v25_execution._SHARED_CLOCK.clear()
        v25_execution._SHARED_CLOCK.update(previous)


@pytest.fixture(autouse=True)
def isolate_protected_market_lane():
    """Canlı stratejinin korumalı (sabit IP) piyasa yolu süreç geneli tekildir; testler arasında taşınmasın."""
    from app.public_market_data import PROTECTED_LANE

    previous = (PROTECTED_LANE.fetch, PROTECTED_LANE.clock)
    PROTECTED_LANE.fetch, PROTECTED_LANE.paused_until, PROTECTED_LANE.window = None, 0.0, []
    try:
        yield
    finally:
        PROTECTED_LANE.fetch, PROTECTED_LANE.clock = previous
        PROTECTED_LANE.paused_until, PROTECTED_LANE.window = 0.0, []
