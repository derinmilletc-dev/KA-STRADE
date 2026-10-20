"""Zorunlu güvenlik testleri için katı kapı (Backend Security Tests).

Bilinen-hata listesi YOKTUR. Aşağıdakilerden biri varsa çıkış kodu 1'dir (kapalı başarısızlık):
failure/error (setup, teardown, toplama, iç hata), atlanan test (skip, xfail, toplama atlaması),
eksik/boş/bozuk rapor, hiç test içermeyen zorunlu dosya, toplanıp çalıştırılmayan test veya
toplama listesinde olmayan bir testin çalışması. Atlanan test başarı sayılmaz.

Girdiler (pytest'in kendi çıktıları; sınıf adı öneki veya dosya niteliği tahmin edilmez):
  --report     pytest --junitxml dosyası (varsayılan xunit2 yeterli; `file` niteliği KULLANILMAZ).
  --collected  `pytest --collect-only -q` çıktısı: yetkili test listesi (dosya::[Sınıf::]test satırları).
  --require    zorunlu test dosyası, örn. tests/test_account_settings.py (birden çok kez verilebilir).
               Eşleşme yol sınırında sonek eşleşmesidir; rootdir öneki değişirse de çalışır.

Eşleştirme: pytest JUnit `classname`/`name` değerlerini düğüm kimliğinden (nodeid) türetir
(`_pytest.junitxml.mangle_test_address`: ilk `[` öncesi `::` ile bölünür, dosya yolu noktalı yola çevrilir,
`.py` atılır, parametreler son adın sonuna eklenir). Kapı aynı dönüşümü toplama listesindeki her düğüm
kimliğine uygular ve (classname, name) çiftlerini JUnit ile birebir karşılaştırır. JUnit `file` niteliği
testin TANIMLANDIĞI dosyadır; başka modülden miras alınan testlerde düğüm kimliğindeki dosyadan farklıdır,
bu yüzden kullanılmaz. Hem toplama hem koşu aynı çağrı dizininden (aynı rootdir) yapılmalıdır.

Her zorunlu dosya için: toplanan > 0 ve toplanan her test JUnit'te çalışmış olmalıdır; JUnit'te olup
toplama listesinde bulunmayan test de hatadır. Taban sayı sabitlenmez; sınır: koddan test silinirse
toplanan da düşer ve kapı bunu yakalamaz. Başarıda dosya başına sayılar bildirimde yazılır.
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

MAX_ITEMS = 30
MAX_TEXT = 200
NODE_LINE = re.compile(r"^(?P<file>[^\s:]+\.py)::")
BAD_KINDS = {"failure", "error", "setup-error", "teardown-error", "collection-error", "internal-error"}
COLLECTION_KINDS = {"collection-error", "collection-skipped", "internal-error"}


def annotate(level: str, title: str, lines: list[str]) -> None:
    body = "%0A".join(" ".join(line.replace("%", "%25").split()) for line in lines)
    print(f"::{level} title={title}::{body}")


def clip(text: str, limit: int = MAX_TEXT) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def norm_path(value: str) -> str:
    value = (value or "").replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def path_matches(path: str, required: str) -> bool:
    path, required = norm_path(path), norm_path(required)
    return bool(path) and (path == required or path.endswith("/" + required))


def junit_key(nodeid: str) -> tuple[str, str]:
    """Düğüm kimliğinden pytest'in JUnit (classname, name) çifti; `mangle_test_address` ile aynı kural."""
    path, bracket, params = nodeid.partition("[")
    names = path.split("::")
    names[0] = re.sub(r"\.py$", "", names[0].replace("/", "."))
    names[-1] += bracket + params
    return ".".join(names[:-1]), names[-1]


def read_text_file(label: str, path_text: str) -> tuple[Path | None, str | None]:
    """Ortak dosya doğrulaması: yok, dosya değil (dizin), 0 bayt, okunamaz."""
    path = Path(path_text)
    try:
        if not path.exists():
            return None, f"{label} missing: {path_text} (the step that creates it did not run or failed)."
        if not path.is_file():
            return None, f"{label} path is not a regular file (directory?): {path_text}"
        if path.stat().st_size == 0:
            return None, f"{label} is empty (0 bytes): {path_text}"
    except OSError as exc:
        return None, f"{label} cannot be inspected ({type(exc).__name__}): {path_text}"
    return path, None


def load_report(path_text: str) -> tuple[ET.Element | None, str | None]:
    path, error = read_text_file("JUnit report", path_text)
    if error or path is None:
        return None, error
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return None, f"JUnit report is not valid XML (corrupt or truncated): {exc}"
    except OSError as exc:
        return None, f"JUnit report cannot be read ({type(exc).__name__}): {path_text}"
    if root.tag not in ("testsuite", "testsuites"):
        return None, f"JUnit report has unexpected root element <{root.tag}>; expected <testsuites> or <testsuite>."
    return root, None


def load_collected(path_text: str) -> tuple[dict[tuple[str, str], str] | None, list[str]]:
    """`pytest --collect-only -q` çıktısından {(classname, name): düğüm dosyası}. Hata varsa (None, sorunlar)."""
    path, error = read_text_file("Collected-test list", path_text)
    if error or path is None:
        return None, [error or "Collected-test list unavailable."]
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return None, [f"Collected-test list cannot be read ({type(exc).__name__}): {path_text}"]
    problems = [clip(line) for line in lines if "ERROR collecting" in line]
    expected: dict[tuple[str, str], str] = {}
    for line in lines:
        match = NODE_LINE.match(line)
        if match:
            expected[junit_key(line)] = match.group("file")
    if problems:
        problems = ["Collection errors reported by pytest --collect-only: " + "; ".join(problems[:MAX_ITEMS])]
    if not expected and not problems:
        problems = ["Collected-test list contains no test node ids."]
    return expected, problems


