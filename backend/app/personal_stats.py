"""Kendi işlem geçmişinden çıkarımlar (saf hesap). Girdi: kapanmış işlemler; çıktı: örnek sayısı yeterliyse kısa tespitler.

Küçük örneklerde sonuç yanıltıcıdır; bu yüzden her tespit asgari işlem sayısı ister ve sayıyı metinde gösterir.
Bunlar neden-sonuç değil, geçmiş dağılımın tarifidir."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

TR = timezone(timedelta(hours=3))
MIN_TOTAL = 8
MIN_GROUP = 4
BLOCKS = ((0, 4), (4, 8), (8, 12), (12, 16), (16, 20), (20, 24))


def _time(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _rate(rows: list[dict[str, Any]]) -> float:
    return sum(1 for r in rows if r["net"] > 0) / len(rows) * 100 if rows else 0.0


def block_of(hour: int) -> tuple[int, int]:
    return BLOCKS[min(hour // 4, 5)]


def analyze(trades: list[dict[str, Any]], basis: str = "opened") -> dict[str, Any]:
    """trades: [{product_id, side, net (sayı), time (kapanış ISO), opened (açılış ISO)}]. basis="closed": açılış bilinmiyor, saat dilimi kapanışa göre."""
    verb = "kapattığın" if basis == "closed" else "açtığın"
    rows = []
    for t in trades:
        closed, opened = _time(t.get("time")), _time(t.get("opened"))
        try:
            net = float(t["net"])
        except (KeyError, TypeError, ValueError):
            continue
        if closed is None:
            continue
        rows.append({"coin": str(t.get("product_id", "")).split("-", 1)[0], "side": str(t.get("side") or "").upper(), "net": net, "closed": closed, "opened": opened})
    rows.sort(key=lambda r: r["closed"])
    out: dict[str, Any] = {"n": len(rows), "enough": len(rows) >= MIN_TOTAL, "insights": [], "bad_blocks": [], "good_blocks": []}
    if len(rows) < MIN_TOTAL:
        out["insights"] = [{"tone": "flat", "text": f"Henüz {len(rows)} kapanmış işlem var; kalıp çıkarmak için en az {MIN_TOTAL} gerekir."}]
        return out
    overall = _rate(rows)
    ins: list[dict[str, str]] = []

    # saat dilimi (TSİ)
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(block_of((r["opened"] if basis != "closed" and r["opened"] else r["closed"]).astimezone(TR).hour), []).append(r)
    ranked = sorted(((b, g) for b, g in groups.items() if len(g) >= MIN_GROUP), key=lambda x: _rate(x[1]))
    if len(ranked) >= 2:
        (wb, wg), (bb, bg) = ranked[0], ranked[-1]
        if _rate(bg) - _rate(wg) >= 25:
            out["bad_blocks"].append({"from": wb[0], "to": wb[1], "win_rate": round(_rate(wg)), "trades": len(wg)})
            out["good_blocks"].append({"from": bb[0], "to": bb[1], "win_rate": round(_rate(bg)), "trades": len(bg)})
            ins.append({"tone": "warn", "text": f"TSİ {wb[0]:02d}-{wb[1]:02d} arası {verb} {len(wg)} işlemin %{round(_rate(wg))}'i kazandı; {bb[0]:02d}-{bb[1]:02d} arasında %{round(_rate(bg))} ({len(bg)} işlem)."})

    # yön
    longs, shorts = [r for r in rows if r["side"] == "LONG"], [r for r in rows if r["side"] == "SHORT"]
    if len(longs) >= MIN_GROUP and len(shorts) >= MIN_GROUP and abs(_rate(longs) - _rate(shorts)) >= 20:
        better, worse, bn, wn = (longs, shorts, "LONG", "SHORT") if _rate(longs) > _rate(shorts) else (shorts, longs, "SHORT", "LONG")
        ins.append({"tone": "flat", "text": f"{bn} işlemlerin %{round(_rate(better))} kazandı ({len(better)}), {wn} işlemlerin %{round(_rate(worse))} ({len(worse)})."})

    # kazanç/kayıp büyüklüğü
    wins = [r["net"] for r in rows if r["net"] > 0]
    losses = [-r["net"] for r in rows if r["net"] < 0]
    if len(wins) >= 3 and len(losses) >= 3:
        avg_w, avg_l = sum(wins) / len(wins), sum(losses) / len(losses)
        if avg_l > avg_w * 1.2:
            ins.append({"tone": "warn", "text": f"Ortalama kaybın ({avg_l:.2f}) ortalama kazancından ({avg_w:.2f}) büyük: kayıpları daha erken kesmek ya da hedefleri yaklaştırmak gerekebilir."})
        elif avg_w > avg_l * 1.5:
            ins.append({"tone": "good", "text": f"Ortalama kazancın ({avg_w:.2f}) kaybından ({avg_l:.2f}) belirgin büyük: kazananları iyi taşıyorsun."})

    # kayıptan sonra hemen işlem (öç alma)
    after_loss, others = [], []
    prev: dict[str, Any] | None = None
    for r in rows:
        if prev and prev["net"] < 0 and r["opened"] and 0 <= (r["opened"] - prev["closed"]).total_seconds() <= 900:
            after_loss.append(r)
        else:
            others.append(r)
        prev = r
    if len(after_loss) >= MIN_GROUP and len(others) >= MIN_GROUP and _rate(others) - _rate(after_loss) >= 20:
        ins.append({"tone": "warn", "text": f"Kayıptan sonraki 15 dk içinde açtığın {len(after_loss)} işlemin %{round(_rate(after_loss))}'i kazandı (diğerlerinde %{round(_rate(others))}). Hemen tekrar girmeden önce bekle."})

    # coin
    by_coin: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_coin.setdefault(r["coin"], []).append(r)
    bad = [(c, g) for c, g in by_coin.items() if len(g) >= 3 and sum(x["net"] for x in g) < 0 and _rate(g) <= 34]
    if bad:
        c, g = min(bad, key=lambda x: sum(r["net"] for r in x[1]))
        ins.append({"tone": "warn", "text": f"{c}: {len(g)} işlemde net {sum(r['net'] for r in g):.2f}, kazanma %{round(_rate(g))}. Bu coinde zorlanıyorsun."})

    if not ins:
        ins.append({"tone": "flat", "text": f"{len(rows)} işlemde (%{round(overall)} kazanma) belirgin bir kalıp çıkmadı."})
    out["insights"] = ins[:5]
    out["win_rate"] = round(overall, 1)
    # Yorum ekranındaki "Sana özel" notları için ham sayılar (coin bazında kayıt, ortalama kazanç/kayıp).
    out["by_coin"] = sorted(({"coin": c, "n": len(g), "win_rate": round(_rate(g)), "net": round(sum(x["net"] for x in g), 2)}
                             for c, g in by_coin.items() if len(g) >= MIN_GROUP), key=lambda item: -item["n"])[:30]
    out["avg_win"] = round(sum(wins) / len(wins), 4) if len(wins) >= 3 else None
    out["avg_loss"] = round(sum(losses) / len(losses), 4) if len(losses) >= 3 else None
    return out
