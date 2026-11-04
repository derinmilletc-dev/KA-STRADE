from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def two_factor_gate_off_unless_test_enables(monkeypatch):
    """Üretimde 2FA varsayılan olarak zorunlu; diğer modüllerin testleri kapıyı kendi açar (REQUIRE_2FA=true)."""
    import os
    if "REQUIRE_2FA" not in os.environ:
        monkeypatch.setenv("REQUIRE_2FA", "false")


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


@pytest.fixture(autouse=True)
def isolate_shared_strategy_cache():
    """Paylaşılan strateji sonucu önbelleği testler arasında taşınmasın (monkeypatch'li stratejiler)."""
    import sys

    def clear():
        module = sys.modules.get("app.original_v2_demo_execution")
        if module is not None:
            module._STRATEGY_CACHE.clear()

    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def daily_ledger_offline_unless_tested(request, monkeypatch):
    """Eski testlerin sahte Binance istemcilerinde /fapi/v1/income yok. Hesap geneli günlük defter (fail-closed) yalnız
    kendi test dosyasında gerçek çalışır; diğerlerinde boş defter döner (olay tabanlı eski hesap aynen geçerli)."""
    if request.module.__name__.endswith("test_daily_loss_account_wide"):
        return
    import importlib

    async def empty(client, state, **kwargs):
        return {"day": None, "fetched_at": 0.0, "totals": {"net": 0.0}, "rows": [], "source": "TEST_OFFLINE"}

    # Testler modülü hem `app.` hem `backend.app.` yoluyla içe aktarabilir (iki ayrı modül nesnesi): ikisini de çevrimdışı yap.
    for name in ("app.daily_ledger", "backend.app.daily_ledger"):
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setattr(module, "refresh", empty)
