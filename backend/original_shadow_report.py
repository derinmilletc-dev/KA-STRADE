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
    for group, row in summarize(trades).items():
        mean = "-" if row["mean_net_r"] is None else f"{row['mean_net_r']:+.3f}R"
        win = "-" if row["win_rate"] is None else f"%{row['win_rate'] * 100:.0f}"
        pf = "-" if row["profit_factor_r"] is None else f"{row['profit_factor_r']:.2f}"
        print(f"{group}: N={row['N']} çözülemeyen={row['unresolved']} ort={mean} kazanma={win} PF={pf} belirsiz_mum={row['ambiguous_bars']}")
    print("Not: küçük N kanıt değildir; karşılaştırma önceden yazılmış kanıt kuralıyla yapılmalıdır.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
