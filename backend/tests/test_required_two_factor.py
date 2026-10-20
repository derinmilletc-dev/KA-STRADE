import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

try:
    import fastapi  # noqa: F401
except ImportError:  # yerelde bağımlılık yoksa yalnızca HTTPException taklidi
    stub = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code, detail=None):
            self.status_code, self.detail = status_code, detail
    stub.HTTPException = HTTPException
    sys.modules["fastapi"] = stub

from fastapi import HTTPException

from app import required_two_factor as gate


def request(path):
    return SimpleNamespace(url=SimpleNamespace(path=path), state=SimpleNamespace())


def run(path, *, has_two_factor, required=True):
    fake = types.ModuleType("app.account_settings")

    async def enabled(_request, _user):
        return has_two_factor
    fake.enabled = enabled
    req = request(path)
    env = {"REQUIRE_2FA": "true" if required else ""}
    with mock.patch.dict(sys.modules, {"app.account_settings": fake}), mock.patch.dict("os.environ", env):
        result = asyncio.run(gate.enforce(req, {"id": "u1"}))
    return result, req


class RequiredTwoFactorTests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(gate.required({}))
        result, req = run("/api/v22/anything", has_two_factor=False, required=False)
        self.assertFalse(result)
        self.assertFalse(getattr(req.state, "two_factor_setup_required", False))

    def test_user_without_two_factor_blocked_outside_setup_paths(self):
        for path in ("/api/v22/account/security", "/api/mod/customers", "/api/market", "/api/v22/admin/accounts"):
            with self.assertRaises(HTTPException) as caught:
                run(path, has_two_factor=False)
            self.assertEqual(caught.exception.status_code, 403)
            self.assertEqual(caught.exception.detail, gate.SETUP_REQUIRED_DETAIL)

    def test_setup_paths_allowed_and_flagged(self):
        for path in gate.SETUP_PATHS:
            result, req = run(path, has_two_factor=False)
            self.assertTrue(result)
            self.assertTrue(req.state.two_factor_setup_required)

    def test_user_with_two_factor_is_not_restricted(self):
        result, req = run("/api/v22/anything", has_two_factor=True)
        self.assertFalse(result)
        self.assertFalse(req.state.two_factor_setup_required)

    def test_path_lookalikes_not_allowed(self):
        self.assertFalse(gate.path_allowed("/api/v22/account/2fa/disable"))
        self.assertFalse(gate.path_allowed("/api/v22/session/../admin"))
        self.assertTrue(gate.path_allowed("/api/v22/session/"))

    def test_read_failure_denies_access(self):
        fake = types.ModuleType("app.account_settings")

        async def enabled(_request, _user):
            raise RuntimeError("db")
        fake.enabled = enabled
        with mock.patch.dict(sys.modules, {"app.account_settings": fake}), mock.patch.dict("os.environ", {"REQUIRE_2FA": "1"}):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(gate.enforce(request("/api/v22/session"), {"id": "u1"}))
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
