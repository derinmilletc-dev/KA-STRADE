import asyncio
import unittest

from app.symbol_lock import SymbolAwareLock, declare_entry, position_guard


class SymbolAwareLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_other_symbol_runs_while_entry_in_flight(self):
        lock = SymbolAwareLock()
        entry_started, release_entry, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def entry():
            with declare_entry("BTCUSDT"):
                async with lock:
                    entry_started.set()
                    await release_entry.wait()

        async def close():
            await entry_started.wait()
            async with position_guard({"lock": lock}, "ETHUSDT"):
                closed.set()

        tasks = [asyncio.create_task(entry()), asyncio.create_task(close())]
        await asyncio.wait_for(closed.wait(), 1)
        self.assertFalse(release_entry.is_set())
        release_entry.set()
        await asyncio.gather(*tasks)
        self.assertFalse(lock.locked())

    async def test_same_symbol_close_waits_for_entry(self):
        lock = SymbolAwareLock()
        entry_started, release_entry, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def entry():
            with declare_entry("BTCUSDT"):
                async with lock:
                    entry_started.set()
                    await release_entry.wait()

        async def close():
            await entry_started.wait()
            async with lock.symbol("BTCUSDT"):
                closed.set()

        tasks = [asyncio.create_task(entry()), asyncio.create_task(close())]
        await asyncio.sleep(0.05)
        self.assertFalse(closed.is_set())
        release_entry.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
        self.assertTrue(closed.is_set())

    async def test_plain_holder_excludes_symbol_guard_and_vice_versa(self):
        lock = SymbolAwareLock()
        order: list[str] = []
        gate = asyncio.Event()

        async def loop_job():
            async with lock:
                order.append("loop-start")
                await gate.wait()
                order.append("loop-end")

        async def close():
            await asyncio.sleep(0.01)
            async with lock.symbol("ETHUSDT"):
                order.append("close")

        tasks = [asyncio.create_task(loop_job()), asyncio.create_task(close())]
        await asyncio.sleep(0.05)
        self.assertEqual(order, ["loop-start"])
        gate.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
        self.assertEqual(order, ["loop-start", "loop-end", "close"])

        # Pozisyon işlemi sürerken genel kilit (döngü/acil durdurma) bekler.
        order.clear()
        release = asyncio.Event()

        async def close_long():
            async with lock.symbol("ETHUSDT"):
                order.append("close-start")
                await release.wait()
                order.append("close-end")

        async def emergency():
            await asyncio.sleep(0.01)
            async with lock:
                order.append("emergency")

        tasks = [asyncio.create_task(close_long()), asyncio.create_task(emergency())]
        await asyncio.sleep(0.05)
        self.assertEqual(order, ["close-start"])
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
        self.assertEqual(order, ["close-start", "close-end", "emergency"])

    async def test_declaration_does_not_leak_to_child_tasks(self):
        lock = SymbolAwareLock()
        child_ran = asyncio.Event()
        release = asyncio.Event()

        async def child():
            async with lock:  # bildirimsiz: ETH pozisyon işlemiyle yan yana çalışmamalı
                child_ran.set()

        async def entry():
            with declare_entry("BTCUSDT"):
                task = asyncio.create_task(child())
                await release.wait()
                await task

        async def close_eth():
            async with lock.symbol("ETHUSDT"):
                await asyncio.sleep(0.05)
                self.assertFalse(child_ran.is_set())

        main = asyncio.create_task(entry())
        await asyncio.wait_for(close_eth(), 1)
        release.set()
        await asyncio.wait_for(main, 1)
        self.assertTrue(child_ran.is_set())

    async def test_reacquire_after_release_keeps_declaration(self):
        lock = SymbolAwareLock()
        with declare_entry("BTCUSDT"):
            await lock.acquire()
            lock.release()
            await lock.acquire()
            async with lock.symbol("ETHUSDT"):
                pass
            lock.release()

    async def test_plain_asyncio_lock_falls_back(self):
        state = {"lock": asyncio.Lock()}
        async with position_guard(state, "BTCUSDT"):
            self.assertTrue(state["lock"].locked())

    async def test_is_asyncio_lock_and_release_errors(self):
        lock = SymbolAwareLock()
        self.assertIsInstance(lock, asyncio.Lock)
        with self.assertRaises(RuntimeError):
            lock.release()

    async def test_cancelled_waiter_does_not_block_others(self):
        lock = SymbolAwareLock()
        await lock.acquire()
        waiter = asyncio.create_task(lock.acquire())
        await asyncio.sleep(0.01)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        lock.release()
        await asyncio.wait_for(lock.acquire(), 1)
        lock.release()


if __name__ == "__main__":
    unittest.main()
