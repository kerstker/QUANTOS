"""Run the strict Binance USDT-M continuous paper replay.

This is the operator-facing entry point for the user's USDT request.  It uses
the shared forward-track strategy registry (the frozen three-Alpha formulas
plus the pre-registered crowd_contra variant CANDIDATEs, same ``_alpha_spec``
normalization), current Binance contract filters, the supplied
4 bps taker benchmark, current public spread calibration, funding and
Almgren--Chriss impact.  It only reads public market data and writes paper
evidence; it has no credential or order path.
"""
from __future__ import annotations

import argparse
import json

from tools.binance_usdc_alpha_retest import DEFAULT_TAKER_BPS, run_retest


DEFAULT_USDT_DAYS = 730
DEFAULT_USDT_LEDGER = "data/cache/binance_usdt_continuous_paper_24m.db"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_USDT_DAYS)
    parser.add_argument("--aum", type=float, default=1_000_000.0)
    parser.add_argument("--fee-bps", type=float, default=DEFAULT_TAKER_BPS)
    parser.add_argument("--participation", type=float, default=0.10)
    parser.add_argument("--metrics-workers", type=int, default=16)
    parser.add_argument(
        "--output",
        default="data/cache/binance_usdt_continuous_paper.json",
    )
    parser.add_argument("--ledger", default=DEFAULT_USDT_LEDGER)
    args = parser.parse_args()
    report = run_retest(
        quote_asset="USDT",
        days=args.days,
        aum=args.aum,
        fee_bps=args.fee_bps,
        participation=args.participation,
        output_path=args.output,
        metrics_workers=args.metrics_workers,
        ledger_path=args.ledger,
    )
    print(json.dumps({
        "quote_asset": report["quote_asset"],
        "classification": report["classification"],
        "paper_verdict": report["paper_verdict"],
        "deployment_gate": report["deployment_gate"],
        "risk_gate": {
            "mode": report["risk_gate"]["mode"],
            "all_passed": report["risk_gate"]["all_passed"],
            "strategies": {
                key: {
                    "decision": value["decision"],
                    "status": value["status"],
                    "reasons": value["reasons"],
                }
                for key, value in report["risk_gate"]["strategies"].items()
            },
        },
        "continuous_paper": report["continuous_paper"],
        "simulations": {
            key: {
                "net_pnl": value["net_pnl"],
                "net_return": value["stats"]["total_return"],
                "sharpe": value["stats"]["sharpe"],
                "max_drawdown": value["stats"]["max_drawdown"],
                "fee_cost": value["fee_cost"],
                "spread_cost": value["spread_cost"],
                "impact_cost": value["impact_cost"],
                "funding_pnl": value["funding_pnl"],
                "n_rebalances": value["n_rebalances"],
                "n_trades": value["n_trades"],
                "reconciliation_residual": value["reconciliation_residual"],
            }
            for key, value in report["simulations"].items()
        },
        "output": args.output,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
