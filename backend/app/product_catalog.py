"""Müşteriye anlatılan ürün bilgisi (planlar, fiyatlar, ödeme, günlük AI hakları).

Kais AI'nin plan/ödeme cevapları yalnız buradan beslenir. Rakamlar elle yazılmaz; ödeme, deneme,
Grafik AI, AI Analist ve asistan sınırlarını uygulayan modüllerden okunur. Burada yönetim, sunucu
veya iç süreç bilgisi bulunmaz; yalnız müşterinin bilmesi gerekenler vardır.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from . import binance_manual_payments, chart_ai_store, member_entitlements, subscription_core
from .analyst_credits import CreditConfig
from .assistant_config import AssistantConfig

CURRENCY = "USDT"
PAYMENT_METHOD = "Binance Pay"
# KAIS AI mesaj hakkı UTC gün başında (Türkiye saatiyle 03:00) yenilenir; Grafik AI Türkiye saatiyle 00:00'da.
KAIS_RESET_HOUR_TR = 3
CHART_RESET_HOUR_TR = 0


def price(plan: str) -> str:
    return f"{Decimal(binance_manual_payments.PLAN_AMOUNTS[plan]):.2f}"


def limits(assistant: AssistantConfig | None = None, credits: CreditConfig | None = None) -> dict[str, dict[str, int]]:
    """Plan başına günlük AI hakları. Değerler sınırı uygulayan ayarlardan okunur."""
    assistant = assistant or AssistantConfig()
    credits = credits or CreditConfig()
    paid = {
        "kais_ai_messages_per_day": assistant.daily_limit,
        "chart_ai_per_day": chart_ai_store.SUBSCRIBER_DAILY_LIMIT,
        "ai_analyst_per_day": member_entitlements.ANALYST_PAID_DAILY,
    }
    return {
        "TRIAL": {
            "kais_ai_messages_per_day": min(assistant.trial_daily_limit, assistant.daily_limit),
            "chart_ai_per_day": chart_ai_store.TRIAL_DAILY_LIMIT,
            "ai_analyst_per_day": max(1, credits.total // credits.cost),
        },
        "SMART_TRADE": {
            "kais_ai_messages_per_day": min(assistant.smart_trade_daily_limit, assistant.daily_limit),
            "chart_ai_per_day": chart_ai_store.SMART_TRADE_DAILY_LIMIT,
            "ai_analyst_per_day": member_entitlements.ANALYST_SMART_TRADE_DAILY,
        },
        "MASTER_MODE": dict(paid),
    }


def plans(assistant: AssistantConfig | None = None, credits: CreditConfig | None = None) -> dict[str, Any]:
    """get_plans aracının müşteriye dönük, eksiksiz plan özeti."""
    rights = limits(assistant, credits)
    period = binance_manual_payments.PAID_PERIOD_DAYS
    return {
        "plans": [
            {
                "id": "TRIAL", "name": "1 Hafta Ücretsiz Deneme", "price": "0", "currency": CURRENCY,
                "period_days": subscription_core.TRIAL_DAYS, "payment_required": False,
                "live_trading": False, "live_strategies_and_auto_trade": False,
                "includes": [
                    "Master Trade analiz ekranı", "Demo hesapta manuel ve Grid işlemler",
                    "KAIS stratejileri ve Auto Trade yalnız Demo hesapta", "Pozisyon koruması (Demo)",
                    "Scanner tam açık", "KAIS AI, Grafik AI ve AI Analist (günlük sınırlı)",
                ],
                "not_included": ["Gerçek parayla (canlı) işlem", "Canlı strateji ve canlı Auto Trade"],
                "daily_limits": rights["TRIAL"],
                "rules": [
                    "Hesap başına bir kez kullanılır", "E-posta doğrulaması gerekir",
                    "Ödeme bilgisi istenmez", "Süre bitince otomatik ücret alınmaz, ücretli plana kendiliğinden geçmez",
                ],
            },
            {
                "id": "SMART_TRADE", "name": "SMART TRADE", "price": price("SMART_TRADE"), "currency": CURRENCY,
                "period_days": period, "payment_required": True,
                "live_trading": True, "live_strategies_and_auto_trade": False,
                "includes": [
                    "Manuel işlem (Demo ve canlı)", "Grid işlemleri", "Master Trade ekranının tamamı",
                    "Stop, hedef ve pozisyon koruması", "Demo hesapta tüm KAIS stratejileri ve Auto Trade",
                    "AI Analist ve Scanner", "Analiz Stüdyosu", "KAIS Rehber", "KAIS AI asistanı", "Destek",
                ],
                "not_included": ["Canlı hesapta KAIS stratejileri", "Canlı Auto Trade"],
                "daily_limits": rights["SMART_TRADE"],
            },
            {
                "id": "MASTER_MODE", "name": "MASTER MODE", "price": price("MASTER_MODE"), "currency": CURRENCY,
                "period_days": period, "payment_required": True,
                "live_trading": True, "live_strategies_and_auto_trade": True,
                "includes": [
                    "Smart Trade'deki her şey", "Canlı hesapta KAIS stratejileri", "Canlı Auto Trade",
                    "Öncelikli ve hızlı destek",
                ],
                "not_included": [],
                "daily_limits": rights["MASTER_MODE"],
            },
        ],
        "same_kais_ai_for_all_plans": True,
        "daily_reset": {
            "kais_ai": f"Her gün Türkiye saatiyle {KAIS_RESET_HOUR_TR:02d}:00",
            "chart_ai": f"Her gün Türkiye saatiyle {CHART_RESET_HOUR_TR:02d}:00",
            "ai_analyst": f"İlk analizle başlayan {(credits or CreditConfig()).window_hours} saatlik pencere dolunca",
        },
        "payment": {
            "method": PAYMENT_METHOD, "currency": CURRENCY,
            "where": "Ana sayfa → ABONELİK (telefonda Menü → Abonelik), planı seç, “Binance ile öde”",
            "steps": [
                "“Binance ile öde”ye bas; sana özel referans kodu oluşur",
                "Plan tutarını Binance Pay ile ödeme ekranındaki Pay ID'ye gönder, ödeme notuna referans kodunu yaz",
                f"Binance işlem/sipariş numarasını {binance_manual_payments.PAYMENT_WINDOW_HOURS} saat içinde aynı ekrana gir",
                "Ödeme kontrol edilip onaylanınca erişim açılır ve bildirim gelir",
            ],
            "approval": "Her ödeme elle kontrol edilir; genelde birkaç saat içinde onaylanır",
            "card_or_wallet_stored": False,
        },
        "renewal": {
            "automatic_renewal": False,
            "how": f"Süre bitince tekrar ödeme yaparak {period} gün daha uzatılır",
            "early_payment": "Plan veya deneme sürerken yapılan ödeme onaylanırsa yeni dönem mevcut sürenin bitişinden sonra başlar; kalan gün kaybolmaz",
        },
        "cancellation": {
            "needed": False,
            "note": "Otomatik yenileme olmadığı için iptal gerekmez; süre bitince plan kendiliğinden kapanır ve ücret alınmaz",
        },
        "refund_and_problems": "İade, yanlış tutar, reddedilen veya uzun süre bekleyen ödeme için Destek bölümünden talep oluşturulur",
        "free_member": {
            "chart_ai_total": chart_ai_store.FREE_LIFETIME_LIMIT,
            "ai_analyst_per_window": max(1, (credits or CreditConfig()).total // (credits or CreditConfig()).cost),
            "note": "Plansız üye Master Trade'i görüntüler; canlı işlem ve ücretli özellikler kapalıdır",
        },
    }
