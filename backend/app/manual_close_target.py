"""Manuel pozisyon için kapanış hedefi: "fiyat X olunca kapat" ya da "net Y USDT kâr edince kapat".

Saf hesap ve ayar doğrulaması; borsaya dokunmaz. Sunucunun koruma döngüsü (~10 sn) her turda `reached` ile bakar,
hedefe ulaşıldıysa pozisyon reduce-only piyasa emriyle kapatılır. Stop ve hedef emirleri olduğu gibi kalır; kapanış
hedefi onların üstüne eklenen ek bir çıkıştır.

Fiyat hedefi, kurulduğu andaki fiyata göre yön alır: hedef o anki fiyatın üstündeyse fiyat hedefe çıkınca, altındaysa
inince kapanır (LONG ve SHORT için aynı kural; böylece hem kâr al hem kârı kilitle seviyesi kurulabilir).
Kâr hedefi nettir: giriş ve çıkış komisyonu (taraf başı politika ücreti) düşüldükten sonraki tutar."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

PROFIT_MIN = Decimal("0.01")
PROFIT_MAX = Decimal("100000")
MODES = ("price", "profit")


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def build_target(mode: str, value: Any, mark: Any, now_text: str) -> dict[str, Any]:
    """Doğrulanmış hedef kaydı; geçersizse ValueError (mesaj kullanıcıya gösterilir)."""
    if mode not in MODES:
        raise ValueError("Hedef türü fiyat ya da kâr olmalı.")
    number = _decimal(value)
    if number is None or number <= 0:
        raise ValueError("Hedef sıfırdan büyük bir sayı olmalı.")
    if mode == "profit":
        if number < PROFIT_MIN or number > PROFIT_MAX:
            raise ValueError("Kâr hedefi 0,01 ile 100.000 USDT arasında olmalı.")
        return {"mode": "profit", "value": float(number), "set_at": now_text}
    current = _decimal(mark)
    if current is None or current <= 0:
        raise ValueError("Güncel fiyat okunamadı; birkaç saniye sonra tekrar dene.")
    if number == current:
        raise ValueError("Hedef fiyat şu anki fiyatla aynı; hemen kapatmak için Kapat'ı kullan.")
    return {"mode": "price", "value": float(number), "side": "ABOVE" if number > current else "BELOW",
            "mark_at_set": float(current), "set_at": now_text}


def net_pnl(direction: str, mark: Decimal, entry: Decimal, quantity: Decimal, fee_rate: Decimal) -> Decimal:
    """Şimdi kapatılırsa giriş + çıkış komisyonu düşülmüş net sonuç (USDT)."""
    sign = Decimal(1) if direction == "LONG" else Decimal(-1)
    return (mark - entry) * quantity * sign - (entry + mark) * quantity * fee_rate


def reached(target: Any, direction: str, mark: Any, entry: Any, quantity: Any, fee_rate: Any) -> bool:
    """Hedefe ulaşıldı mı. Veri eksik ya da bozuksa False (kapatma yapılmaz; uydurma yok)."""
    if not isinstance(target, dict) or direction not in {"LONG", "SHORT"}:
        return False
    price = _decimal(mark)
    value = _decimal(target.get("value"))
    if price is None or price <= 0 or value is None or value <= 0:
        return False
    if target.get("mode") == "price":
        side = target.get("side")
        return (side == "ABOVE" and price >= value) or (side == "BELOW" and price <= value)
    if target.get("mode") == "profit":
        open_price, size, fee = _decimal(entry), _decimal(quantity), _decimal(fee_rate)
        if open_price is None or open_price <= 0 or size is None or size <= 0 or fee is None or fee < 0:
            return False
        return net_pnl(direction, price, open_price, abs(size), fee) >= value
    return False


def describe(target: dict[str, Any]) -> str:
    value = target.get("value")
    if target.get("mode") == "profit":
        return f"net {value} USDT kâr"
    return f"fiyat {value} ({'yukarı' if target.get('side') == 'ABOVE' else 'aşağı'})"
