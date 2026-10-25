"""SHORT-filtre karşılaştırma çalışması: yalnız saf hesap yardımcıları.

Canlı strateji bu modülü İÇE AKTARMAZ; hiçbir canlı parametreyi değiştirmez. Aday kurallar yalnız çevrimdışı
araştırmada, mevcut Stage 5 önbelleğindeki "filtre KAPALI" kararlarının üstüne uygulanır:

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
    """Reddedilen girişin, motorun beklediği 'giriş yok' biçimi (compact_decision ile aynı alanlar)."""
    return {"decision": "WAIT", "signal_timestamp": decision.get("signal_timestamp"), "entry_eligible": False,
            "reason": "QUALITY_OR_MTF_GATE", "reasons": ["CANDIDATE_FILTER"]}


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


def build_union(base_rows: list[dict[str, Any]], variants: dict[str, list[dict[str, Any]]],
                complete: Callable[[dict[str, Any]], bool]) -> tuple[list[dict[str, Any]], list[bool], dict[str, list[bool]], dict[str, list[float | None]]]:
    """Bootstrap için ortak gözlem listesi: her sinyal bir kez; kontrol ve her aday için üyelik ve R değeri."""
    base = {row["signal_id"]: row for row in base_rows}
    maps = {name: {row["signal_id"]: row for row in rows} for name, rows in variants.items()}
    identifiers = list(dict.fromkeys([*base, *(i for rows in maps.values() for i in rows)]))
    union = []
    for identifier in identifiers:
        row = base.get(identifier)
        if row is None or not complete(row):
            row = next((maps[name][identifier] for name in maps if identifier in maps[name]), row)
        union.append(row)
    base_mask = [i in base and complete(base[i]) for i in identifiers]
    masks = {name: [i in maps[name] and complete(maps[name][i]) for i in identifiers] for name in maps}
    values = {name: [maps[name][i]["net_r"] if keep else None for i, keep in zip(identifiers, masks[name])]
              for name in maps}
    return union, base_mask, masks, values


def _fmt(value: Any, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}" if isinstance(value, (int, float)) else str(value)


def render_report(results: dict[str, Any]) -> str:
    """Düz Türkçe özet; ayrıntı results.json içinde. Hiçbir yerde 'kazanan' ilan edilmez."""
    lines = ["# SHORT filtresi karşılaştırması", "",
             "**Bu rapor bir karar değil, bir ölçümdür. Hiçbir aday 'kazanan' ilan edilmez; canlı parametre değişmez.**", ""]
    parity = results.get("reference_parity", {})
    lines += ["## Önce güvenilirlik kontrolleri", "",
              f"- Aday kuralların mevcut canlı filtreyi yeniden üretmesi (CUR_80 = ON): **{results.get('decision_parity', 'ATLANDI')}**",
              f"- Eski deney sonuçlarıyla uyum (CUR_80 ve OFF): **{parity.get('status', 'ATLANDI')}**"]
    for note in parity.get("notes", []):
        lines.append(f"  - {note}")
    lines.append("- `ATLANDI` başarılı sayılmaz. `BAŞARISIZ` ise aşağıdaki sayılara güvenilmez.")
    lines.append("")
    for ordering, block in results.get("orderings", {}).items():
        lines += [f"## Sıralama: {ordering}", "",
                  "| Aday | İşlem | Ort. R | Kâr faktörü | Kazanma | Maks. düşüş $ | SHORT işlem | SHORT ort. R | LONG işlem | LONG ort. R | Örneklem |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
        for name, item in block["variants"].items():
            s, ls = item["summary"], item["summary"].get("long_short", {})
            sh, lo = ls.get("SHORT", {}), ls.get("LONG", {})
            lines.append(f"| {name} | {s.get('trade_count')} | {_fmt(s.get('net_expectancy_r'))} | {_fmt(s.get('profit_factor'), 2)} | "
                         f"{_fmt(s.get('win_rate'), 2)} | {_fmt(s.get('max_drawdown_usdt'), 2)} | {sh.get('trade_count', '—')} | "
                         f"{_fmt(sh.get('net_expectancy_r'))} | {lo.get('trade_count', '—')} | {_fmt(lo.get('net_expectancy_r'))} | "
                         f"{s.get('sample_status')} |")
        lines += ["", "### Kontrole (CUR_80) göre eklenen / elenen işlemler", "",
                  "| Aday | Eklenen | Elenen | Eklenenlerin ort. R | Elenenlerin ort. R | Ort. R farkı (aday − kontrol) |",
                  "|---|---:|---:|---:|---:|---:|"]
        for name, item in block["variants"].items():
            change = item.get("changes_vs_control")
            if change is None:
                continue
            contrast = item.get("contrast") or {}
            lines.append(f"| {name} | {change['added'].get('trade_count')} | {change['removed'].get('trade_count')} | "
                         f"{_fmt(change['added'].get('net_expectancy_r'))} | {_fmt(change['removed'].get('net_expectancy_r'))} | "
                         f"{_fmt(contrast.get('mean_r_difference'))} |")
        lines += ["", "### Dönemlere göre (TRAIN / VALIDATION) ve yöne göre ortalama R", "",
                  "| Aday | Dönem | Yön | İşlem | Ort. R | Örneklem |", "|---|---|---|---:|---:|---|"]
        for name, item in block["variants"].items():
            for period in ("TRAIN", "VALIDATION"):
                summary = (item.get("periods") or {}).get(period) or {}
                for direction in ("SHORT", "LONG"):
                    m = (summary.get("long_short") or {}).get(direction, {})
                    lines.append(f"| {name} | {period} | {direction} | {m.get('trade_count', '—')} | "
                                 f"{_fmt(m.get('net_expectancy_r'))} | {summary.get('sample_status', '—')} |")
        lines += ["", "### Piyasa dönemine göre (BTC aylık getirisi: YUKARI ≥ +5 %, ASAGI ≤ −5 %, aksi YATAY)", "",
                  "| Aday | Piyasa | Yön | İşlem | Ort. R |", "|---|---|---|---:|---:|"]
        for name, item in block["variants"].items():
            for label, directions in (item.get("market_regime") or {}).items():
                for direction, m in directions.items():
                    lines.append(f"| {name} | {label} | {direction} | {m.get('trade_count')} | {_fmt(m.get('net_expectancy_r'))} |")
        lines.append("")
    lines += ["## Sınırlamalar", ""] + [f"- {text}" for text in results.get("limitations", [])]
    return "\n".join(lines) + "\n"
