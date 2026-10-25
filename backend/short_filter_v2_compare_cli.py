"""SHORT-filtre karşılaştırması: Kais Original V2 hattında (çevrimdışı; ağ yok, canlıya bağlı değil).

V2'nin kendi ön tarama düzenini kullanır (aynı ölçüm görünümü 2020-10..2022-08, aynı V2 profili, aynı motor,
aynı maliyet/çıkış kuralları). Mühürlü TEST'e dokunmaz (veri yükleyici ve yol bekçisi zaten reddeder).

Aday SHORT kuralları (app/short_filter_study.py), motorun karar fonksiyonuna 'filtre KAPALI' karar üzerinden
uygulanır; aday yalnız giriş eler. Önce güvenilirlik: kontrol kuralı CUR_80, kayıtlı run3 sayımlarını birebir
yeniden üretmeli ve eski ön tarama sonucuyla (işlem sayısı, ortalama R) aynı çıkmalı; olmazsa sonuç üretilmez.
Hiçbir aday "kazanan" ilan edilmez.

Kullanım (Windows PowerShell, repo klasöründe, sanal ortamla):
  .\\.venv\\Scripts\\python.exe backend\\short_filter_v2_compare_cli.py --metadata C:\\Users\\<kullanıcı>\\kaistrade-data\\donchian-holdout-evidence\\current-metadata.json --variants CUR_80
  (önce yalnız kontrol: --variants CUR_80 ; sonra bütün adaylar için --variants vermeden)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import measure_original_gap_counts as gap
import prescreen_original_v2 as pre
from app import short_filter_study as study
from build_measurement_view import load_measurement_dataset, sha256_file
from measure_donchian_counts import (
    DATA_HOME, PHASES, VIEW, MeasurementError, deny_network, emit, locked_plan, primary_config, quiet_native,
    validate_loaded_scope,
)
from measure_original_counts import METADATA, VIEW_SHA

DEFAULT_OUTPUT_NAME = "short-filter-v2-compare"
OUTPUT = DATA_HOME / DEFAULT_OUTPUT_NAME
PRESCREEN_REFERENCE = DATA_HOME / "original-v2-prescreen" / "prescreen.json"
SCHEMA = "short-filter-v2-compare-v1"
LIMITATIONS = [
    "Veri: 2020-10..2022-08 (TRAIN: yükseliş ağırlıklı 2021, VALIDATION: 2022 ayı piyasası). V2'nin kuralları bu pencerede "
    "geliştirildi; bu pencere artık 'temiz' değil. Sonuçlar kanıt değil, ölçümdür; asıl hakem canlı/ileri veridir.",
    "Aday sayısı arttıkça aynı veride 'şans eseri iyi görünen' aday çıkma ihtimali artar. Çoklu karşılaştırma düzeltmesi "
    "yoktur; yalnız her adayın kendi ortalama R aralığı (bilgi amaçlı) verilir.",
    "Aday kurallar yalnızca giriş ELER (filtre-KAPALI kümenin alt kümesi). Elenen girişin yerine başka giriş açılması "
    "ve sıra/portföy farkları motor tarafından yeniden hesaplanır.",
    "Kâr/zarar dolar cinsinden portföy tavanına (350 USDT toplam pozisyon) ve motorun risk kapılarına tabidir; küçük "
    "alt kümelerde (yön, piyasa dönemi) örneklem çok azalır. 60'tan az işlem 'YETERSIZ_ORNEKLEM'dir.",
    "Canlı V2'nin günlük hacim sıralamasıyla coin seçimi ve TP1=%80 / %70 kapama kuralı bu çevrimdışı motorda bire bir "
    "aynı değildir (motor, ön tarama profilini kullanır). Sonuçlar canlı sonucu garanti etmez.",
    "Piyasa dönemi etiketi BTC'nin aylık getirisine göredir; kaba bir sınıflandırmadır.",
    "EXHAUST_80 ve EXHAUST_ANY, ilk 7 adayın sonuçları GÖRÜLDÜKTEN sonra tasarlandı. Eşikleri yoktur (trap_score'un kendi "
    "sabitleri), yalnız iki adaydır ve sonuca göre ayar yapılmamıştır; yine de bu veri bu aday ailesi için 'kör' değildir. "
    "Toplam aday sayısı 9'dur ve çoklu karşılaştırma düzeltmesi yoktur.",
    "Hiçbir kural 17 canlı işlemden hareketle seçilmedi ya da ayarlanmadı.",
]


def observation_row(obs: Any) -> dict[str, Any]:
    return {"signal_id": obs.signal_id, "symbol": obs.symbol, "direction": obs.direction,
            "opened_at": datetime.fromtimestamp(obs.opened_at, timezone.utc).isoformat(),
            "closed_at": datetime.fromtimestamp(obs.closed_at, timezone.utc).isoformat(),
            "net_r": obs.net_r, "net_pnl": float(obs.net)}


def write_table(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row}) or ["empty"]
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def brief(observations: list[Any]) -> dict[str, Any]:
    if not observations:
        return {"N": 0, "mean_net_r": None, "usdt_pf": None}
    metrics, _ = pre.summarize(observations)
    return {"N": metrics["N"], "mean_net_r": metrics["mean_net_r"], "usdt_pf": metrics["usdt_pf"]}


def month_key(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m")


async def run(args: argparse.Namespace, out: Path, stream) -> dict[str, Any]:
    from app.strategies.original_gap_engine import OriginalGapRiskEngine

    plan = locked_plan()
    pre.require(sha256_file(args.metadata) == plan["input_sha256"]["metadata"], "LOCKED_METADATA_MISMATCH")
    pre.require(sha256_file(VIEW / "manifest.json") == VIEW_SHA, "LOCKED_VIEW_MISMATCH")
    full_references = pre.load_references()
    references = {name: value["phases"][name] for name, value in full_references.items()}
    # Eski ön taramanın "kaynak dosyası değişmedi" kilidi burada UYGULANMAZ: o tarihten beri uygulama dosyaları
    # (main.py, v25_execution.py ...) başka işler için değişti. Yerine iki sert kontrol var: kayıtlı run3 sayımlarıyla
    # birebir eşleşme ve eski ön tarama sonucuyla eşleşme. Farklı olan dosyalar bilgi için listelenir.
    recorded = full_references["TRAIN"]["provenance"]["source_sha256"]
    current = gap.provenance(plan)
    changed_sources = sorted(name for name, digest in current["source_sha256"].items() if recorded.get(name) != digest)
    record = {"profile_hash": pre.PROFILE_HASH, "run3_report_sha256": pre.RUN3_HASHES,
              "sources_changed_since_run3": changed_sources}
    data = load_measurement_dataset(VIEW, args.metadata, PHASES[0].start, PHASES[-1].end)
    validate_loaded_scope(data, plan["symbols"])

    btc = data.frames.get("BTCUSDT", {}).get("15m")
    month_ret = study.month_returns(btc.rows, month_key) if btc is not None else {}
    label_of = lambda obs: study.market_label(month_ret.get(month_key(obs.opened_at)))  # noqa: E731

    prescreen = json.loads(PRESCREEN_REFERENCE.read_text(encoding="utf-8")) if PRESCREEN_REFERENCE.exists() else None
    names = args.variants or list(study.CANDIDATES)
    unknown = [name for name in names if name not in study.CANDIDATES]
    pre.require(not unknown, f"UNKNOWN_VARIANT:{','.join(unknown)}")
    if study.CONTROL not in names:
        names.insert(0, study.CONTROL)
    names.sort(key=lambda name: name != study.CONTROL)

    results: dict[str, Any] = {"schema": SCHEMA, "label": "TRAIN+VALIDATION ONLY; NO WINNER; CANLI PARAMETRE DEĞİŞMEDİ",
                               "protocol_sha256": study.protocol_sha256(), "provenance": record,
                               "candidates": {name: study.CANDIDATES[name] for name in names},
                               "limitations": LIMITATIONS, "phases": {}, "anchors": {"notes": []}}
    results["anchors"]["notes"].append(
        "Eski kaynak-dosya kilidi uygulanmadı; run3'ten beri değişen dosyalar: "
        + (", ".join(changed_sources) if changed_sources else "yok"))
    held: dict[str, dict[str, list[Any]]] = {}
    features: dict[tuple[str, int], dict[str, Any]] = {}
    from app import main as native_main
    from app.regime_study import short_gate_disabled

    with study.VariantGate(native_main, short_gate_disabled) as gate:
        for name in names:
            gate.variant = name
            held[name] = {}
            for phase in PHASES:
                gate.phase = phase.name
                engine = OriginalGapRiskEngine(data, primary_config(phase, plan))
                counts = await gap.replay_counts(engine, phase, lambda value: emit(stream, value))
                if name == study.CONTROL:
                    pre.assert_count_parity(counts, references[phase.name], phase.name)  # kayıtlı run3 ile birebir
                observations = pre.verified_observations(engine.trades)
                held[name][phase.name] = observations
                emit(stream, {"status": "variant_done", "variant": name, "phase": phase.name, "N": len(observations)})
        features = dict(gate.features)
    results["anchors"]["count_parity"] = "BAŞARILI"  # assert_count_parity başarısız olsaydı yukarıda hata verirdi

    prescreen_ok = None
    for phase in PHASES:
        mine = brief(held[study.CONTROL][phase.name])
        if prescreen is None:
            results["anchors"]["notes"].append(f"{phase.name}: ön tarama sonucu bulunamadı ({PRESCREEN_REFERENCE})")
            continue
        ref = prescreen["phases"][phase.name]["metrics"]
        same = mine["N"] == ref["N"] and abs(mine["mean_net_r"] - ref["mean_net_r"]) <= 1e-9
        prescreen_ok = (prescreen_ok is not False) and same
        results["anchors"]["notes"].append(
            f"{phase.name}: işlem {mine['N']} vs {ref['N']}, ort. R {mine['mean_net_r']} vs {ref['mean_net_r']} -> "
            f"{'AYNI' if same else 'FARKLI'}")
    results["anchors"]["prescreen_parity"] = "ATLANDI" if prescreen_ok is None else "BAŞARILI" if prescreen_ok else "BAŞARISIZ"

    for phase in [*PHASES, None]:
        label = phase.name if phase else "TRAIN+VALIDATION"
        block: dict[str, Any] = {}
        for name in names:
            observations = held[name][phase.name] if phase else [o for p in PHASES for o in held[name][p.name]]
            metrics, costs = pre.summarize(observations) if observations else ({"N": 0}, {})
            item: dict[str, Any] = {"metrics": metrics, "costs": costs,
                                    "by_direction": {side: brief([o for o in observations if o.direction == side])
                                                     for side in ("LONG", "SHORT")}}
            buckets: dict[tuple[str, str], list[Any]] = {}
            for obs in observations:
                buckets.setdefault((label_of(obs), obs.direction), []).append(obs)
            regime: dict[str, dict[str, Any]] = {}
            for (market, side), rows in sorted(buckets.items()):
                regime.setdefault(market, {})[side] = brief(rows)
            item["market_regime"] = regime
            if phase and observations:
                interval = pre.bootstrap_mean(observations, phase)["methods"]
                first = next(iter(interval.values()), None)
                item["bootstrap"] = interval
                item["mean_r_95"] = first["mean_net_r_95"] if first and first["status"] else None
            if name != study.CONTROL:
                control = {o.signal_id: o for o in (held[study.CONTROL][phase.name] if phase else
                                                    [o for p in PHASES for o in held[study.CONTROL][p.name]])}
                mine_ids = {o.signal_id: o for o in observations}
                added = [o for i, o in mine_ids.items() if i not in control]
                removed = [o for i, o in control.items() if i not in mine_ids]
                item["changes_vs_control"] = {"added": brief(added), "removed": brief(removed)}
                rows = study.signal_changes([observation_row(o) for o in observations],
                                            [observation_row(o) for o in control.values()])
                write_table(out / f"degisim-{name}-vs-{study.CONTROL}-{label.replace('+', '_')}.csv", rows)
            block[name] = item
        results["phases"][label] = block
        for name in names:
            observations = held[name][phase.name] if phase else [o for p in PHASES for o in held[name][p.name]]
            write_table(out / f"islemler-{name}-{label.replace('+', '_')}.csv", [observation_row(o) for o in observations])

    # Yeni (tükenme) adaylar için önceden sabit kanıt ölçütü: tabana göre iki dönemde de anlamlı iyileşme var mı?
    evidence = study.build_evidence(
        names, {name: {phase.name: [float(o.net_r) for o in held[name][phase.name]] for phase in PHASES} for name in names},
        [phase.name for phase in PHASES])
    if evidence:
        results["evidence"] = evidence

    # Çakışma analizi: filtre-KAPALI (OFF) işlemleri ve sinyalleri üstünden (OFF çalıştırılmadıysa atlanır).
    if "OFF" in names:
        results["overlap"] = study.build_overlap(features, {
            phase.name: [{**observation_row(o), "opened_at_epoch": int(o.opened_at)} for o in held["OFF"][phase.name]]
            for phase in PHASES})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--metadata", required=True, type=Path, help="donchian-holdout-evidence\\current-metadata.json")
    parser.add_argument("--variants", nargs="+", default=None, help=f"Seçenekler: {', '.join(study.CANDIDATES)}")
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME,
                        help="kaistrade-data altında oluşturulacak YENİ klasör adı (var olan klasörün üstüne yazmaz)")
    args = parser.parse_args(argv)
    loop, stream = None, sys.stdout
    try:
        pre.require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", args.output_name) is not None, "BAD_OUTPUT_NAME")
        output = DATA_HOME / args.output_name
        pre.require(not output.exists(), f"RESULTS_ALREADY_EXIST:{output} (yeniden çalıştırmak için klasörü taşıyın)")
        pre.require(args.metadata.resolve() == METADATA.resolve(), "METADATA_COPY_REQUIRED")
        output.mkdir(parents=True, exist_ok=False)
        os.environ.update({"DATA_DIR": str(output / "runtime"), "PROTREBOT_DATA_DIR": str(output / "runtime"),
                           "DATABASE_URL": "", "PROTREBOT_DURABLE_AUTH_REQUIRED": "0", "ASSISTANT_LIVE_TESTS": "0"})
        loop = asyncio.new_event_loop()
        started = time.monotonic()
        with quiet_native(), deny_network():
            results = loop.run_until_complete(run(args, output, stream))
        results["elapsed_seconds"] = time.monotonic() - started
        (output / "results.json").write_text(json.dumps(results, indent=2, allow_nan=False), encoding="utf-8")
        (output / "REPORT.md").write_text(study.render_report(results), encoding="utf-8")
        emit(stream, {"status": "completed", "report": str(output / "REPORT.md"),
                      "prescreen_parity": results["anchors"]["prescreen_parity"]})
        return 1 if results["anchors"]["prescreen_parity"] == "BAŞARISIZ" else 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ArithmeticError) as exc:
        emit(stream, {"status": "failed", "code": str(exc) if isinstance(exc, MeasurementError) else "NATIVE_FAILURE",
                      "error_class": type(exc).__name__})
        return 2
    finally:
        if loop is not None:
            loop.close()


if __name__ == "__main__":
    with gap.previous.research_path_guard():
        raise SystemExit(main())
