"""Rank #19 — offline snapshot rebuild orchestrator.

Regenerates the load-bearing offline snapshots from the cached DBs in dependency order, then
rebuilds the committed evidence digests (tools.build_evidence_digests). A generator whose input DB
is absent (network-fetched roots are gitignored / dead in CI) is SKIPPED honestly, never faked.
Network-free: it runs only the offline `*_research` / `*_offline` / `dry_run_*` generators (which
read cached DBs), never the network fetchers. Wall-clock/git only at this tool boundary.

  python -m tools.rebuild_snapshots           # list the plan (what would rebuild vs skip)
  python -m tools.rebuild_snapshots --run      # execute in dependency order, then rebuild digests
"""
from __future__ import annotations

import argparse
import importlib
import os

from services.execution.backtest_authority import quarantine_legacy_backtest

_CACHE = os.path.join("data", "cache")

# (label, generator module, the cached input DB it needs) — dependency order: raw DBs already exist;
# capacity/dry-run first (funding), then the macro/tail/panel diagnostics, then digests.
_GENERATORS = [
    ("capacity", "tools.capacity_research", "crypto_funding.db"),
    ("dry_run_carry", "tools.dry_run_carry_multi", "crypto_funding.db"),
    ("dry_run_trend", "tools.dry_run_trend_multi", "crypto_daily.db"),
    ("dry_run_crowd_contra", "tools.dry_run_crowd_contra_multi", "crypto_funding.db"),
    ("dry_run_combo", "tools.dry_run_combo_multi", "crypto_funding.db"),
    ("dry_run_riskparity", "tools.dry_run_combo_riskparity_multi", "crypto_funding.db"),
    ("macro_regime", "tools.macro_regime_research", "macro_fred.db"),
    ("combo_book", "tools.combo_book_research", "macro_fred.db"),
    ("scenario_tail", "tools.scenario_tail_research", "crypto_daily.db"),
    ("ic_panel", "tools.ic_panel_research", "crypto_history.db"),
    ("crypto_research", "tools.crypto_research_offline", "crypto.db"),
    ("ml_stack", "tools.ml_stack_research", "crypto_history.db"),
    ("barra_lite", "tools.barra_lite_diagnostics", "crypto_funding.db"),
]


def plan() -> list:
    """The ordered rebuild plan with per-generator input-DB presence (deterministic)."""
    return [{"label": label, "module": module, "input_db": db,
             "input_present": os.path.exists(os.path.join(_CACHE, db))}
            for label, module, db in _GENERATORS]


def run(execute: bool = False) -> dict:
    if execute:
        # Fail before importing any generator.  Catching a quarantine from one
        # child and continuing would still let later V1 engines overwrite
        # retained Sharpe/PnL artifacts before the final digest step failed.
        quarantine_legacy_backtest("tools.rebuild_snapshots")
    ran, skipped, failed = [], [], []
    for step in plan():
        if not step["input_present"]:
            skipped.append(step["label"])
            print(f"  SKIP  {step['label']:22} (input {step['input_db']} absent)", flush=True)
            continue
        if not execute:
            print(f"  PLAN  {step['label']:22} <- {step['input_db']}", flush=True)
            continue
        try:
            importlib.import_module(step["module"]).main()
            ran.append(step["label"])
            print(f"  OK    {step['label']}", flush=True)
        except Exception as exc:                       # a generator failure is recorded, never a fake snapshot
            failed.append((step["label"], f"{type(exc).__name__}: {exc}"))
            print(f"  FAIL  {step['label']}: {exc}", flush=True)
    if execute:
        importlib.import_module("tools.build_evidence_digests").main()
        print("  OK    evidence digests rebuilt", flush=True)
    return {"ran": ran, "skipped": skipped, "failed": failed}


def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild offline snapshots + evidence digests (Rank #19).")
    ap.add_argument("--run", action="store_true", help="execute the generators (default: list the plan)")
    args = ap.parse_args()
    print("=== offline snapshot rebuild plan (dependency order) ===", flush=True)
    result = run(execute=args.run)
    if args.run:
        print(f"=== rebuilt {len(result['ran'])} · skipped {len(result['skipped'])} · "
              f"failed {len(result['failed'])} ===", flush=True)


if __name__ == "__main__":
    main()
