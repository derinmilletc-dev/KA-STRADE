"""Instagram vitrini: normalleştirme, önbellek, sayfalama, hata durumları ve gizli bilgi sızmaması."""
import json
import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import instagram_showcase as ig

TOKEN = "IGAA-test-secret-token-0123456789"
SECRET = b"k" * 32
CDN = "https://scontent-ist1-1.cdninstagram.com/v/t51/"


class FakeMeta:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.fail: str | None = None
        self.reject_fields: set[str] = set()
        self.pages = {
            None: {"data": [self.media("101", "IMAGE"), self.media("102", "VIDEO"), self.carousel("103")],
                   "paging": {"cursors": {"after": "CURSOR2"}, "next": f"https://graph.instagram.com/v26.0/me/media?access_token={TOKEN}&after=CURSOR2"}},
            "CURSOR2": {"data": [self.media("104", "IMAGE")], "paging": {"cursors": {"before": "x"}}},
        }

    @staticmethod
    def media(media_id, media_type):
        row = {"id": media_id, "media_type": media_type, "permalink": f"https://www.instagram.com/p/{media_id}/",
               "caption": f"Gönderi {media_id}", "timestamp": "2026-10-30T12:00:00+0000",
               "media_url": f"{CDN}{media_id}.jpg"}
        if media_type == "VIDEO":
            row.update(media_url=f"{CDN}{media_id}.mp4", thumbnail_url=f"{CDN}{media_id}-thumb.jpg")
        return row

    def carousel(self, media_id):
        return {**self.media(media_id, "CAROUSEL_ALBUM"),
                "children": {"data": [{"id": "201", "media_type": "IMAGE", "media_url": f"{CDN}201.jpg"},
                                      {"id": "202", "media_type": "VIDEO", "media_url": f"{CDN}202.mp4", "thumbnail_url": f"{CDN}202.jpg"},
                                      {"id": "203", "media_type": "IMAGE", "media_url": "https://evil.example.com/x.jpg"}]}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if self.fail == "token":
            return httpx.Response(400, json={"error": {"code": 190, "message": f"Invalid OAuth access token {TOKEN}"}})
        if self.fail == "server":
            return httpx.Response(500, json={"error": {"code": 2, "message": "internal"}})
        if request.url.path.endswith("/refresh_access_token"):
            return httpx.Response(200, json={"access_token": "IGAA-refreshed-token", "token_type": "bearer", "expires_in": 5184000})
        fields = request.url.params.get("fields", "")
        if any(name in fields for name in self.reject_fields):
            return httpx.Response(400, json={"error": {"code": 100, "message": "Tried accessing nonexisting field"}})
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json=self.pages[request.url.params.get("after")])
        return httpx.Response(200, json={"user_id": "1789", "username": "kaistrade_", "name": "KAISTRADE",
                                         "profile_picture_url": f"{CDN}avatar.jpg", "biography": "Kripto analiz platformu",
                                         "media_count": 42})


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def setup(monkeypatch, tmp_path):
    meta, clock = FakeMeta(), Clock()
    service = ig.InstagramShowcase(transport=httpx.MockTransport(meta.handler), token_path=tmp_path / "token.enc", clock=clock)
    # İlk istekte yenileme denenmesin diye anahtar "yeni yenilenmiş" sayılır.
    service.tokens.last_attempt = clock.now
    app = FastAPI()
    app.include_router(ig.router)
    app.state.instagram_showcase = service
    app.state.v22_commercial = {"secret": SECRET}
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("INSTAGRAM_USERNAME", "kaistrade_")
    monkeypatch.delenv("INSTAGRAM_USER_ID", raising=False)
    monkeypatch.delenv("INSTAGRAM_API_VERSION", raising=False)
    return TestClient(app), meta, clock, service


def feed(client, after=None):
    return client.get(ig.FEED_PATH, params={"after": after} if after else None)


def test_missing_credentials_return_safe_fallback_without_calling_meta(setup, monkeypatch):
    client, meta, _, _ = setup
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN")
    body = feed(client).json()
    assert body["configured"] is False and body["available"] is False
    assert body["media"] == [] and body["profile"] is None
    assert body["profile_url"] == "https://www.instagram.com/kaistrade_/"
    assert meta.calls == []


def test_profile_and_media_are_normalized_and_secrets_never_leak(setup):
    client, meta, _, _ = setup
    response = feed(client)
    body = response.json()
    assert response.status_code == 200 and body["available"] is True and body["stale"] is False
    assert body["profile"] == {"username": "kaistrade_", "name": "KAISTRADE", "profile_picture_url": f"{CDN}avatar.jpg",
                               "biography": "Kripto analiz platformu", "media_count": 42,
                               "profile_url": "https://www.instagram.com/kaistrade_/"}
    assert [row["id"] for row in body["media"]] == ["101", "102", "103"]
    video = body["media"][1]
    assert video["media_url"].endswith("102.mp4") and video["thumbnail_url"].endswith("102-thumb.jpg")
    carousel = body["media"][2]
    assert [child["id"] for child in carousel["children"]] == ["201", "202"]  # yabancı alan adı atıldı
    assert body["next"] == "CURSOR2"
    text = response.text
    assert TOKEN not in text and "access_token" not in text and "graph.instagram.com" not in text
    assert all(call.url.host == "graph.instagram.com" for call in meta.calls)
    assert all(call.url.path.startswith("/v26.0/") for call in meta.calls)