def children(case: ET.Element) -> list[tuple[str, str]]:
    """Bir <testcase> içindeki TÜM failure/error/skipped çocukları (tür, metin); hiçbiri atılmaz."""
    found: list[tuple[str, str]] = []
    for child in case:
        message = child.get("message") or ""
        detail = message or (child.text or "")
        if child.tag == "failure":
            found.append(("failure", detail))
        elif child.tag == "error":
            if message == "collection failure":
                kind = "collection-error"
            elif case.get("classname") == "pytest" and case.get("name") == "internal":
                kind = "internal-error"
            elif message.startswith("failed on setup"):
                kind = "setup-error"
            elif message.startswith("failed on teardown"):
                kind = "teardown-error"
            else:
                kind = "error"
            found.append((kind, detail))
        elif child.tag == "skipped":
            if message == "collection skipped":
                kind = "collection-skipped"
            elif message == "xfail-marked test passes unexpectedly":
                kind = "xpass-unexpected"
            elif child.get("type") == "pytest.xfail":
                kind = "xfail"
            else:
                kind = "skip"
            found.append((kind, detail))
    return found


def analyse(root: ET.Element) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Aynı test birden çok <testcase> (çağrı hatası + teardown hatası) veya tek <testcase> içinde
    birden çok hata taşıyabilir; kimlik (classname, name) ile tek teste indirgenir, ayrıntı kaybolmaz."""
    tests: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for case in root.iter("testcase"):
        tests[(case.get("classname") or "", case.get("name") or "")].extend(children(case))
    return tests


def describe(identity: tuple[str, str], kinds: list[tuple[str, str]]) -> str:
    classname, name = identity
    label = f"{classname}::{name}" if classname else name
    detail = "; ".join(f"{kind}: {clip(text)}" if text else kind for kind, text in kinds)
    return f"{label} [{detail}]" if detail else label


def more(items: list[str]) -> str:
    return "; ".join(items[:MAX_ITEMS]) + (f" (+{len(items) - MAX_ITEMS} more)" if len(items) > MAX_ITEMS else "")


def main(report: str, collected: str, required: list[str]) -> int:
    problems: list[str] = []
    root, error = load_report(report)
    if error or root is None:
        problems.append(error or "JUnit report unavailable.")
    expected, collect_problems = load_collected(collected)
    problems.extend(collect_problems)

    passed = failed = skipped = 0
    counts: dict[str, tuple[int, int]] = {}
    if root is not None:
        tests = analyse(root)
        if not tests:
            problems.append("JUnit report contains no test cases (zero tests ran).")
        bad: list[str] = []
        skip_list: list[str] = []
        collection_errors: list[str] = []
        executed: set[tuple[str, str]] = set()
        for identity, kinds in tests.items():
            names = {kind for kind, _ in kinds}
            if not names & COLLECTION_KINDS:
                executed.add(identity)
            if names & {"collection-error", "internal-error"}:
                collection_errors.append(describe(identity, kinds))
            if names & BAD_KINDS:
                failed += 1
                bad.append(describe(identity, kinds))
            elif names:
                skipped += 1
                skip_list.append(describe(identity, kinds))
            else:
                passed += 1
            if names & {"collection-skipped"} and not names & BAD_KINDS:
                collection_errors.append(describe(identity, kinds))
        if collection_errors:
            problems.append(f"{len(collection_errors)} collection problem(s): " + more(collection_errors))
        if bad:
            problems.append(f"{len(bad)} failed test(s): " + more(bad))
        if skip_list:
            problems.append(f"{len(skip_list)} skipped/xfail test(s); skipped is not a pass: " + more(skip_list))
        if expected is not None:
            unexpected = sorted(describe(key, []) for key in executed - set(expected))
            if unexpected:
                problems.append(f"{len(unexpected)} executed test(s) are not in the collected list: " + more(unexpected))
            for module in required:
                wanted = {key for key, file in expected.items() if path_matches(file, module)}
                ran = wanted & executed
                counts[module] = (len(wanted), len(ran))
                if not wanted:
                    problems.append(f"Required file has no collected tests: {module}")
                elif not ran:
                    problems.append(f"Required file ran no tests (collected {len(wanted)}): {module}")
                elif ran != wanted:
                    missing = sorted(describe(key, []) for key in wanted - ran)
                    problems.append(f"Required file executed {len(ran)} of {len(wanted)} collected tests: {module}; "
                                    "not executed: " + more(missing))
    summary = f"{passed} passed, {failed} failed, {skipped} skipped"
    per_file = [f"{m}: collected {c}, executed {e}" for m, (c, e) in counts.items()]
    if problems:
        annotate("error", "security tests", [summary, *per_file, *problems])
        return 1
    annotate("notice", "security tests", [summary, *per_file])
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict gate for the Backend Security Tests JUnit report.")
    parser.add_argument("--report", required=True, help="pytest --junitxml file")
    parser.add_argument("--collected", required=True, help="output of pytest --collect-only -q")
    parser.add_argument("--require", action="append", required=True, metavar="FILE",
                        help="required test file, e.g. tests/test_account_settings.py (repeatable)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    try:
        code = main(args.report, args.collected, args.require)
    except Exception as exc:  # kapalı başarısızlık: beklenmeyen kapı hatası asla yeşil olmaz
        annotate("error", "security tests", [f"Gate internal error ({type(exc).__name__}): {exc}"])
        code = 1
    sys.exit(code)
