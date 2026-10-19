"""admin.kaistrade.com is an allowed browser origin for cookie-authenticated mutations; unknown origins are still refused."""
import unittest

from fastapi import HTTPException
from starlette.requests import Request

from app import main
from app.browser_security import USER_SESSION_COOKIE, validate_browser_request

ADMIN = "https://admin.kaistrade.com"


def mutation(origin):
    headers = {"Cookie": f"{USER_SESSION_COOKIE}=synthetic-session-not-live", "X-Requested-With": "XMLHttpRequest"}
    if origin:
        headers["Origin"] = origin
    # The proxy forwards to the API host, so the request's own origin never equals the browser's origin.
    return Request({
        "type": "http", "scheme": "https", "server": ("api.example.test", 443), "path": "/api/test", "method": "POST",
        "query_string": b"", "client": ("127.0.0.1", 1234),
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "state": {},
    })


class AdminOriginTests(unittest.TestCase):
    def test_admin_origin_is_configured_next_to_the_production_origin(self):
        self.assertEqual(main.ADMIN_WEB_ORIGIN, ADMIN)
        self.assertIn(ADMIN, main.WEB_CORS_ORIGINS)
        self.assertIn(main.PRODUCTION_WEB_ORIGIN, main.WEB_CORS_ORIGINS)

    def test_cookie_mutation_from_admin_origin_is_allowed(self):
        validate_browser_request(mutation(ADMIN), main.WEB_CORS_ORIGINS)

    def test_lookalike_and_unknown_origins_are_still_refused(self):
        for origin in ("https://admin.kaistrade.com.evil.example", "http://admin.kaistrade.com", "https://evil.kaistrade.net", ""):
            with self.assertRaises(HTTPException) as caught:
                validate_browser_request(mutation(origin), main.WEB_CORS_ORIGINS)
            self.assertEqual(caught.exception.status_code, 403, origin)


if __name__ == "__main__":
    unittest.main()
