import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app import live_registry as reg


def make_state(application, user_id):
    return {"_live_user_id": user_id, "events": [], "auto": {"enabled": False}}


class LiveRegistryTests(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(state=reg.UserAwareState())
        self.seen = []

        async def engine(application):
            self.seen.append(reg.CURRENT_USER.get())
            await asyncio.sleep(3600)

        self.registry = reg.LiveRegistry(self.app, make_state, engine)
        self.app.state.live_registry = self.registry

    def test_each_member_gets_their_own_state(self):
        async def run():
            reg.bind("alice")
            alice = self.app.state.v25_execution
            alice["events"].append("alice-order")
            reg.bind("bob")
            bob = self.app.state.v25_execution
            self.assertIsNot(alice, bob)
            self.assertEqual(bob["events"], [])
            reg.bind("alice")
            self.assertIs(self.app.state.v25_execution, alice)
        asyncio.run(run())

    def test_concurrent_requests_do_not_mix(self):
        async def request(user, delay):
            reg.bind(user)
            state = self.app.state.v25_execution
            await asyncio.sleep(delay)
            self.assertIs(self.app.state.v25_execution, state)
            self.assertEqual(state["_live_user_id"], user)
            state["events"].append(user)
            return state["events"]
        async def run():
            a, b = await asyncio.gather(asyncio.create_task(request("alice", 0.02)), asyncio.create_task(request("bob", 0.0)))
            self.assertEqual(a, ["alice"])
            self.assertEqual(b, ["bob"])
        asyncio.run(run())

    def test_unbound_context_sees_only_the_system_state(self):
        async def run():
            state = self.app.state.v25_execution
            self.assertEqual(state["_live_user_id"], "")
            self.assertIs(state, self.registry.system)
            reg.bind("alice")
            self.assertIsNot(self.app.state.v25_execution, self.registry.system)
        asyncio.run(run())

    def test_engine_starts_once_per_member_bound_to_that_member(self):
        async def run():
            self.assertTrue(self.registry.ensure_engine("alice"))
            self.assertFalse(self.registry.ensure_engine("alice"))
            self.assertTrue(self.registry.ensure_engine("bob"))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertCountEqual(self.seen, ["alice", "bob"])
            await self.registry.stop_engines()
        asyncio.run(run())

    def test_no_engine_without_a_member(self):
        async def run():
            self.assertFalse(self.registry.ensure_engine(""))
            self.assertFalse(self.registry.ensure_engine(None))
        asyncio.run(run())

    def test_snapshot_keys_are_per_member(self):
        self.assertEqual(reg.snapshot_key_for("alice"), "v25_live:state:alice")
        self.assertNotEqual(reg.snapshot_key_for("alice"), reg.snapshot_key_for("bob"))

    def test_flag_needs_database(self):
        with mock.patch.dict("os.environ", {"MULTI_USER_LIVE": "1", "DATABASE_URL": ""}):
            self.assertFalse(reg.enabled())
        with mock.patch.dict("os.environ", {"MULTI_USER_LIVE": "1", "DATABASE_URL": "postgres://x"}):
            self.assertTrue(reg.enabled())
        with mock.patch.dict("os.environ", {"MULTI_USER_LIVE": "", "DATABASE_URL": "postgres://x"}):
            self.assertFalse(reg.enabled())

    def test_plain_state_attributes_still_work(self):
        self.app.state.other = 5
        self.assertEqual(self.app.state.other, 5)
        self.assertTrue(hasattr(self.app.state, "v25_execution"))


if __name__ == "__main__":
    unittest.main()
