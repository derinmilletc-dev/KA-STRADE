"""dca_parity.json üretir: backend planının TP / en kötü zarar sonuçları. Frontend (`frontend/src/dca.test.mts`) AYNI dosyayı okuyup aynı sayıları üretmeli.
Çalıştırma: python tests/make_dca_parity_fixture.py   (sabitler değişirse yeniden üretilir; test_dca_plan eski dosyayı yakalar)"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.dca_engine.plan import DcaPlanError, limits_payload, prepare_dca_plan  # noqa: E402

RULES = {"tick": 0.01, "step": 0.001, "min_qty": 0.001, "min_notional": 5}
LOOSE = {"max_leverage": 5, "max_loss_per_trade": 1000, "daily_loss_limit": 1000, "max_total_exposure_usdt": 100000, "max_positions": 10,
         "max_stop_distance_pct": 50}
BASE = dict(direction="LONG", price=100.0, base_margin_usdt=10.0, safety_orders=3, step_pct=1.5, volume_multiplier=1.5, take_profit_pct=1.0,
            stop_gap_pct=1.5, leverage=3)
CASES = [
    {}, {"direction": "SHORT"}, {"safety_orders": 5, "step_pct": 1.0, "stop_gap_pct": 1.0, "volume_multiplier": 1.2},
    {"take_profit_pct": 0.5}, {"take_profit_pct": 0.2}, {"take_profit_pct": 0.2, "direction": "SHORT"}, {"take_profit_pct": 0.25, "leverage": 5},
    {"price": 0.5, "base_margin_usdt": 20.0}, {"take_profit_pct": 0.3, "safety_orders": 5, "step_pct": 0.5, "stop_gap_pct": 0.5},
]


def build(policy):
    out = []
    for extra in CASES:
        args = {**BASE, **extra}
        row = {"args": args}
        try:
            plan = prepare_dca_plan(**args, rules=RULES, policy=policy)
            row.update(ok=True, tp_prices=[r["tp_price"] for r in plan["stages"]], tp_net_after_costs=[r["tp_net_profit_after_costs_usdt"] for r in plan["stages"]],
                       tp_net_fee_only=[r["tp_net_profit_usdt"] for r in plan["stages"]], worst_loss=plan["worst_loss_usdt"])
        except DcaPlanError as exc:
            text = str(exc)
            row.update(ok=False, tp_rejected=("net kâr" in text or "işlem ücretini" in text), message=text)
        out.append(row)
    return out


def fixture():
    return {"costs": limits_payload()["costs"], "rules": RULES, "cases": build(LOOSE)}


if __name__ == "__main__":
    path = pathlib.Path(__file__).parent / "fixtures" / "dca_parity.json"
    path.write_text(json.dumps(fixture(), ensure_ascii=False, indent=1) + "\n")
    print("yazıldı:", path)
