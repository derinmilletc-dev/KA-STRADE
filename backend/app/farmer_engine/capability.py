"""KAIS FARMER — Binance yetenek kapısı (C0).

Farmer hedefi `TAKE_PROFIT_MARKET + reduceOnly + tam pozisyon miktarı`dır (koşullu/algo uç). Bu davranışın GERÇEK Binance'te
doğrulanması bu ortamda yapılamadı (ağ, anahtar ve Testnet yok). Burada her madde için DÜRÜST durum tutulur:

    REPO_EVIDENCE  : aynı çağrı şekli repodaki üretim yollarında kullanılıyor (dosya/işlev gösterilir). Bu, Binance'in kabul ettiğinin
                     bu ortamda doğrulandığı anlamına GELMEZ; yalnız çağrı şeklinin repoyla tutarlı olduğunu söyler.
    UNVERIFIED     : repoda kanıt yok; gerçek Testnet/canlı denemeyle kapatılmadan production varsayımı yapılmaz.

KRİTİK KURAL: `live_entry_blockers()` boş dönmeden KAIS FARMER canlı giriş AÇMAZ. Kapı iki ayrı sunucu ayarına bağlıdır ve ikisi de
varsayılan KAPALIdır:
    KAISTRADE_FARMER_LIVE        — canlı Farmer'ın kendisi (varsayılan KAPALI)
    KAISTRADE_FARMER_TPM_VERIFIED — operatör, aşağıdaki UNVERIFIED maddelerini Testnet/küçük canlı denemeyle kapattığını beyan eder
Kod, UNVERIFIED maddeleri kendiliğinden "doğrulandı" yapmaz; operatör beyanı yalnızca kapıyı açar ve beyan kayda geçer.
Demo (gerçek para yok) bu kapıya tabi değildir.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

LIVE_ENV_FLAG = "KAISTRADE_FARMER_LIVE"
TP_MARKET_VERIFIED_ENV = "KAISTRADE_FARMER_TPM_VERIFIED"

REPO_EVIDENCE, UNVERIFIED = "REPO_EVIDENCE", "UNVERIFIED"


@dataclass(frozen=True)
class Capability:
    key: str
    question: str
    status: str
    evidence: str


CAPABILITIES: tuple[Capability, ...] = (
    Capability("endpoint_accepts", "POST /fapi/v1/algoOrder TAKE_PROFIT_MARKET kabul ediyor mu?", REPO_EVIDENCE,
               "v25_execution.post_algo (3006), original_v2_live TP1/TP3 kurulumu (~1186-1247)"),
    Capability("field_names", "algoType=CONDITIONAL, type, side, quantity, triggerPrice, clientAlgoId alan adları", REPO_EVIDENCE,
               "v25_execution (3556, 4133), original_v2_live (1186-1187); yanıt okuma: normalize_open_algo_rows (2990)"),
    Capability("reduce_only_with_quantity", "TAKE_PROFIT_MARKET'te quantity + reduceOnly=true kabul ediliyor mu?", REPO_EVIDENCE,
               "v25_execution TP1 (4133): quantity + reduceOnly; original_v2_live TP1 (1247)"),
    Capability("full_position_by_quantity", "quantity = TÜM pozisyon (reduceOnly) ile tam kapanış çalışıyor mu?", UNVERIFIED,
               "Repoda tam kapanış hedefi closePosition ile (v25_execution 4157); Farmer kararı quantity+reduceOnly. Kısmi miktar kanıtlı, "
               "tam miktar gerçek denemeyle doğrulanmadı."),
    Capability("close_position_rules", "closePosition gerekli mi / quantity ile birlikte yasak mı?", REPO_EVIDENCE,
               "v25_execution (3556-3560): closePosition ile quantity+reduceOnly ayrı dallar; Farmer closePosition KULLANMAZ"),
    Capability("working_type_mark_price", "workingType=MARK_PRICE gönderiliyor", REPO_EVIDENCE,
               "v25_execution (3556), original_v2_live (1187), grid adaptörü stop (live_adapter.place_order)"),
    Capability("price_protect_effect", "priceProtect=TRUE'nun tetik üzerindeki etkisi", UNVERIFIED,
               "Repo parametreyi gönderiyor ama etkisini ölçen/okuyan kod yok."),
    Capability("triggered_record_shape", "Tetik sonrası algo kaydı (algoStatus değerleri) nasıl görünüyor?", UNVERIFIED,
               "Grid adaptörü TRIGGERED/FINISHED/EXECUTED/FILLED varsayıyor; README bunu 'gerçek borsada denenmedi' diye işaretliyor."),
    Capability("actual_order_link", "algoId → actualOrderId → gerçek emir ilişkisi", REPO_EVIDENCE,
               "original_v2_live (1268-1280): GET /fapi/v1/algoOrder → actualOrderId → GET /fapi/v1/order (FILLED, reduceOnly, executedQty)"),
    Capability("already_crossed_error", "Fiyat tetik seviyesini zaten geçmişse hangi hata gelir (-2021?)", UNVERIFIED,
               "Repoda TAKE_PROFIT_MARKET için bu hata işlenmiyor; yalnız Grid stop'u için varsayım var. Farmer motoru (C3) hata kodunu "
               "varsaymadan, hedef reddinde stop'u yerinde tutan güvenli davranışı tanımlayacak."),
    Capability("fill_read_after_trigger", "Tetik sonrası gerçek dolum (miktar/ortalama fiyat) nasıl okunur?", REPO_EVIDENCE,
               "actualOrderId ile GET /fapi/v1/order (executedQty, avgPrice) — original_v2_live (1271-1280); userTrades: verified_plan_pnl"),
)


def unverified() -> tuple[Capability, ...]:
    return tuple(item for item in CAPABILITIES if item.status == UNVERIFIED)


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def live_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Canlı KAIS FARMER varsayılan KAPALI."""
    return _truthy((os.environ if env is None else env).get(LIVE_ENV_FLAG))


def tp_market_declared_verified(env: Mapping[str, str] | None = None) -> bool:
    return _truthy((os.environ if env is None else env).get(TP_MARKET_VERIFIED_ENV))


def live_entry_blockers(env: Mapping[str, str] | None = None) -> list[str]:
    """Canlı giriş için kalan engeller (Türkçe, kullanıcıya/operatöre gösterilebilir). Boş liste = kapı açık."""
    reasons: list[str] = []
    if not live_enabled(env):
        reasons.append(f"Canlı KAIS FARMER kapalı (sunucuda {LIVE_ENV_FLAG}=1 ile açılır).")
    if not tp_market_declared_verified(env):
        pending = ", ".join(item.key for item in unverified())
        reasons.append("Gerçek Binance hedef emri (TAKE_PROFIT_MARKET) davranışı doğrulanmadı; doğrulanmamış maddeler: "
                       f"{pending}. Testnet/küçük canlı denemeden sonra operatör {TP_MARKET_VERIFIED_ENV}=1 ile beyan eder.")
    return reasons
