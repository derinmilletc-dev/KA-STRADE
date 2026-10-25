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
import json
from collections import defaultdict
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
}
CONTROL = "CUR_80"
EXTENDED_RSI_BELOW = 30  # SHORT için "aşırı satım / dipten kovalama" göstergesi
MARKET_LABEL_PCT = 5.0   # BTC aylık getiri: >= +5 YUKARI, <= -5 ASAGI, arası YATAY


def protocol_sha256() -> str:
    payload = json.dumps({"candidates": CANDIDATES, "control": CONTROL, "extended_rsi_below": EXTENDED_RSI_BELOW,
                          "market_label_pct": MARKET_LABEL_PCT}, sort_keys=True, separators=(",", ":"))
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
            return apply_candidate(gate.variant, off)

        self.main.canonical_historical_decision = wrapper
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.main.canonical_historical_decision = self.original


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
