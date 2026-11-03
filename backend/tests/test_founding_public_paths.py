"""Founding 30 genel uçları oturum istemeden çalışmalı; /me ise oturum ister.

Başvuru, onay, durum sorgulama ve davet bağlantısı oturum açılmadan kullanılır. Bir genel uç
MEMBER_PUBLIC_PATHS'e eklenmezse ara katman "Oturum gerekli" (401) döner ve akış canlıda kırılır.
"""
from app import main
from app.founding import public_router

PROTECTED = {"/api/v22/founding/me"}


def test_every_public_founding_route_is_reachable_without_session():
    paths = {route.path for route in public_router.routes}
    assert "/api/v22/founding/invite" in paths and "/api/v22/founding/apply" in paths
    for path in paths - PROTECTED:
        assert path in main.MEMBER_PUBLIC_PATHS, f"{path} oturum istiyor; davetli/başvuran kişi giriş yapmamış olur"


def test_member_status_route_stays_protected():
    for path in PROTECTED:
        assert path not in main.MEMBER_PUBLIC_PATHS
