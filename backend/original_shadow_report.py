"""Gölge kayıt özeti (salt okunur). Kullanım: python original_shadow_report.py [klasör]

Bu araç yalnız signals.jsonl / trades.jsonl dosyalarını okur; ağ, anahtar veya emir yoktur.
Küçük örneklemde sonuç KANIT DEĞİLDİR; N görünür olsun diye grup başına yazılır.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.original_shadow import GROUPS, directory, summarize


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str]) -> int:
    folder = Path(argv[1]) if len(argv) > 1 else directory()
    signals, trades = read(folder / "signals.jsonl"), read(folder / "trades.jsonl")
    print(f"Klasör: {folder}")
    print("Kaydedilen sinyal:", {g: sum(1 for s in signals if s["group"] == g) for g in GROUPS})
    def fmt(value, pattern):
        return "-" if value is None else pattern.format(value)

    for group, row in summarize(trades).items():
        print(f"{group}: N={row['N']} çözülemeyen={row['unresolved']} ort={fmt(row['mean_net_r'], '{:+.3f}R')} "
              f"kazanma={fmt(row['win_rate'] and row['win_rate'] * 100, '%{:.0f}')} PF={fmt(row['profit_factor_r'], '{:.2f}')} "
              f"belirsiz_mum={row['ambiguous_bars']}")
        print(f"   giriş: APPROXIMATE_ENTRY={row['approximate_entry']} | tarama-kapanış farkı ort={fmt(row['scan_vs_close_abs_bps_mean'], '{:.1f}bps')} "
              f"maks={fmt(row['scan_vs_close_abs_bps_max'], '{:.1f}bps')} | tick farkı giriş={fmt(row['entry_tick_abs_bps_mean'], '{:.2f}bps')} "
              f"TP1={fmt(row['tp1_tick_abs_bps_mean'], '{:.2f}bps')}")
        print(f"   canlıda açılabilirlik: açılamaz={row['live_unopenable']} bilinmiyor={row['live_openable_unknown']} | "
              f"yalnız açılabilenler N={row['N_live_openable']} ort={fmt(row['mean_net_r_live_openable'], '{:+.3f}R')}")
    print("Toplanmayan (yeni ağ çağrısı gerektirir): mark fiyatı farkı, gerçekleşen dolum farkı.")
    print("Not: küçük N kanıt değildir; karşılaştırma önceden yazılmış kanıt kuralıyla yapılmalıdır.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
