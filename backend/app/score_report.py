"""Puanlama raporu (yalnız okur, hiçbir karara dokunmaz): sinyal günlüğündeki gerçek hipotetik sonuçlara (maliyet sonrası R) bakarak
güven puanının, eşiklerin ve Kais Long gölge puanının gerçekte işe yarayıp yaramadığını ölçer.

Sınırlar (rapor da bunları yazar): sonuçlar "sinyali alsaydık" hesabıdır, gerçek dolum değildir; aynı coinde ardışık taramalar
birbirine benzer olduğundan etkin örnek sayısı görünenden azdır; gölge puanın direnç yakınlığı cezası günlükte direnç olmadığı için
hesaplanamaz (yalnız fiyatın EMA20'den uzamış olması cezası uygulanır)."""
from __future__ import annotations

import math
from itertools import pairwise
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
            "stop_pct": round(sum(1 for r in rows if r.get("hypothetical_result") == "STOP") / n * 100), "stop_after_1r_pct": _stop_after_1r(rows)}


def _stop_after_1r(rows: list[dict[str, Any]]) -> int | None:
    """Stop olanların yüzde kaçında fiyat stoptan önce en az +1R gitmiş (yalnız mfe_r kayıtlıysa)."""
    stops = [r for r in rows if r.get("hypothetical_result") == "STOP" and _num(r.get("mfe_r")) is not None]
    return round(sum(1 for r in stops if _num(r["mfe_r"]) >= 1.0) / len(stops) * 100) if len(stops) >= 10 else None


def by_confidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for lo, hi in pairwise(CONF_EDGES):
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
    atr, entry, resistance = _num(record.get("atr")), _num(record.get("entry")), _num(record.get("resistance"))
    if not atr or atr <= 0 or entry is None:
        return None
    return {"entry": entry, "ema": {"ema20": record.get("ema20"), "ema50": record.get("ema50"), "ema200": record.get("ema200")}, "macd": record.get("macd_hist"),
            "rsi": record.get("rsi"), "adx": record.get("adx"), "volume_ratio": record.get("vol_ratio"), "atr": atr,
            "resistance": resistance if resistance is not None and resistance > entry else entry + atr * 100}   # direnç kayıtta yoksa yakınlık cezası devre dışı kalır


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
    report = {"total_records": len(records), "with_outcome": len(rows),
              "long": _section([r for r in rows if r["_dir"] == "LONG"]), "short": _section([r for r in rows if r["_dir"] == "SHORT"])}
    report["findings"] = findings(report)
    return report


def _section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"overall": summarize_r(rows), "by_confidence": by_confidence(rows), "sweep_confidence": sweep_confidence(rows), "sweep_trap": sweep_trap(rows),
            "decision": decision_split(rows), "shadow": shadow_compare(rows)}


MIN_CLAIM_N = 30
CURRENT_GATE = 80


def _verdict(s: dict[str, Any]) -> str:
    if s["n"] < MIN_CLAIM_N or s["mean_r"] is None or s["ci95"] is None:
        return "yetersiz"
    return "zarar" if s["mean_r"] + s["ci95"] < 0 else "kâr" if s["mean_r"] - s["ci95"] > 0 else "belirsiz"


def _fmt(s: dict[str, Any]) -> str:
    return f"ort R {s['mean_r']:+.2f} (n={s['n']})"


