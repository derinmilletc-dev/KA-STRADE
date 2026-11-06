"""Günlük defter ısıtma — AĞ YOK. Durum sorgusu defteri arka planda taze tutar; kapı ve tazelik şartı değişmez."""
from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from app import daily_ledger as dl
from app import v25_execution as v25

CREDS = ("A" * 12, "B" * 12)


class FakeClient:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    async def signed(self, method, path, params=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return []  # bugün için gelir satırı yok: geçerli, boş defter


def app(connected=True):
    return SimpleNamespace(state=SimpleNamespace(v25_execution={"connected": connected}))


def run_with(client, scenario):
    """Sahte istemciyle senaryoyu çalışan bir olay döngüsünde yürütür."""
    with mock.patch.object(v25, "client_for_with_credentials", lambda application, credentials, request=None: client):
        return asyncio.run(scenario())


class LedgerWarmup(unittest.TestCase):
    def test_warms_a_missing_ledger_so_the_gate_hits_the_cache(self):
        application, client = app(), FakeClient()

        async def scenario():
            self.assertTrue(v25.schedule_ledger_warmup(application, CREDS))
            await application.state.v25_ledger_warm["task"]
            state = application.state.v25_execution
            self.assertIsNotNone(dl.cached(state, max_age=v25.LEDGER_WARM_AGE_SECONDS))  # kapının göreceği taze defter
            before = client.calls
            await v25.refresh_account_daily_ledger(client, state)  # emir anındaki kapı: ağa gitmez
            self.assertEqual(client.calls, before)

        run_with(client, scenario)
        self.assertEqual(client.calls, 1)

    def test_fresh_ledger_is_not_refreshed_again(self):
        application, client = app(), FakeClient()

        async def scenario():
            self.assertTrue(v25.schedule_ledger_warmup(application, CREDS))
            await application.state.v25_ledger_warm["task"]
            application.state.v25_ledger_warm["next_at"] = 0.0  # geri çekilme süresi geçmiş olsa bile defter taze
            self.assertFalse(v25.schedule_ledger_warmup(application, CREDS))

        run_with(client, scenario)
        self.assertEqual(client.calls, 1)

    def test_single_flight_while_a_refresh_is_running(self):
        application, client = app(), FakeClient()

        async def scenario():
            self.assertTrue(v25.schedule_ledger_warmup(application, CREDS))
            self.assertFalse(v25.schedule_ledger_warmup(application, CREDS))  # ilk görev bitmeden ikincisi başlamaz
            await application.state.v25_ledger_warm["task"]

        run_with(client, scenario)
        self.assertEqual(client.calls, 1)

    def test_read_failure_backs_off_and_never_raises(self):
        application, client = app(), FakeClient(fail=True)

        async def scenario():
            self.assertTrue(v25.schedule_ledger_warmup(application, CREDS))
            await application.state.v25_ledger_warm["task"]  # istisna fırlatmaz
            warm = application.state.v25_ledger_warm
            self.assertGreater(warm["next_at"], time.monotonic() + v25.LEDGER_WARM_BACKOFF_SECONDS - 5)
            self.assertFalse(v25.schedule_ledger_warmup(application, CREDS))  # geri çekilme sürüyor: Binance zorlanmaz

        run_with(client, scenario)
        self.assertEqual(client.calls, 1)

    def test_not_connected_or_unusable_credentials_do_nothing(self):
        client = FakeClient()

        async def scenario():
            self.assertFalse(v25.schedule_ledger_warmup(app(connected=False), CREDS))
            self.assertFalse(v25.schedule_ledger_warmup(app(), ("", "")))
            self.assertFalse(v25.schedule_ledger_warmup(app(), ("short", "short")))
            self.assertFalse(v25.schedule_ledger_warmup(SimpleNamespace(state=SimpleNamespace()), CREDS))  # bozuk uygulama: sessizce False

        run_with(client, scenario)
        self.assertEqual(client.calls, 0)

    def test_warm_age_is_below_the_gate_freshness(self):
        self.assertLess(v25.LEDGER_WARM_AGE_SECONDS, dl.FRESH_SECONDS)


if __name__ == "__main__":
    unittest.main()
