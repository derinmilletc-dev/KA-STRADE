"""Coinbase salt-okunur doğrulama aracı (E aşaması öncesi). EMİR VERMEZ: yalnızca GET.

Çalıştır (VPS'te, backend klasöründen):
  COINBASE_KEY_NAME='organizations/.../apiKeys/...' COINBASE_KEY_PEM_FILE=/yol/anahtar.pem python3 -m app.coinbase.doctor
Çıktıyı olduğu gibi paylaş: anahtar/gizli bilgi yazdırılmaz.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

os.environ.setdefault("COINBASE_ENABLED", "true")

from . import market, order_api, service  # noqa: E402
from .client import CoinbaseClient  # noqa: E402
from .errors import CoinbaseError  # noqa: E402
from .jwt_auth import load_private_key  # noqa: E402
from .trading_client import CoinbaseTradingClient  # noqa: E402


def line(ok: bool | None, text: str) -> None:
    print(f"[{'OK ' if ok else 'YOK' if ok is False else '?? '}] {text}")


async def run() -> int:
    name = os.environ.get("COINBASE_KEY_NAME", "").strip()
    pem_file = os.environ.get("COINBASE_KEY_PEM_FILE", "").strip()
    if not name or not pem_file:
        print("COINBASE_KEY_NAME ve COINBASE_KEY_PEM_FILE gerekli.")
        return 2
    with open(pem_file, encoding="utf-8") as handle:
        key = load_private_key(handle.read())
    client = CoinbaseClient(name, key)
    problems = 0

    async def step(title: str, work: Any) -> Any:
        nonlocal problems
        try:
            result = await work()
            return result
        except CoinbaseError as exc:
            problems += 1
            line(False, f"{title}: {exc.code} — {exc.message}")
        except Exception as exc:  # noqa: BLE001
            problems += 1
            line(False, f"{title}: beklenmeyen hata {type(exc).__name__}")
        return None

    account = await step("Hesap özeti", lambda: service.fetch_account(client))
    if account is not None:
        balance = account.get("balance") or {}
        line(True, f"Hesap okundu. Alım gücü alanı: {'var' if balance.get('futures_buying_power') else 'YOK'}; marjin seviyesi: {balance.get('worst_margin_level')}")
    positions = await step("Pozisyonlar", lambda: service.fetch_positions(client))
    if positions is not None:
        line(True, f"Açık pozisyon sayısı: {len(positions)}")

    products = await step("Ürün listesi", lambda: market.fetch_products(client))
    sample: str | None = None
    if products:
        with_root = [p for p in products if p.get("root")]
        line(bool(with_root), f"Ürün sayısı {len(products)}; dayanak varlık (contract_root_unit) olan: {len(with_root)} (Kais analizi buna bağlı)")
        line(None, "İlk 5 ürün: " + ", ".join(f"{p['product_id']}→{p.get('root')}" for p in products[:5]))
        sample = str(products[0]["product_id"])
    if sample:
        quote = await step("Anlık fiyat", lambda: market.fetch_quote(client, sample))
        if quote:
            line(True, f"{sample} alış/satış okundu (fark %{quote.get('spread_pct')})")
        trading = CoinbaseTradingClient(name, key)
        product = await step("Ürün kuralları", lambda: order_api.get_product(trading, sample))
        if product:
            line(True, f"{sample}: sözleşme boyutu {product['contract_size']}, miktar adımı {product['base_increment']}, fiyat adımı {product['price_increment']}, durum {product['status']}")
    print("\nSonuç:", "SORUN VAR, yukarıdaki [YOK] satırlarını gönder." if problems else "okuma tarafı sorunsuz. (Emir tarafı hâlâ küçük canlı testte doğrulanacak.)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
