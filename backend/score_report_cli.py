"""Sinyal günlüğünden puanlama raporu: python3 score_report_cli.py [günlük.sqlite3 ...]  (yalnız okur, hiçbir şeyi değiştirmez)."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from app.score_report import build, render
from app.signal_journal import Journal, _base_path


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    paths = [Path(a) for a in argv if not a.startswith("--")] or [_base_path()]
    records = []
    for path in paths:
        if not path.exists():
            print(f"Günlük bulunamadı: {path} (PROTREBOT_SIGNAL_JOURNAL_PATH ayarlı mı?)", file=sys.stderr)
            return 2
        with tempfile.TemporaryDirectory() as tmp:   # canlı günlüğe yazılmaz: salt-okunur bağlanıp geçici kopyadan okunur
            copy = Path(tmp) / "journal-copy.sqlite3"
            source = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            target = sqlite3.connect(copy)
            try:
                source.backup(target)
            finally:
                target.close(); source.close()
            records += Journal(copy).rows()
    report = build(records)
    print(json.dumps(report, ensure_ascii=False, indent=1) if as_json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
