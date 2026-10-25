"""SHORT-filtre karşılaştırma çalışması: yalnız saf hesap yardımcıları.

Canlı strateji bu modülü İÇE AKTARMAZ; hiçbir canlı parametreyi değiştirmez. Aday kurallar yalnız çevrimdışı
araştırmada, "filtre KAPALI" kararların üstüne uygulanır:

* OFF kararı, SHORT alignment filtresi kapatılmış hâliyle bütün giriş adaylarını içerir (ON kümesinin üst kümesi).
* Her aday kural, bu kümeden bazı SHORT (veya LONG) girişlerini ELER; hiçbir giriş EKLEMEZ.
* CUR_80 kontrol kuralı, mevcut canlı filtreyi (alignment >= 80 -> reddet) birebir yeniden üretmek ZORUNDADIR.
  Üretemezse (parity) çalışma sonuç vermez.

Bu modül ağ, borsa istemcisi veya fastapi gerektirmez.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable

# Aday kurallar sonuçlar görülmeden ÖNCE burada sabitlenir. Değiştirmek rapordaki protokol özetini değiştirir.
CANDIDATES: dict[str, dict[str, Any]] = {
    "CUR_80": {
        "label": "Mevcut canlı filtre (kontrol)",
        "short_block_at": 80, "long_block_at": None, "short_only_if_extended": False,
    },
    "OFF": {
        "label": "SHORT filtresi kapalı (eski deneyin tekrarı)",
        "short_block_at": None, "long_block_at": None, "short_only_if_extended": False,
    },
    "THR_75": {
        "label": "SHORT eşiği 75 (daha sıkı)",
        "short_block_at": 75, "long_block_at": None, "short_only_if_extended": False,
    },
    "THR_85": {
        "label": "SHORT eşiği 85 (daha gevşek)",
        "short_block_at": 85, "long_block_at": None, "short_only_if_extended": False,
    },
    "THR_90": {
        "label": "SHORT eşiği 90 (çok gevşek)",
        "short_block_at": 90, "long_block_at": None, "short_only_if_extended": False,
    },
    "EXT_ONLY_80": {
        "label": "SHORT yalnız alignment >= 80 VE hareket aşırı uzamışsa reddedilir",
        "short_block_at": 80, "long_block_at": None, "short_only_if_extended": True,
    },
    "SYM_80": {
        "label": "Simetrik: LONG ve SHORT için alignment >= 80 reddedilir",
        "short_block_at": 80, "long_block_at": 80, "short_only_if_extended": False,
    },
    # --- Yeni adaylar (bu PR). İlk 7 sonucun raporu görüldükten SONRA tasarlandı; eşikleri YOK: trap_score'un kendi
    # sabitleri aynen kullanılır. Yalnız iki aday vardır; sonuca göre ayar yapılmaz. ---
    "EXHAUST_80": {
        "label": "SHORT yalnız alignment >= 80 VE en az bir tükenme işareti varsa reddedilir (güven puanı tek başına ret sebebi değil)",
        "short_block_at": 80, "long_block_at": None, "short_only_if_extended": False, "short_exhaustion": True,
    },
    "EXHAUST_ANY": {
        "label": "SHORT, alignment'a bakılmadan, en az bir tükenme işareti varsa reddedilir",
        "short_block_at": 0, "long_block_at": None, "short_only_if_extended": False, "short_exhaustion": True,
    },
}
CONTROL = "CUR_80"
EXTENDED_RSI_BELOW = 30  # SHORT için "aşırı satım / dipten kovalama" göstergesi
MARKET_LABEL_PCT = 5.0   # BTC aylık getiri: >= +5 YUKARI, <= -5 ASAGI, arası YATAY

# Tükenme (dipten kovalama) işaretleri. Eşikler analysis.py'deki trap_score SHORT sabitleriyle AYNIDIR (yeni serbest
# parametre yok): uzama > 2 ATR, RSI < 28, destek mesafesi < 1 ATR, alt fitil/gövde oranı > 1.5.
TRAP_BASE = 10
TRAP_WEIGHTS = {"low_volume": 24, "lower_wick": 20, "extension": 18, "near_support": 15, "rsi_low": 10}
EXHAUSTION_FLAGS = ("extension", "rsi_low", "near_support", "lower_wick")  # düşük hacim tükenme değil, zayıf hareket
RSI_LOW_BELOW = 28.0
SUPPORT_ATR_BELOW = 1.0
TRAP_GATE_MAX = 35  # main.canonical_historical_decision: trap_score <= 35


def protocol_sha256() -> str:
    payload = json.dumps({"candidates": CANDIDATES, "control": CONTROL, "extended_rsi_below": EXTENDED_RSI_BELOW,
                          "market_label_pct": MARKET_LABEL_PCT, "trap_weights": TRAP_WEIGHTS,
                          "exhaustion_flags": EXHAUSTION_FLAGS, "rsi_low_below": RSI_LOW_BELOW,
                          "support_atr_below": SUPPORT_ATR_BELOW}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _alignment(decision: dict[str, Any]) -> float | None:
    mtf = decision.get("mtf")
    value = mtf.get("alignment") if isinstance(mtf, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _is_extended(decision: dict[str, Any]) -> bool:
    """15m girişinin aşırı uzamış olduğu: fomo_risk YÜKSEK (fiyat EMA20'den > 2 ATR) ya da RSI çok düşük."""
    analysis = decision.get("analysis") or {}
    radar = analysis.get("radar") or {}
    rsi = analysis.get("rsi")
    return radar.get("fomo_risk") == "YÜKSEK" or (isinstance(rsi, (int, float)) and rsi < EXTENDED_RSI_BELOW)


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def short_flags(decision: dict[str, Any]) -> dict[str, bool] | None:
    """SHORT girişinin trap_score bileşenlerini, karar çıktısından YENİDEN kurar.

    extension / rsi_low / near_support / low_volume doğrudan alanlardan hesaplanır. Alt fitil oranı karar çıktısında
    yoktur; trap_score'dan kalan = trap_score - 10 - (diğer bayrakların ağırlıkları) ise ve yalnız 0 ya da 20 ise
    çıkarılır (20 = alt fitil). Başka bir kalan çıkarsa (alan eksik, karar SHORT değil, beklenmeyen biçim) None döner;
    çağıran None'ı "bilinmiyor" sayar ve ENGELLEMEZ (sessizce yanlış sınıflamaz)."""
    analysis = decision.get("analysis")
    if not isinstance(analysis, dict):
        return None
    radar = analysis.get("radar") or {}
    trap = _num(radar.get("trap_score"))
    rsi, atr, entry = _num(analysis.get("rsi")), _num(analysis.get("atr")), _num(analysis.get("entry"))
    support, volume = _num(analysis.get("support")), _num(analysis.get("volume_ratio"))
    if None in (trap, rsi, atr, entry, support, volume) or "fomo_risk" not in radar:
        return None
    flags = {
        "extension": radar["fomo_risk"] == "YÜKSEK",
        "rsi_low": rsi < RSI_LOW_BELOW,
        "near_support": (entry - support) / max(atr, 0.00000001) < SUPPORT_ATR_BELOW,
        "low_volume": volume < 0.9,
    }
    remainder = trap - TRAP_BASE - sum(TRAP_WEIGHTS[name] for name, on in flags.items() if on)
    if remainder == 0:
        flags["lower_wick"] = False
    elif remainder == TRAP_WEIGHTS["lower_wick"]:
        flags["lower_wick"] = True
    else:
        return None
    return flags


def has_exhaustion(decision: dict[str, Any]) -> bool | None:
    flags = short_flags(decision)
    return None if flags is None else any(flags[name] for name in EXHAUSTION_FLAGS)


def trap_gate_pairs() -> dict[str, Any]:
    """trap_score <= 35 kapısının SHORT için hangi bayrak kombinasyonlarını zaten elediğini hesaplar (sabit yazı değil).

    Kapıyı geçebilen tek-bayraklı durumlar ve geçen çiftler listelenir. Yeni tükenme kuralının, kapının zaten elediği
    durumlarla çakıştığı yer bu hesaptan görülür."""
    names = list(TRAP_WEIGHTS)
    passing, failing = [], []
    for size in range(0, len(names) + 1):
        for combo in itertools.combinations(names, size):
            score = TRAP_BASE + sum(TRAP_WEIGHTS[name] for name in combo)
            (passing if score <= TRAP_GATE_MAX else failing).append(list(combo))
    return {"passes_trap_gate": passing, "rejected_by_trap_gate": failing}


def blocks(name: str, decision: dict[str, Any]) -> bool:
    """True: aday kural bu (uygun) girişi reddeder. Uygun olmayan karar hiçbir zaman 'reddedilmez' (zaten giriş yok)."""
    rule = CANDIDATES[name]
    if not decision.get("entry_eligible"):
        return False
    side = decision.get("decision")
    alignment = _alignment(decision)
    if side == "SELL":
        limit = rule["short_block_at"]
        if limit is None or alignment is None or alignment < limit:
            return False
        if rule.get("short_exhaustion"):
            return has_exhaustion(decision) is True  # bilinmiyorsa (None) ENGELLEME
        return _is_extended(decision) if rule["short_only_if_extended"] else True
    if side == "BUY":
        limit = rule["long_block_at"]
        return limit is not None and alignment is not None and alignment >= limit
    return False


def blocked_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Reddedilen girişin biçimi: canlı filtrenin reddettiği kararın sıkıştırılmış hâliyle (Dataset.canonical) birebir aynı
    alanlar ve değerler. Böylece CUR_80 kontrolü, kayıtlı sayımlardaki ret nedenlerini de aynen üretir."""
    return {"decision": "WAIT", "signal_timestamp": decision.get("signal_timestamp"), "entry_eligible": False,
            "reason": "QUALITY_OR_MTF_GATE", "reasons": ["QUALITY_OR_MTF_GATE"]}


def apply_candidate(name: str, off_decision: dict[str, Any]) -> dict[str, Any]:
    return blocked_decision(off_decision) if blocks(name, off_decision) else off_decision


def parity_mismatches(pairs: Iterable[tuple[Any, dict[str, Any], dict[str, Any]]]) -> list[Any]:
    """CUR_80, OFF kararlarına uygulanınca canlı (ON) kararla aynı giriş kararını vermeli.

    pairs: (anahtar, on_karar, off_karar). Uyuşmayan anahtarların listesi döner; boşsa parity sağlanmıştır."""
    bad = []
    for key, on, off in pairs:
        candidate = apply_candidate(CONTROL, off)
        if bool(candidate.get("entry_eligible")) != bool(on.get("entry_eligible")) \
                or candidate.get("decision") != on.get("decision"):
            bad.append(key)
    return bad


def month_returns(rows: list[dict[str, Any]], month_of: Callable[[int], str]) -> dict[str, float]:
    """Aylık getiri (yüzde): ayın ilk mumunun açılışı -> son mumunun kapanışı."""
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    for row in rows:
        month = month_of(int(row["time"]))
        first.setdefault(month, float(row["open"]))
        last[month] = float(row["close"])
    return {month: (last[month] / first[month] - 1) * 100 for month in first if first[month] > 0}


def market_label(return_pct: float | None) -> str:
    if return_pct is None:
        return "BILINMIYOR"
    if return_pct >= MARKET_LABEL_PCT:
        return "YUKARI"
    if return_pct <= -MARKET_LABEL_PCT:
        return "ASAGI"
    return "YATAY"


def breakdown(rows: list[dict[str, Any]], label_of: Callable[[dict[str, Any]], str],
              metrics_fn: Callable[[list[dict[str, Any]]], dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Etiket (piyasa dönemi) x yön kırılımı."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(label_of(row), row["direction"])].append(row)
    result: dict[str, dict[str, Any]] = {}
    for (label, direction), items in sorted(groups.items()):
        result.setdefault(label, {})[direction] = metrics_fn(items)
    return result


def signal_changes(variant_rows: list[dict[str, Any]], base_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aday ile kontrol arasındaki eklenen/elenen işlemler, tek tek."""
    current = {row["signal_id"]: row for row in variant_rows}
    previous = {row["signal_id"]: row for row in base_rows}
    output = []
    for kind, source, other in (("EKLENDI", current, previous), ("ELENDI", previous, current)):
        for identifier, row in source.items():
            if identifier not in other:
                output.append({"degisim": kind, "signal_id": identifier, "symbol": row.get("symbol"),
                               "direction": row.get("direction"), "opened_at": row.get("opened_at"),
                               "closed_at": row.get("closed_at"), "net_r": row.get("net_r"),
                               "net_pnl": row.get("net_pnl"), "status": row.get("status")})
    return sorted(output, key=lambda item: (str(item["opened_at"]), item["signal_id"]))


def compact_off(result: dict[str, Any]) -> dict[str, Any]:
    """Filtre-KAPALI karar sonucunu önbellek için küçültür (Dataset.canonical ile aynı kurallar)."""
    if not result.get("entry_eligible"):
        return {key: result.get(key) for key in ("decision", "signal_timestamp", "entry_eligible", "reason", "reasons")}
    analysis = {key: value for key, value in (result.get("analysis") or {}).items() if key != "series"}
    return {**result, "analysis": analysis}


class VariantGate:
    """`main_module.canonical_historical_decision` fonksiyonunu sarar.

    'Filtre KAPALI' karar (gate_disabled bağlamı içinde) her anahtar için bir kez hesaplanır ve önbelleğe alınır;
    seçili aday kural, her çağrıda bu önbellekteki karara uygulanır. Önbellek yalnız hesap bağlamı olmayan (policy ve
    account_context verilmeyen) saf tarihsel kararlar içindir; bağlamlı çağrılar sarmalayıcıdan geçmeden doğrudan
    gerçek fonksiyona gider. `variant` None iken de doğrudan gerçek fonksiyon çalışır."""

    def __init__(self, main_module: Any, gate_disabled: Callable[[], Any]) -> None:
        self.main = main_module
        self.gate_disabled = gate_disabled
        self.variant: str | None = None
        self.memo: dict[tuple, dict[str, Any]] = {}
        self.features: dict[tuple[str, int], dict[str, Any]] = {}  # (sembol, karar zamanı) -> uygun giriş özellikleri
        self.phase: str | None = None  # CLI, her dönem başında ayarlar; yalnız özellik kaydına etiket olur
        self.calls_to_real = 0
        self.original: Callable[..., dict[str, Any]] | None = None

    def __enter__(self) -> "VariantGate":
        self.original = self.main.canonical_historical_decision
        gate = self

        def wrapper(symbol, candles_by_timeframe, decision_time, **kwargs):
            if gate.variant is None or kwargs.get("policy") is not None or kwargs.get("account_context") is not None:
                return gate.original(symbol, candles_by_timeframe, decision_time, **kwargs)
            override = kwargs.get("historical_policy_override") or {}
            key = (symbol, int(decision_time), tuple(sorted(override.items())), tuple(kwargs.get("required_intervals") or ()))
            off = gate.memo.get(key)
            if off is None:
                with gate.gate_disabled():
                    gate.calls_to_real += 1
                    off = compact_off(gate.original(symbol, candles_by_timeframe, decision_time, **kwargs))
                gate.memo[key] = off
                row = feature_row(off)
                if row is not None:
                    gate.features[(symbol, int(decision_time))] = {**row, "phase": gate.phase}
            return apply_candidate(gate.variant, off)

        self.main.canonical_historical_decision = wrapper
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.main.canonical_historical_decision = self.original


def feature_row(off: dict[str, Any]) -> dict[str, Any] | None:
    """Uygun (giriş yapılabilir) bir filtre-KAPALI kararın çakışma analizi için özet özellikleri."""
    if not off.get("entry_eligible") or off.get("decision") not in {"BUY", "SELL"}:
        return None
    analysis = off.get("analysis") or {}
    radar = analysis.get("radar") or {}
    row: dict[str, Any] = {"side": "SHORT" if off["decision"] == "SELL" else "LONG", "alignment": _alignment(off),
                           "confidence": analysis.get("confidence"), "trap_score": radar.get("trap_score"),
                           "breakout_quality": radar.get("breakout_quality")}
    if off["decision"] == "SELL":
        row["flags"] = short_flags(off)
    return row


def simple_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Küçük, bağımlılıksız özet: işlem sayısı, ortalama R, kazanma oranı, kâr faktörü ($)."""
    if not rows:
        return {"N": 0, "mean_net_r": None, "win_rate": None, "usdt_pf": None}
    gains = sum(max(float(r["net_pnl"]), 0.0) for r in rows)
    losses = -sum(min(float(r["net_pnl"]), 0.0) for r in rows)
    return {"N": len(rows), "mean_net_r": sum(float(r["net_r"]) for r in rows) / len(rows),
            "win_rate": sum(1 for r in rows if float(r["net_r"]) > 0) / len(rows),
            "usdt_pf": gains / losses if losses > 0 else None}


def _mean(values: list[Any]) -> float | None:
    numbers = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(numbers) / len(numbers) if numbers else None


def overlap_summary(features: dict[tuple[str, int], dict[str, Any]], trades: list[dict[str, Any]],
                    phase: str, blocked_at: float = 80.0) -> dict[str, Any]:
    """Mevcut filtrenin (alignment >= blocked_at) reddettiği SHORT'lar ile tükenme işaretleri / trap_score çakışması.

    * signal: uygun (filtre-KAPALI) SHORT kararları — sinyal düzeyi, işlem değil.
    * trades: filtre-KAPALI (OFF) işlemleri, (sembol, açılış zamanı) ile özelliklere bağlanır. Bağlanamayan işlem
      sayısı ayrıca verilir (gizlenmez); 0 değilse trade düzeyi kırılımlara dikkatle bakılmalıdır."""
    shorts = [row for row in features.values() if row.get("phase") == phase and row["side"] == "SHORT"]
    is_blocked = lambda row: row["alignment"] is not None and row["alignment"] >= blocked_at  # noqa: E731
    blocked = [row for row in shorts if is_blocked(row)]
    rest = [row for row in shorts if not is_blocked(row)]

    def share(rows: list[dict[str, Any]], name: str | None = None) -> dict[str, Any]:
        known = [r for r in rows if r.get("flags") is not None]
        hit = [r for r in known if (any(r["flags"][f] for f in EXHAUSTION_FLAGS) if name is None else r["flags"][name])]
        return {"N": len(rows), "bilinmeyen": len(rows) - len(known), "isaretli": len(hit),
                "oran": len(hit) / len(known) if known else None}

    signal = {
        "uygun_short": len(shorts), "mevcut_filtrenin_reddettigi": len(blocked),
        "reddedilenlerde_herhangi_isaret": share(blocked), "reddedilmeyenlerde_herhangi_isaret": share(rest),
        "reddedilenlerde_isaret_bazinda": {name: share(blocked, name) for name in (*EXHAUSTION_FLAGS, "low_volume")},
        "reddedilenlerde_trap_dagilimi": dict(sorted(
            (str(k), v) for k, v in Counter(r["trap_score"] for r in blocked).items())),
        "ortalama_kirilim_kalitesi": {"reddedilen": _mean([r.get("breakout_quality") for r in blocked]),
                                      "reddedilmeyen": _mean([r.get("breakout_quality") for r in rest])},
        "ortalama_guven": {"reddedilen": _mean([r.get("confidence") for r in blocked]),
                           "reddedilmeyen": _mean([r.get("confidence") for r in rest])},
    }
    matched, unmatched, groups = 0, 0, defaultdict(list)
    for trade in trades:
        feature = features.get((trade["symbol"], int(trade["opened_at_epoch"])))
        if feature is None or feature.get("phase") != phase:
            unmatched += 1
            continue
        matched += 1
        high = feature["alignment"] is not None and feature["alignment"] >= blocked_at
        side = "alignment>=%g" % blocked_at if high else "alignment<%g" % blocked_at
        if trade["direction"] == "SHORT":
            flags = feature.get("flags")
            mark = "bilinmiyor" if flags is None else "isaret_var" if any(flags[f] for f in EXHAUSTION_FLAGS) else "isaret_yok"
            groups[("SHORT", side, mark)].append(trade)
        else:
            groups[("LONG", side, "-")].append(trade)
    trade_level = {"|".join(key): simple_stats(rows) for key, rows in sorted(groups.items())}
    return {"signal_duzeyi": signal, "islem_duzeyi": trade_level,
            "baglanan_islem": matched, "baglanamayan_islem": unmatched}


BOOTSTRAP_ITERATIONS = 4000
BOOTSTRAP_SEED = 20261025


def bootstrap_diff(candidate: list[float], base: list[float], iterations: int = BOOTSTRAP_ITERATIONS,
                   seed: int = BOOTSTRAP_SEED) -> dict[str, Any] | None:
    """Ortalama R farkı (aday - taban) için %95 aralık. İki küme bağımsız yeniden örneklenir (işlem kümeleri farklı
    olduğu için eşleştirme yok); sabit tohum, aynı girdi aynı sonucu verir. Kümelerden biri boşsa None."""
    if not candidate or not base:
        return None
    rng = random.Random(seed)
    diffs = []
    for _ in range(iterations):
        a = sum(rng.choice(candidate) for _ in candidate) / len(candidate)
        b = sum(rng.choice(base) for _ in base) / len(base)
        diffs.append(a - b)
    diffs.sort()
    point = sum(candidate) / len(candidate) - sum(base) / len(base)
    return {"diff": point, "lo": diffs[int(0.025 * iterations)], "hi": diffs[int(0.975 * iterations) - 1],
            "n_candidate": len(candidate), "n_base": len(base)}


def evidence_verdict(per_phase: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Önceden sabit ölçüt: aday, tabandan YALNIZCA şu koşulda 'daha iyi KANITLANDI' sayılır:
    iki dönemde de (TRAIN ve VALIDATION) ortalama R farkı pozitif VE farkın %95 aralığının alt sınırı > 0.
    Aksi hâlde 'KANITLANMADI' (bu, 'daha kötü' demek değildir; 'bu veriyle ayırt edilemiyor' demektir)."""
    reasons = []
    for phase in ("TRAIN", "VALIDATION"):
        item = per_phase.get(phase)
        if item is None:
            reasons.append(f"{phase}: karşılaştırılacak işlem yok")
        elif not item["lo"] > 0:
            reasons.append(f"{phase}: fark {item['diff']:+.3f} R, %95 aralık [{item['lo']:+.3f}; {item['hi']:+.3f}] 0'ı içeriyor ya da altında")
    return {"karar": "KANITLANMADI" if reasons else "KANITLANDI", "nedenler": reasons}


def build_evidence(names: list[str], net_r: dict[str, dict[str, list[float]]], phases: list[str]) -> dict[str, Any]:
    """Tükenme adayları için, her taban (CUR_80 ve çalıştırıldıysa OFF) karşısında önceden sabit kanıt ölçütü.

    net_r: {aday: {dönem: [işlem net R'leri]}}. Yalnız 'short_exhaustion' adayları değerlendirilir."""
    evidence: dict[str, Any] = {}
    for name in names:
        if not CANDIDATES[name].get("short_exhaustion"):
            continue
        evidence[name] = {}
        for base in (CONTROL, "OFF"):
            if base not in names:
                continue
            per_phase = {phase: bootstrap_diff(net_r[name][phase], net_r[base][phase]) for phase in phases}
            evidence[name][base] = {**evidence_verdict(per_phase), "farklar": per_phase}
    return evidence


def build_overlap(features: dict[tuple[str, int], dict[str, Any]], trades_by_phase: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {"trap_kapisi": trap_gate_pairs(),
            "donemler": {phase: overlap_summary(features, trades, phase) for phase, trades in trades_by_phase.items()}}


def render_overlap(overlap: dict[str, Any]) -> list[str]:
    lines = ["## Çakışma analizi: tuzak puanı kapısı ve tükenme işaretleri", ""]
    structure = overlap.get("trap_kapisi", {})
    lines += ["Tuzak puanı kapısı (trap_score <= 35) SHORT için hangi uyarı kombinasyonlarını zaten geçiriyor "
              "(hesaplanmış, elle yazılmamış):", ""]
    for combo in structure.get("passes_trap_gate", []):
        lines.append(f"- geçer: {' + '.join(combo) if combo else '(hiç uyarı yok)'}")
    lines += ["", f"Geri kalan {len(structure.get('rejected_by_trap_gate', []))} kombinasyon kapıda zaten reddedilir. "
              "Yani yeni tükenme kuralı yalnız yukarıdaki tek-uyarılı (ve destek+RSI çifti) durumlarda ek bir şey yapabilir.", ""]
    for phase, item in overlap.get("donemler", {}).items():
        signal, trades = item["signal_duzeyi"], item["islem_duzeyi"]
        lines += [f"### {phase}", "",
                  f"- Uygun SHORT sinyali (filtre kapalıyken): {signal['uygun_short']}; mevcut filtrenin reddettiği: {signal['mevcut_filtrenin_reddettigi']}",
                  f"- Reddedilenlerde herhangi bir tükenme işareti: {signal['reddedilenlerde_herhangi_isaret']['isaretli']} / "
                  f"{signal['reddedilenlerde_herhangi_isaret']['N']} (oran {_fmt(signal['reddedilenlerde_herhangi_isaret']['oran'], 2)})",
                  f"- Reddedilmeyenlerde: oran {_fmt(signal['reddedilmeyenlerde_herhangi_isaret']['oran'], 2)}",
                  "- Reddedilenlerde işaret bazında: " + ", ".join(
                      f"{name} {v['isaretli']}" for name, v in signal["reddedilenlerde_isaret_bazinda"].items()),
                  f"- Ortalama kırılım kalitesi: reddedilen {_fmt(signal['ortalama_kirilim_kalitesi']['reddedilen'], 1)}, "
                  f"reddedilmeyen {_fmt(signal['ortalama_kirilim_kalitesi']['reddedilmeyen'], 1)}; ortalama güven: reddedilen "
                  f"{_fmt(signal['ortalama_guven']['reddedilen'], 1)}, reddedilmeyen {_fmt(signal['ortalama_guven']['reddedilmeyen'], 1)}",
                  f"- İşlemler özelliklere bağlandı: {item['baglanan_islem']} bağlandı, {item['baglanamayan_islem']} bağlanamadı", "",
                  "| Grup (yön / alignment / işaret) | İşlem | Ort. R | Kâr faktörü | Örneklem |", "|---|---:|---:|---:|---|"]
        for key, m in trades.items():
            lines.append(f"| {key} | {m['N']} | {_fmt(m['mean_net_r'])} | {_fmt(m['usdt_pf'], 2)} | {_sample(m['N'])} |")
        lines.append("")
    return lines


def _fmt(value: Any, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}" if isinstance(value, (int, float)) else str(value)


def _sample(n: Any) -> str:
    return "—" if not isinstance(n, int) else "YETERSIZ_ORNEKLEM" if n < 60 else "yeterli"


def render_report(results: dict[str, Any]) -> str:
    """Düz Türkçe özet; ayrıntı results.json içinde. Hiçbir yerde 'kazanan' ilan edilmez."""
    anchors = results.get("anchors", {})
    lines = ["# SHORT filtresi karşılaştırması (Kais Original V2 hattı)", "",
             "**Bu rapor bir karar değil, bir ölçümdür. Hiçbir aday 'kazanan' ilan edilmez; canlı parametre değişmez.**", "",
             "## Önce güvenilirlik kontrolleri", "",
             f"- Kontrol kuralı (CUR_80) eski kayıtlı sayımlarla (run3) birebir aynı mı: **{anchors.get('count_parity', 'ATLANDI')}**",
             f"- Kontrol sonucu eski ön tarama sonucuyla (işlem sayısı, ortalama R) aynı mı: **{anchors.get('prescreen_parity', 'ATLANDI')}**"]
    for note in anchors.get("notes", []):
        lines.append(f"  - {note}")
    lines += ["- `ATLANDI` başarılı sayılmaz. `BAŞARISIZ` ise aşağıdaki sayılara güvenilmez.", ""]
    verdict = results.get("evidence")
    if verdict:
        lines += ["## Yeni adaylar gerçekten daha iyi mi? (önceden sabitlenmiş ölçüt)", "",
                  "Ölçüt: iki dönemde de ortalama R farkı pozitif ve farkın %95 aralığı 0'ın üstünde olmalı.", "",
                  "| Aday | Taban | Karar | Neden |", "|---|---|---|---|"]
        for name, bases in verdict.items():
            for base, item in bases.items():
                lines.append(f"| {name} | {base} | **{item['karar']}** | {'; '.join(item['nedenler']) or '—'} |")
        lines += ["", "`KANITLANMADI` = bu veriyle ayırt edilemiyor; yeni kuralın daha iyi olduğu söylenemez.", ""]
    overlap = results.get("overlap")
    if overlap:
        lines += render_overlap(overlap)
    for phase, variants in results.get("phases", {}).items():
        lines += [f"## Dönem: {phase}", "",
                  "| Aday | İşlem | Ort. R | Kâr faktörü | Kazanma | Maks. düşüş $ | Net $ | Ort. R %95 aralık | Örneklem |",
                  "|---|---:|---:|---:|---:|---:|---:|---|---|"]
        for name, item in variants.items():
            m = item["metrics"]
            interval = item.get("mean_r_95")
            text = "—" if not interval else f"[{_fmt(interval[0])}; {_fmt(interval[1])}]"
            lines.append(f"| {name} | {m.get('N')} | {_fmt(m.get('mean_net_r'))} | {_fmt(m.get('usdt_pf'), 2)} | "
                         f"{_fmt(m.get('win_rate'), 2)} | {_fmt(m.get('max_drawdown_usdt'), 2)} | "
                         f"{_fmt(m.get('total_net_pnl_usdt'), 2)} | {text} | {_sample(m.get('N'))} |")
        lines += ["", "### Yöne göre (LONG / SHORT ayrı)", "",
                  "| Aday | Yön | İşlem | Ort. R | Kâr faktörü | Örneklem |", "|---|---|---:|---:|---:|---|"]
        for name, item in variants.items():
            for direction in ("SHORT", "LONG"):
                d = (item.get("by_direction") or {}).get(direction, {})
                lines.append(f"| {name} | {direction} | {d.get('N', '—')} | {_fmt(d.get('mean_net_r'))} | "
                             f"{_fmt(d.get('usdt_pf'), 2)} | {_sample(d.get('N'))} |")
        lines += ["", "### Kontrole (CUR_80) göre eklenen / elenen işlemler", "",
                  "| Aday | Eklenen | Eklenenlerin ort. R | Elenen | Elenenlerin ort. R |", "|---|---:|---:|---:|---:|"]
        for name, item in variants.items():
            change = item.get("changes_vs_control")
            if change:
                lines.append(f"| {name} | {change['added']['N']} | {_fmt(change['added']['mean_net_r'])} | "
                             f"{change['removed']['N']} | {_fmt(change['removed']['mean_net_r'])} |")
        lines += ["", f"### Piyasa dönemine göre (BTC aylık getirisi: YUKARI ≥ +{MARKET_LABEL_PCT:g} %, ASAGI ≤ −{MARKET_LABEL_PCT:g} %, aksi YATAY)", "",
                  "| Aday | Piyasa | Yön | İşlem | Ort. R |", "|---|---|---|---:|---:|"]
        for name, item in variants.items():
            for label, directions in (item.get("market_regime") or {}).items():
                for direction, m in directions.items():
                    lines.append(f"| {name} | {label} | {direction} | {m.get('N')} | {_fmt(m.get('mean_net_r'))} |")
        lines.append("")
    lines += ["## Sınırlamalar", ""] + [f"- {text}" for text in results.get("limitations", [])]
    return "\n".join(lines) + "\n"
