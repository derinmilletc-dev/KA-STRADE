"""SHORT-filtre karşılaştırma aracı (yalnız çevrimdışı araştırma; ağ yok, canlıya bağlı değil).

Stage 5 geliştirme verisini (TRAIN + VALIDATION; mühürlü TEST'e DOKUNMAZ) ve eski deneyin aynı çalışma
altyapısını kullanır: aynı mumlar, aynı çıkış kuralı (B), aynı portföy motoru. Aday SHORT filtreleri, eski
deneyin "filtre KAPALI" kararları üzerine uygulanır (bkz. app/short_filter_study.py). Mevcut hiçbir araştırma
dosyasını değiştirmez; çıktıyı `<output>/short-filter-compare/` altına yazar.

Önce güvenilirlik: (1) CUR_80 adayı, canlı filtre (ON) kararlarını birebir üretmeli; (2) CUR_80 ve OFF sonuçları
eski deneyin regime-study.json sonuçlarıyla aynı olmalı. Biri başarısızsa betik sıfırdan farklı kodla çıkar ve
rapor bunu en üstte yazar. Hiçbir aday "kazanan" ilan edilmez.

Kullanım (sunucuda, backend klasöründe; Stage 5 çıktı klasörü --output):
  python short_filter_compare_cli.py --output ~/kaistrade-data/regime --workers 4 --check-only
  python short_filter_compare_cli.py --output ~/kaistrade-data/regime --workers 4
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

if __name__ in ("__main__", "__mp_main__"):
    _parser = argparse.ArgumentParser(add_help=False)
    _parser.add_argument("--output", type=Path)
    _known, _ = _parser.parse_known_args()
    if _known.output is not None:
        _root = _known.output.resolve()
        if _root.is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError("Araştırma çıktıları Git dışında kalmalı")
        os.environ.update({"DATA_DIR": str(_root / "short-filter-compare" / "runtime"),
                           "PROTREBOT_DATA_DIR": str(_root / "short-filter-compare" / "runtime"),
                           "DATABASE_URL": "", "PROTREBOT_DURABLE_AUTH_REQUIRED": "0", "ASSISTANT_LIVE_TESTS": "0"})

from regime_cli import deny_network, precompute  # noqa: E402  (ağ engeli ve ortam yukarıda kuruldu)

deny_network()

from app import execution_core as core  # noqa: E402
from app import short_filter_study as study  # noqa: E402
from app.backtest_ablation import PRESETS, frozen_replay, prepare_entries, verify_control  # noqa: E402
from app.backtest_baseline import Config, metrics, run  # noqa: E402
from app.backtest_data import load_dataset  # noqa: E402
from app.backtest_diagnostics import trade_set_changes, utc  # noqa: E402
from app.regime_statistics import cohort_periods, complete, joint_contrasts, summary  # noqa: E402
from app.regime_study import ResearchDataset, development_manifest, lock_plan  # noqa: E402
from backtest_cli import export_trades  # noqa: E402
from backtest_diagnostics_cli import write_json, write_table  # noqa: E402

FINGERPRINT_SOURCES = ("analysis.py", "main.py", "execution_core.py", "v25_execution.py", "backtest_data.py",
                       "regime_study.py", "short_filter_study.py")
LIMITATIONS = [
    "Geliştirme verisi (TRAIN+VALIDATION) kullanıldı; mühürlü TEST'e dokunulmadı. Aday sayısı arttıkça aynı veride "
    "'şans eseri iyi görünen' aday çıkma ihtimali artar; sonuçlar kanıt değil, ölçümdür.",
    "Aday kurallar yalnızca giriş ELER (OFF kümesinin alt kümesi). Elenen girişin yerine başka giriş açılması, kontrol "
    "ve adaylarda aynı portföy motoru tarafından yeniden taranır; bu yüzden küçük boyut/sıra farkları olabilir.",
    "Bu araç, eski deneyle aynı 'Kais Original' B-çıkış altyapısını kullanır. Canlı V2'nin günlük coin seçimi (hacim "
    "sıralaması) ve TP1=%80 / %70 kapama kuralı bire bir bu altyapıda değildir; sonuçlar canlı sonucu garanti etmez.",
    "Her 60'tan az işlemli küme 'YETERSIZ_ORNEKLEM'dir; yorumlanmaz. Yön ve dönem kırılımlarında örneklem çok küçülür.",
    "Piyasa dönemi etiketi BTC'nin aylık getirisine göredir; kaba bir sınıflandırmadır.",
    "Ortalama R farkı güven aralıkları (varsa) birden çok aday karşılaştırıldığı için Bonferroni düzeltmeli aralıkla "
    "birlikte okunmalıdır (results.json).",
]


def fingerprint(data, locked: dict) -> str:
    app = Path(__file__).parent / "app"
    sources = {name: hashlib.sha256((app / name).read_bytes()).hexdigest() for name in FINGERPRINT_SOURCES}
    return hashlib.sha256(json.dumps({
        "sources": sources, "plan": locked["sha256"], "archives": data.report["sources"],
        "candidates": study.protocol_sha256(),
        "worker_source": hashlib.sha256((Path(__file__).parent / "regime_cli.py").read_bytes()).hexdigest(),
    }, sort_keys=True).encode()).hexdigest()


class CandidateDataset(ResearchDataset):
    """Kararlar, eski deneyin 'filtre KAPALI' önbelleğinden gelir; seçili aday kural üstüne uygulanır."""

    candidate: str | None = None

    def canonical(self, symbol, at, policy):
        if self.candidate is None:
            return super().canonical(symbol, at, policy)
        key = (symbol, at, policy["min_confidence"], policy["mtf_allow_either_timeframe"], False)
        if key not in self.decisions:
            raise ValueError(f"Önbellekte karar yok: {symbol}/{at}")
        return study.apply_candidate(self.candidate, self.decisions[key])


def decision_parity(data: CandidateDataset) -> dict:
    pairs = []
    for key, on in data.decisions.items():
        if key[-1] is True:
            pairs.append((key[:-1], on, data.decisions[(*key[:-1], False)]))
    bad = study.parity_mismatches(pairs)
    return {"status": "BAŞARILI" if not bad else "BAŞARISIZ", "checked": len(pairs),
            "mismatches": len(bad), "first_mismatches": [list(item) for item in bad[:5]]}


def close(a, b, tolerance=1e-9) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= tolerance)


def reference_parity(ordering: str, items: dict, reference: dict | None) -> dict:
    if reference is None:
        return {"status": "ATLANDI", "notes": ["regime-study.json bulunamadı; eski deneyle karşılaştırma yapılamadı."]}
    expected = (reference.get("results") or {}).get(ordering)
    if not expected:
        return {"status": "ATLANDI", "notes": [f"Referansta {ordering} sonucu yok."]}
    notes, failed = [], False
    for name, ref_summary in (("CUR_80", expected.get("B")), ("OFF", (expected.get("SHORT_GATE_OFF") or {}).get("summary"))):
        if name not in items or ref_summary is None:
            notes.append(f"{name}: karşılaştırılamadı (aday çalıştırılmadı ya da referans yok)")
            continue
        mine = items[name]["summary"]
        same = (mine.get("trade_count") == ref_summary.get("trade_count")
                and close(mine.get("net_expectancy_r"), ref_summary.get("net_expectancy_r")))
        failed |= not same
        notes.append(f"{name} {ordering}: işlem {mine.get('trade_count')} vs {ref_summary.get('trade_count')}, "
                     f"ort. R {mine.get('net_expectancy_r')} vs {ref_summary.get('net_expectancy_r')} -> "
                     f"{'AYNI' if same else 'FARKLI'}")
    return {"status": "BAŞARISIZ" if failed else "BAŞARILI", "notes": notes}


def execute(args: argparse.Namespace) -> int:
    root = args.output.resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("Araştırma çıktıları Git dışında kalmalı")
    out = root / "short-filter-compare"
    out.mkdir(parents=True, exist_ok=True)
    locked = lock_plan(root)  # eski kilitle aynı olmak zorunda; farklıysa hata verir, üzerine yazmaz
    plan = locked["plan"]
    names = args.variants or list(study.CANDIDATES)
    unknown = [name for name in names if name not in study.CANDIDATES]
    if unknown:
        raise SystemExit(f"Bilinmeyen aday: {unknown}")
    if study.CONTROL not in names:
        names.insert(0, study.CONTROL)
    if "TP_FIRST" in args.orderings and args.orderings[0] != "STOP_FIRST":
        raise SystemExit("TP_FIRST için önce STOP_FIRST çalışmalı (aynı giriş takvimi).")

    data_root = root / "development"
    development_manifest(data_root, plan)
    config = Config(plan["splits"]["TRAIN"]["evaluate_start"], plan["period"]["end"],
                    conditional_current_metadata=True, policy={
                        "allowed_symbols": plan["research_symbols"], "max_total_exposure_usdt": 350})
    original = load_dataset(data_root, data_root / "current-metadata.json", config.start, config.end)
    data = CandidateDataset(original.frames, original.marks, original.funding, original.metadata,
                            original.report, original.funding_months)
    digest = fingerprint(data, locked)
    print("NATIVE önbellek hazırlanıyor (ilk çalıştırmada uzun sürebilir; sonra yeniden kullanılır)...", flush=True)
    precompute(data, config, args.workers, out / "cache", digest)

    parity = decision_parity(data)
    write_json(out / "decision-parity.json", parity)
    print(f"KARAR_PARITY {parity['status']} kontrol={parity['checked']} uyumsuz={parity['mismatches']}", flush=True)
    if parity["status"] != "BAŞARILI":
        print("Aday kuralın canlı filtreyi yeniden üretemediği görüldü; sonuç üretilmiyor.", flush=True)
        return 1
    if args.check_only:
        print("Yalnızca kontrol istendi; önbellek ve parity hazır.", flush=True)
        return 0

    reference_path = args.reference_regime or root / "regime-study.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8")) if reference_path.exists() else None

    btc = data.frames.get("BTCUSDT", {}).get("15m")
    month_ret = study.month_returns(btc.rows, lambda t: utc_month(t)) if btc is not None else {}

    def label_of(row):
        return study.market_label(month_ret.get(utc(row["opened_at"]).strftime("%Y-%m")))

    def metric_fn(rows):
        return metrics(rows, config.initial_equity)

    results = {"label": "TRAIN+VALIDATION ONLY; NO WINNER; CANLI PARAMETRE DEĞİŞMEDİ",
               "protocol_sha256": study.protocol_sha256(), "fingerprint": digest, "candidates": {
                   name: study.CANDIDATES[name] for name in names},
               "test_candles_loaded": 0, "decision_parity": parity["status"], "orderings": {},
               "limitations": LIMITATIONS, "market_month_returns_pct": month_ret}
    frozen_entries: dict[str, dict] = {}
    reference_notes, reference_failed = [], False
    for ordering in args.orderings:
        current = replace(config, intrabar=ordering)
        items: dict[str, dict] = {}
        for name in names:
            data.candidate = name
            if ordering == "STOP_FIRST":
                native = run(data, current)
                frozen_entries[name] = asyncio.run(prepare_entries(data, current, native))
                a = frozen_replay(data, current, frozen_entries[name], PRESETS["A"])
                verify_control(a, native)
            b = frozen_replay(data, current, frozen_entries[name], PRESETS["B"])
            items[name] = {"summary": summary(b["trades"], current, plan["minimum_complete_trades"]),
                           "periods": cohort_periods(b["trades"], current, plan),
                           "market_regime": study.breakdown(b["trades"], label_of, metric_fn),
                           "trades": b["trades"]}
            export_trades(b["trades"], out / f"{name}-{ordering.lower()}-trades.csv")
            print(f"VARYANT {ordering} {name}: işlem={len(b['trades'])}", flush=True)
        data.candidate = None
        control_rows = items[study.CONTROL]["trades"]
        others = {name: items[name]["trades"] for name in names if name != study.CONTROL}
        contrasts = {}
        if others:
            union, base_mask, masks, values = study.build_union(control_rows, others, complete)
            settings = plan["bootstrap"]
            contrasts = joint_contrasts(union, masks, current, samples=settings["samples"],
                                        family_size=max(len(masks), 1), alpha=settings["alpha"],
                                        base_mask=base_mask, target_values=values)
        rows_out = []
        for name in names:
            item = items[name]
            if name != study.CONTROL:
                item["changes_vs_control"] = trade_set_changes(item["trades"], control_rows, current)
                item["contrast"] = contrasts.get(name)
                changes = study.signal_changes(item["trades"], control_rows)
                write_table(out / f"degisim-{name}-vs-{study.CONTROL}-{ordering.lower()}.csv", changes)
            s = item["summary"]
            rows_out.append({"ordering": ordering, "variant": name, "trade_count": s.get("trade_count"),
                             "net_expectancy_r": s.get("net_expectancy_r"), "profit_factor": s.get("profit_factor"),
                             "win_rate": s.get("win_rate"), "max_drawdown_usdt": s.get("max_drawdown_usdt"),
                             "sample_status": s.get("sample_status")})
        ref = reference_parity(ordering, items, reference)
        reference_notes += ref["notes"]
        reference_failed |= ref["status"] == "BAŞARISIZ"
        write_table(out / f"comparison-{ordering.lower()}.csv", rows_out)
        results["orderings"][ordering] = {"variants": {name: {k: v for k, v in item.items() if k != "trades"}
                                                       for name, item in items.items()}}
    statuses = {"BAŞARISIZ" if reference_failed else ("BAŞARILI" if reference else "ATLANDI")}
    results["reference_parity"] = {"status": statuses.pop(), "notes": reference_notes}
    write_json(out / "results.json", results)
    (out / "REPORT.md").write_text(study.render_report(results), encoding="utf-8")
    print(f"BITTI. Rapor: {out / 'REPORT.md'}  referans_uyumu={results['reference_parity']['status']}", flush=True)
    return 1 if reference_failed else 0


def utc_month(timestamp: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True, help="Stage 5 çıktı klasörü (development/, study-lock.json, regime-study.json)")
    parser.add_argument("--reference-regime", type=Path, default=None, help="Eski deneyin regime-study.json dosyası")
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 5))
    parser.add_argument("--orderings", nargs="+", default=["STOP_FIRST"], choices=["STOP_FIRST", "TP_FIRST"])
    parser.add_argument("--variants", nargs="+", default=None, help=f"Seçenekler: {', '.join(study.CANDIDATES)}")
    parser.add_argument("--check-only", action="store_true", help="Yalnız önbellek + parity kontrolü")
    sys.exit(execute(parser.parse_args()))
