"""Stüdyo çizim eşitleme: doğrulama, son yazan kazanır, sınırlar."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from fastapi import HTTPException

    from app import studio_drawings as sd
except ModuleNotFoundError:  # yerel ortamda fastapi yoksa CI'da çalışır
    sd = None


class FakePool:
    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self.executed: list[str] = []

    async def execute(self, sql, *args):
        self.executed.append(sql.split()[0])
        if sql.lstrip().startswith("INSERT"):
            user, symbol, data, stamp = args
            old = self.rows.get((user, symbol))
            if old is None or old["updated_at"] <= stamp:
                self.rows[(user, symbol)] = {"data": data, "updated_at": stamp}
        elif sql.lstrip().startswith("DELETE"):
            user, symbol, stamp = args
            old = self.rows.get((user, symbol))
            if old and old["updated_at"] <= stamp:
                self.rows.pop((user, symbol))

    async def fetchrow(self, sql, user, symbol):
        return self.rows.get((user, symbol))

    async def fetchval(self, sql, user):
        return sum(1 for key in self.rows if key[0] == user)


def request(pool):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=pool)))


@unittest.skipIf(sd is None, "fastapi yok")
class StudioDrawingTests(unittest.TestCase):
    def setUp(self):
        self.pool = FakePool()
        sd._SCHEMA.clear()
        patcher = patch.object(sd, "authenticated_user_async", AsyncMock(return_value={"id": "u1"}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def put(self, drawings, stamp, symbol="BTCUSDT"):
        return asyncio.run(sd.put_drawings(request(self.pool), sd.DrawingsPut(symbol=symbol, drawings=drawings, updated_at=stamp)))

    def get(self, symbol="BTCUSDT"):
        return asyncio.run(sd.get_drawings(request(self.pool), symbol=symbol))

    def test_roundtrip_and_last_writer_wins(self):
        line = {"id": "a", "type": "hline", "price": 10}
        self.assertTrue(self.put([line], 100)["applied"])
        self.assertEqual(self.get(), {"symbol": "BTCUSDT", "drawings": [line], "updated_at": 100})
        stale = self.put([{"id": "b", "type": "hline", "price": 1}], 50)
        self.assertFalse(stale["applied"]); self.assertEqual(stale["updated_at"], 100)
        self.assertEqual(self.get()["drawings"], [line])

    def test_delete_keeps_a_tombstone_so_old_devices_do_not_restore(self):
        self.put([{"id": "a", "type": "hline", "price": 10}], 100)
        self.assertTrue(self.put([], 200)["applied"])
        self.assertEqual(self.get(), {"symbol": "BTCUSDT", "drawings": [], "updated_at": 200})
        self.assertFalse(self.put([{"id": "a", "type": "hline", "price": 10}], 150)["applied"])

    def test_missing_record_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            self.get("ETHUSDT")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_rejects_bad_payloads(self):
        for bad in ([{"id": "a", "type": "script"}], [{"type": "hline"}], ["x"], [{"id": "a" * 41, "type": "hline"}],
                    [{"id": "a", "type": "text", "p": {}, "text": "x" * 3000}]):
            with self.assertRaises(HTTPException) as ctx:
                self.put(bad, 10)
            self.assertEqual(ctx.exception.status_code, 422)
        with self.assertRaises(HTTPException):
            self.put([], 10, symbol="bad symbol!")

    def test_users_are_isolated_and_symbol_cap(self):
        self.put([{"id": "a", "type": "hline", "price": 1}], 10)
        with patch.object(sd, "authenticated_user_async", AsyncMock(return_value={"id": "u2"})), self.assertRaises(HTTPException):
            self.get()
        for index in range(sd.MAX_SYMBOLS_PER_USER - 1):
            self.put([{"id": "a", "type": "hline", "price": 1}], 10, symbol=f"C{index}USDT")
        with self.assertRaises(HTTPException) as ctx:
            self.put([{"id": "a", "type": "hline", "price": 1}], 10, symbol="ZZZUSDT")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertTrue(self.put([{"id": "b", "type": "hline", "price": 2}], 20)["applied"])  # mevcut coin hâlâ güncellenir


if __name__ == "__main__":
    unittest.main()
