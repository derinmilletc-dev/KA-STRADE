"""Asistan testleri için kapı: pytest JUnit raporunu bilinen-hatalar listesiyle karşılaştırır.

- Listede olmayan her failure/error -> çıkış 1 (yeni regresyon).
- Toplama hatası, boş rapor veya rapor yoksa -> çıkış 1 (testler hiç koşmamış sayılmaz).
- Listede olup artık geçen test -> uyarı (listeden silinmeli), CI'ı kırmaz.
Sonuç GitHub annotation olarak da yazılır (log erişimi olmayan yerlerden okunabilsin diye)."""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def node_id(case: ET.Element) -> str:
    module = case.get("classname", "")
    parts = module.split(".")
    # classname örn. "tests.test_assistant_api.AssistantApiTests" -> tests/test_assistant_api.py::AssistantApiTests::name
    path = "/".join(parts[:2]) + ".py" if len(parts) >= 2 else module
    rest = "::".join(parts[2:] + [case.get("name", "")])
    return f"{path}::{rest}"


def annotate(level: str, title: str, lines: list[str]) -> None:
    body = "%0A".join(line.replace("%", "%25") for line in lines)
    print(f"::{level} title={title}::{body}")


def main(report: str, known_file: str) -> int:
    known = {line.strip() for line in Path(known_file).read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")}
    path = Path(report)
    if not path.exists():
        annotate("error", "assistant tests", ["JUnit report missing: tests did not run."])
        return 1
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    collection_errors = sum(int(s.get("errors", 0)) for s in suites) - sum(1 for c in cases if c.find("error") is not None)
    failed = {node_id(c) for c in cases if c.find("failure") is not None or c.find("error") is not None}
    skipped = sum(1 for c in cases if c.find("skipped") is not None)
    passed = len(cases) - len(failed) - skipped
    new = sorted(failed - known)
    fixed = sorted(known - failed)
    summary = [f"{passed} passed, {len(failed)} failed ({len(failed & known)} known, {len(new)} new), {skipped} skipped"]
    if not cases or collection_errors > 0:
        annotate("error", "assistant tests", summary + ["No test cases collected or collection errors occurred."])
        return 1
    if fixed:
        annotate("warning", "known failures now passing (remove from list)", fixed)
    if new:
        annotate("error", "NEW assistant test failures", summary + new)
        return 1
    annotate("notice", "assistant tests", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