def test_unsafe_or_unknown_media_is_dropped():
    assert ig.normalize_media({"id": "1", "media_type": "IMAGE", "permalink": "https://www.instagram.com/p/1/",
                               "media_url": "javascript:alert(1)"}) is None
    assert ig.normalize_media({"id": "1", "media_type": "STORY", "permalink": "https://www.instagram.com/p/1/",
                               "media_url": f"{CDN}1.jpg"}) is None
    assert ig.normalize_media({"id": "1", "media_type": "IMAGE", "permalink": "https://evil.example.com/p/1/",
                               "media_url": f"{CDN}1.jpg"}) is None
    assert ig.safe_media_url("https://cdninstagram.com.evil.example.com/x.jpg") is None
    assert ig.safe_media_url("http://scontent.cdninstagram.com/x.jpg") is None
    assert ig.safe_media_url("https://video.xx.fbcdn.net/v.mp4") == "https://video.xx.fbcdn.net/v.mp4"


def test_unsupported_fields_fall_back_to_documented_minimum(setup):
    client, meta, _, service = setup
    meta.reject_fields = {"biography", "children"}
    body = feed(client).json()
    assert body["available"] is True
    assert body["profile"]["biography"] is None and body["profile"]["media_count"] == 42
    assert "children" not in service.media_fields and "biography" not in service.profile_fields


def test_cache_hit_then_expiry_refetches(setup):
    client, meta, clock, _ = setup
    feed(client)
    first = len(meta.calls)
    feed(client)
    assert len(meta.calls) == first  # önbellekten
    clock.now += ig.CACHE_SECONDS + 1
    feed(client)
    assert len(meta.calls) > first


def test_pagination_only_accepts_cursors_this_server_issued(setup):
    client, _, _, _ = setup
    assert feed(client, "UNKNOWN").status_code == 400
    assert feed(client, "bad cursor!").status_code == 400
    first = feed(client).json()
    second = feed(client, first["next"])
    assert second.status_code == 200
    body = second.json()
    assert [row["id"] for row in body["media"]] == ["104"] and body["next"] is None and body["profile"] is None


@pytest.mark.parametrize("failure", ["timeout", "token", "server"])
def test_failures_without_cache_show_safe_unavailable_state(setup, failure, caplog):
    client, meta, _, _ = setup
    meta.fail = failure
    with caplog.at_level(logging.INFO):
        response = feed(client)
    body = response.json()
    assert response.status_code == 200
    assert body["configured"] is True and body["available"] is False and body["media"] == []
    assert TOKEN not in response.text and TOKEN not in caplog.text
    assert "Invalid OAuth" not in response.text


def test_failure_after_success_serves_last_content_marked_stale(setup):
    client, meta, clock, _ = setup
    fresh = feed(client).json()
    meta.fail = "server"
    clock.now += ig.CACHE_SECONDS + 1
    stale = feed(client).json()
    assert stale["available"] is True and stale["stale"] is True
    assert stale["media"] == fresh["media"]
    calls = len(meta.calls)
    feed(client)
    assert len(meta.calls) == calls  # hatadan hemen sonra Meta'ya yüklenilmez


def test_token_refresh_is_encrypted_on_disk_and_used_afterwards(setup, tmp_path):
    client, meta, clock, service = setup
    service.tokens.last_attempt = 0
    feed(client)
    refresh = [call for call in meta.calls if call.url.path.endswith("/refresh_access_token")]
    assert len(refresh) == 1 and refresh[0].url.params["grant_type"] == "ig_refresh_token"
    stored = (tmp_path / "token.enc").read_bytes()
    assert b"IGAA-refreshed-token" not in stored and TOKEN.encode() not in stored
    later = [call for call in meta.calls if not call.url.path.endswith("/refresh_access_token")]
    assert all(call.url.params["access_token"] == "IGAA-refreshed-token" for call in later)
    # Ortamdaki anahtar değişirse eski kayıt kullanılmaz.
    token, record = service.tokens.current(ig.load_config({"INSTAGRAM_ACCESS_TOKEN": "IGAA-new-env"}), SECRET)
    assert token == "IGAA-new-env" and record is None


def test_config_validation_rejects_unsafe_values():
    config = ig.load_config({"INSTAGRAM_ACCESS_TOKEN": "t", "INSTAGRAM_USER_ID": "../me", "INSTAGRAM_API_VERSION": "v1/../x",
                             "INSTAGRAM_USERNAME": "bad name!"})
    assert config.user_id == "me" and config.version == ig.DEFAULT_API_VERSION and config.username == "kaistrade_"


def test_httpx_log_lines_with_tokens_are_dropped():
    guard = ig.TokenLogFilter()
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP Request: GET %s", (f"https://graph.instagram.com/me?access_token={TOKEN}",), None)
    assert guard.filter(record) is False
    other = logging.LogRecord("app", logging.INFO, __file__, 1, "unrelated", (), None)
    assert guard.filter(other) is True


def test_feed_path_is_public_in_application_gates():
    from app import main, web_security
    assert ig.FEED_PATH in main.MEMBER_PUBLIC_PATHS
    assert ig.FEED_PATH in web_security.PUBLIC_PATHS
    assert json.dumps(sorted(r.path for r in main.app.routes if getattr(r, "path", "") == ig.FEED_PATH)) == json.dumps([ig.FEED_PATH])
