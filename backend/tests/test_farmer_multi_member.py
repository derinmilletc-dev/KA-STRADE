"""KAIS FARMER çok üyeli mod: her üyenin kendi çalışma zamanı, kaydı ve hesap anahtarı; döngü o üyeye bağlı; birbirini görmez."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import farmer_runtime as fr  # noqa: E402
from app import live_registry  # noqa: E402
from app import v25_execution as live  # noqa: E402
from app.farmer_engine.store import FarmerStore  # noqa: E402


class FakeRuntime:
    def __init__(self, account_key: str, store: FarmerStore) -> None:
        self.account_key, self.store, self.owner_id = account_key, store, None
        self.bound_in_loop: str | None = None
        self.task: asyncio.Task | None = None
        self.shutdown_bound: str | None = None

    def start_loop(self) -> None:
        async def loop() -> None:
            self.bound_in_loop = live_registry.CURRENT_USER.get()
        self.task = asyncio.get_running_loop().create_task(loop())

    async def shutdown(self) -> None:
        self.shutdown_bound = live_registry.CURRENT_USER.get()
        self.store.db.close()


def builder(application, *, account_key, store, **_):
    return FakeRuntime(account_key, store)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.app = SimpleNamespace(state=SimpleNamespace())
        self._enabled = live_registry.enabled
        live_registry.enabled = lambda: True

    def tearDown(self):
        live_registry.enabled = self._enabled
        self.tmp.cleanup()

    def registry(self):
        return fr.FarmerMemberRegistry(self.app, data_dir=self.dir, builder=builder)


class TestRegistry(Base):
    def test_each_member_gets_own_runtime_store_and_key(self):
        async def go():
            reg = self.registry()
            a, b = reg.for_member("member-a"), reg.for_member("member-b")
            await asyncio.gather(a.task, b.task)
            return reg, a, b
        reg, a, b = asyncio.run(go())
        self.assertIsNot(a, b)
        self.assertEqual((a.account_key, b.account_key), ("LIVE:member-a", "LIVE:member-b"))
        self.assertEqual((a.owner_id, b.owner_id), ("member-a", "member-b"))
        self.assertTrue((self.dir / "farmer_live_member-a.sqlite3").exists())
        self.assertTrue((self.dir / "farmer_live_member-b.sqlite3").exists())
        self.assertEqual((a.bound_in_loop, b.bound_in_loop), ("member-a", "member-b"))   # döngü kendi üyesine bağlı
        self.assertIsNone(live_registry.CURRENT_USER.get())                                # çağıranın bağlaması sızmaz

    def test_same_member_same_runtime_and_invalid_id_refused(self):
        async def go():
            reg = self.registry()
            first = reg.for_member("m1")
            self.assertIs(reg.for_member("m1"), first)
            for bad in ("", None, "../x", "a b", "x" * 81):
                with self.assertRaises(ValueError):
                    reg.for_member(bad)
        asyncio.run(go())

    def test_live_bot_runners_shows_only_bound_members_farmer(self):
        async def go():
            reg = self.registry()
            self.app.state.live_farmer_registry = reg
            a, b = reg.for_member("m1"), reg.for_member("m2")
            token = live_registry.bind("m1")
            try:
                self.assertEqual(live.live_bot_runners(self.app), [a])
            finally:
                live_registry.CURRENT_USER.reset(token)
            token = live_registry.bind("m3")
            try:
                self.assertEqual(live.live_bot_runners(self.app), [])                      # Farmer'ı olmayan üye: liste boş
            finally:
                live_registry.CURRENT_USER.reset(token)
            self.assertNotIn(b, live.live_bot_runners(self.app))
        asyncio.run(go())

    def test_restore_pending_only_members_with_active_state(self):
        s = FarmerStore(str(self.dir / "farmer_live_busy.sqlite3"))
        s.save_bot("LIVE:busy", "RUNNING", {"x": 1})
        s.db.close()
        s = FarmerStore(str(self.dir / "farmer_live_idle.sqlite3"))
        s.save_bot("LIVE:idle", "OFF", {"x": 1})
        s.db.close()

        async def go():
            reg = self.registry()
            return reg, reg.restore_pending()
        reg, restored = asyncio.run(go())
        self.assertEqual(restored, ["busy"])
        self.assertIsNotNone(reg.get("busy"))
        self.assertIsNone(reg.get("idle"))

    def test_shutdown_releases_each_member_under_its_own_binding(self):
        async def go():
            reg = self.registry()
            a = reg.for_member("m1")
            await reg.shutdown()
            return reg, a
        reg, a = asyncio.run(go())
        self.assertEqual(a.shutdown_bound, "m1")
        self.assertEqual(reg.runtimes, {})


class TestStreamDemand(Base):
    def test_member_demands_are_unioned_and_released_separately(self):
        from app.original_v2_live import apply_farmer_stream_demand

        async def as_member(uid, symbols):
            token = live_registry.bind(uid)
            try:
                await apply_farmer_stream_demand(self.app, symbols)
            finally:
                live_registry.CURRENT_USER.reset(token)

        async def go():
            await as_member("m1", ["BTCUSDT", "ETHUSDT"])
            await as_member("m2", ["ETHUSDT", "SOLUSDT"])
            self.assertEqual(self.app.state.farmer_symbol_demand, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
            await as_member("m1", [])                                         # m1 kapattı: m2'nin talebi kalır
            self.assertEqual(self.app.state.farmer_symbol_demand, ["ETHUSDT", "SOLUSDT"])
        asyncio.run(go())


class TestInstall(Base):
    def test_install_and_uninstall_registry(self):
        async def go():
            reg = fr.install(self.app, data_dir=self.dir, builder=builder)
            self.assertIsInstance(reg, fr.FarmerMemberRegistry)
            reg.for_member("m1")
            await fr.uninstall(self.app)
            self.assertIsNone(self.app.state.live_farmer_registry)
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