def findings(report: dict[str, Any]) -> list[dict[str, str]]:
    """Kural tabanlı, dürüst yorum: yalnız yeterli örnek (>=30) olan karşılaştırmalar konuşur. Eşik taraması çok sayıda deneme
    içerdiği için ('en iyi eşik' seçimi iyimserdir) öneriler yeni veriyle teyit edilmeden uygulanmamalıdır."""
    out: list[dict[str, str]] = []
    n = report["with_outcome"]
    if n < 100:
        out.append({"level": "uyarı", "text": f"Sonuçlanan sinyal sayısı {n}. Güvenilir sonuç için en az 100, daha iyisi 300+ gerekir. Aşağıdakiler yön gösterir, kesin değildir."})
    for name, key in (("LONG", "long"), ("SHORT", "short")):
        sec = report[key]
        overall = sec["overall"]
        verdict = _verdict(overall)
        if verdict == "zarar":
            out.append({"level": "uyarı", "text": f"{name} sinyalleri maliyet sonrası net ZARAR yazıyor: {_fmt(overall)}, güven aralığı sıfırın altında. Bu yönün kuralları gözden geçirilmeli."})
        elif verdict == "kâr":
            out.append({"level": "bilgi", "text": f"{name} sinyalleri maliyet sonrası net kâr bırakıyor: {_fmt(overall)}, güven aralığı sıfırın üstünde."})
        elif verdict == "belirsiz":
            out.append({"level": "bilgi", "text": f"{name}: kâr/zarar henüz ayırt edilemiyor ({_fmt(overall)}, ±{overall['ci95']}). Daha çok veri gerekir."})
        buckets = {b["bucket"]: b for b in sec["by_confidence"]}
        high = summarize_r_merge([buckets.get(k) for k in ("86-89", "90-100")])
        low = summarize_r_merge([buckets.get(k) for k in ("0-69", "70-77", "78-85")])
        if high["n"] >= MIN_CLAIM_N and low["n"] >= MIN_CLAIM_N:
            if high["mean_r"] <= low["mean_r"]:
                out.append({"level": "öneri", "text": f"{name}: güven puanı sıralama gücü göstermiyor; yüksek puanlılar ({_fmt(high)}) düşük puanlılardan ({_fmt(low)}) iyi değil. Puan bileşenlerinin ağırlıkları gözden geçirilmeli."})
            else:
                out.append({"level": "bilgi", "text": f"{name}: yüksek güven puanı daha iyi sonuç veriyor ({_fmt(high)} karşı {_fmt(low)}); puan sıralıyor."})
        usable = [b for b in sec["sweep_confidence"] if b["n"] >= MIN_CLAIM_N and b["mean_r"] is not None]
        current = next((b for b in usable if b["min_confidence"] == CURRENT_GATE), None)
        if usable and current is not None:
            best = max(usable, key=lambda b: b["mean_r"])
            margin = max(best["ci95"] or 0, current["ci95"] or 0)
            if best["min_confidence"] != CURRENT_GATE and best["mean_r"] - current["mean_r"] > margin:
                out.append({"level": "öneri", "text": f"{name}: güven eşiği {best['min_confidence']} daha iyi görünüyor ({_fmt(best)}) ve mevcut {CURRENT_GATE} ({_fmt(current)}) ondan belirgin geride. Çok eşik denendiği için iyimser bir seçimdir; yeni veride teyit edilmeden uygulama."})
        trap = {b["max_trap"]: b for b in sec["sweep_trap"] if b["n"] >= MIN_CLAIM_N and b["mean_r"] is not None}
        if 35 in trap and 20 in trap and trap[20]["mean_r"] - trap[35]["mean_r"] > max(trap[20]["ci95"] or 0, trap[35]["ci95"] or 0):
            out.append({"level": "öneri", "text": f"{name}: tuzak puanı <=20 ({_fmt(trap[20])}) <=35'ten ({_fmt(trap[35])}) belirgin iyi; tuzak sınırını sıkılaştırmak değerlendirilebilir."})
        if overall["n"] >= MIN_CLAIM_N and overall.get("stop_pct") is not None and overall["stop_pct"] >= 60:
            out.append({"level": "öneri", "text": f"{name}: sinyallerin %{overall['stop_pct']}'i stop oluyor. Stop mesafesi dar ya da giriş zamanlaması erken olabilir."})
        if overall.get("stop_after_1r_pct") is not None and overall["stop_after_1r_pct"] >= 25:
            out.append({"level": "öneri", "text": f"{name}: stop olanların %{overall['stop_after_1r_pct']}'inde fiyat stoptan önce en az +1R gitmiş. Kısmi kâr alma ya da stopu girişe çekme kuralı değerlendirilebilir."})
        gate = sec["decision"]
        if gate["ACCEPTED"]["n"] >= MIN_CLAIM_N and gate["REJECTED"]["n"] >= MIN_CLAIM_N and gate["ACCEPTED"]["mean_r"] <= gate["REJECTED"]["mean_r"]:
            out.append({"level": "uyarı", "text": f"{name}: canlı kapıdan geçenler ({_fmt(gate['ACCEPTED'])}) reddedilenlerden ({_fmt(gate['REJECTED'])}) iyi değil; kapı kriterleri değer katmıyor olabilir."})
        sh = sec["shadow"]["groups"] if name == "LONG" else {}
        if sh and sh["yalnız_eski_geçer"]["n"] >= MIN_CLAIM_N and sh["yalnız_yeni_geçer"]["n"] >= MIN_CLAIM_N:
            better = "yeni (Kais Long gölge)" if sh["yalnız_yeni_geçer"]["mean_r"] > sh["yalnız_eski_geçer"]["mean_r"] else "eski"
            out.append({"level": "bilgi", "text": f"LONG gölge puan: yalnız eskinin geçirdikleri {_fmt(sh['yalnız_eski_geçer'])}, yalnız yeninin geçirdikleri {_fmt(sh['yalnız_yeni_geçer'])}; {better} puan daha iyi görünüyor."})
    longs, shorts = _verdict(report["long"]["overall"]), _verdict(report["short"]["overall"])
    if {longs, shorts} == {"zarar", "kâr"}:
        weak = "LONG" if longs == "zarar" else "SHORT"
        out.append({"level": "öneri", "text": f"Yönler arasında belirgin fark var: {weak} zayıf, diğer yön kâr bırakıyor. Zayıf yönün filtresi sıkılaştırılabilir ya da o yön geçici kapatılabilir."})
    if len(out) == (1 if n < 100 else 0):
        out.append({"level": "bilgi", "text": "Henüz yeterli örnekle kanıtlanmış bir bulgu yok."})
    return out


def summarize_r_merge(buckets: list[dict[str, Any] | None]) -> dict[str, Any]:
    """Dilimlerin ağırlıklı ortalaması (karşılaştırma için); n ve ortalama dışında alan gerekmez."""
    rows = [b for b in buckets if b and b["n"]]
    total = sum(b["n"] for b in rows)
    return {"n": total, "mean_r": (sum(b["mean_r"] * b["n"] for b in rows) / total) if total else None}


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
            lines += [f"  (eksik alan yüzünden hesaplanamayan: {sh['skipped_missing_fields']}; dirence yakınlık cezası yalnız kayıtta direnç varsa uygulanır)"]
        lines.append("")
    lines += ["=== BULGULAR VE GELİŞTİRME ÖNERİLERİ ==="] + [f"[{f['level']}] {f['text']}" for f in report.get("findings", [])] + [""]
    lines += ["Dikkat: sonuçlar 'alsaydık' hesabıdır, gerçek dolum değildir. Aynı coinde ardışık taramalar benzer olduğundan gerçek örnek sayısı görünenden azdır.",
              "Bu rapor yalnız ölçer; eşik ya da puan değiştirmez."]
    return "\n".join(lines)
