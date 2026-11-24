"""Puanlama raporu (yalnız okur, hiçbir karara dokunmaz): sinyal günlüğündeki gerçek hipotetik sonuçlara (maliyet sonrası R) bakarak
güven puanının, eşiklerin ve Kais Long gölge puanının gerçekte işe yarayıp yaramadığını ölçer.

Sınırlar (rapor da bunları yazar): sonuçlar "sinyali alsaydık" hesabıdır, gerçek dolum değildir; aynı coinde ardışık taramalar
birbirine benzer olduğundan etkin örnek sayısı görünenden azdır; gölge puanın direnç yakınlığı cezası günlükte direnç olmadığı için
hesaplanamaz (yalnız fiyatın EMA20'den uzamış olması cezası uygulanır)."""
from __future__ import annotations

import math
from statistics import fmean, pstdev
from typing import Any

from .kais_shadow_score import shadow_score

LOW_SAMPLE = 30
CONF_EDGES = (0, 70, 78, 86, 90, 101)
SWEEP_CONF = tuple(range(70, 96, 2))
SWEEP_TRAP = (20, 25, 30, 35, 40, 50)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def usable(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Maliyet sonrası hipotetik sonucu olan LONG/SHORT kayıtlar."""
    out = []
    for r in records:
        direction = r.get("direction") if r.get("direction") in ("LONG", "SHORT") else r.get("hypothetical_direction")
        if direction in ("LONG", "SHORT") and _num(r.get("hypothetical_r_costs")) is not None:
            out.append({**r, "_dir": direction, "_r": _num(r["hypothetical_r_costs"]), "_g": _num(r.get("hypothetical_r_gross"))})
    return out


def summarize_r(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [r["_r"] for r in rows]
    n = len(values)
    if not n:
        return {"n": 0, "mean_r": None, "win_pct": None, "total_r": None, "ci95": None, "low_sample": True}
    mean = fmean(values)
    ci = None if n < 2 else round(1.96 * pstdev(values) / math.sqrt(n), 3)
    return {"n": n, "mean_r": round(mean, 3), "win_pct": round(sum(1 for v in values if v > 0) / n * 100), "total_r": round(sum(values), 2),
            "ci95": ci, "low_sample": n < LOW_SAMPLE,
            "stop_pct": round(sum(1 for r in rows if r.get("hypothetical_result") == "STOP") / n * 100)}


def by_confidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for lo, hi in zip(CONF_EDGES, CONF_EDGES[1:]):
        group = [r for r in rows if (c := _num(r.get("confidence"))) is not None and lo <= c < hi]
        out.append({"bucket": f"{lo}-{min(hi - 1, 100)}", **summarize_r(group)})
    return out


def sweep_confidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"min_confidence": t, **summarize_r([r for r in rows if (c := _num(r.get("confidence"))) is not None and c >= t])} for t in SWEEP_CONF]


def sweep_trap(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"max_trap": t, **summarize_r([r for r in rows if (c := _num(r.get("trap"))) is not None and c <= t])} for t in SWEEP_TRAP]


def decision_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ACCEPTED": summarize_r([r for r in rows if r.get("decision") == "ACCEPTED"]), "REJECTED": summarize_r([r for r in rows if r.get("decision") != "ACCEPTED"])}


def _analysis_of(record: dict[str, Any]) -> dict[str, Any] | None:
    atr, entry = _num(record.get("atr")), _num(record.get("entry"))
    if not atr or atr <= 0 or entry is None:
        return None
    return {"entry": entry, "ema": {"ema20": record.get("ema20"), "ema50": record.get("ema50"), "ema200": record.get("ema200")}, "macd": record.get("macd_hist"),
            "rsi": record.get("rsi"), "adx": record.get("adx"), "volume_ratio": record.get("vol_ratio"), "atr": atr,
            "resistance": entry + atr * 100}   # direnç günlükte yok: yakınlık cezası devre dışı kalır


def shadow_compare(rows: list[dict[str, Any]], old_min: float = 86, floor: float = 80) -> dict[str, Any]:
    """LONG ve eski güven >= 80 olan sinyallerde eski (>=86) ve gölge puanın (>=86) geçiş gruplarının gerçek sonucu."""
    groups: dict[str, list[dict[str, Any]]] = {"ikisi_de_geçer": [], "yalnız_eski_geçer": [], "yalnız_yeni_geçer": [], "ikisi_de_geçmez": []}
    skipped = 0
    for r in rows:
        conf = _num(r.get("confidence"))
        if r["_dir"] != "LONG" or conf is None or conf < floor:
            continue
        analysis = _analysis_of(r)
        shadow = shadow_score(analysis) if analysis else None
        if shadow is None:
            skipped += 1
            continue
        old, new = conf >= old_min, shadow["would_pass"]
        groups["ikisi_de_geçer" if old and new else "yalnız_eski_geçer" if old else "yalnız_yeni_geçer" if new else "ikisi_de_geçmez"].append(r)
    return {"groups": {k: summarize_r(v) for k, v in groups.items()}, "skipped_missing_fields": skipped}


def build(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = usable(records)
    return {"total_records": len(records), "with_outcome": len(rows),
            "long": _section([r for r in rows if r["_dir"] == "LONG"]), "short": _section([r for r in rows if r["_dir"] == "SHORT"])}


def _section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"overall": summarize_r(rows), "by_confidence": by_confidence(rows), "sweep_confidence": sweep_confidence(rows), "sweep_trap": sweep_trap(rows),
            "decision": decision_split(rows), "shadow": shadow_compare(rows)}


def _cell(s: dict[str, Any]) -> str:
    if not s["n"]:
        return "veri yok"
    ci = f" ±{s['ci95']}" if s["ci95"] is not None else ""
    flag = "  (az örnek)" if s["low_sample"] else ""
    return f"n={s['n']:<4} ort R={s['mean_r']:+.3f}{ci}  kazanma %{s['win_pct']}  toplam R={s['total_r']:+.1f}{flag}"


def render(report: dict[str, Any]) -> str:
    lines = [f"PUANLAMA RAPORU: {report['total_records']} kayıt, {report['with_outcome']} tanesinin maliyet sonrası hipotetik sonucu var.",
             "R = riske göre kazanç (maliyet sonrası). ± değeri %95 güven aralığı yarıçapıdır; 0'ı kapsıyorsa kazanç/kayıp ayırt edilemez.", ""]
    for name, key in (("LONG", "long"), ("SHORT", "short")):
        sec = report[key]
        lines += [f"=== {name} ===", f"Genel: {_cell(sec['overall'])}", "", "Güven puanı dilimleri:"]
        lines += [f"  {b['bucket']:>7}: {_cell(b)}" for b in sec["by_confidence"]]
        lines += ["", "Güven eşiği taraması (>= eşik):"] + [f"  >= {b['min_confidence']}: {_cell(b)}" for b in sec["sweep_confidence"]]
        lines += ["", "Tuzak eşiği taraması (<= eşik):"] + [f"  <= {b['max_trap']}: {_cell(b)}" for b in sec["sweep_trap"]]
        d = sec["decision"]
        lines += ["", "Kapılar işe yarıyor mu? (kabul edilenler ve reddedilenler aynı ölçüyle):", f"  Kabul   : {_cell(d['ACCEPTED'])}", f"  Reddedilen: {_cell(d['REJECTED'])}"]
        if name == "LONG":
            sh = sec["shadow"]
            lines += ["", "Kais Long gölge puanı (eski >=86 ile yeni >=86), güven >= 80 olan LONG sinyaller:"]
            lines += [f"  {k:<18}: {_cell(v)}" for k, v in sh["groups"].items()]
            lines += [f"  (eksik alan yüzünden hesaplanamayan: {sh['skipped_missing_fields']}; direnç cezası hesaba katılmadı)"]
        lines.append("")
    lines += ["Dikkat: sonuçlar 'alsaydık' hesabıdır, gerçek dolum değildir. Aynı coinde ardışık taramalar benzer olduğundan gerçek örnek sayısı görünenden azdır.",
              "Bu rapor yalnız ölçer; eşik ya da puan değiştirmez."]
    return "\n".join(lines)
