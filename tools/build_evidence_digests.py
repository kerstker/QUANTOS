"""Rank #19 — reproducibility base: build committed `evidence/*.json` digests of the load-bearing
numbers that otherwise live ONLY in gitignored data/cache/. A digest records each number + a
tolerance + the code version that produced it, so the repo can self-verify its cited conclusions
(the gate test tests/reproducibility/test_evidence_snapshots.py checks them when the cache is
present, skips honestly in CI). Re-run this after a driver refresh to update the digests.

Network-free: reads only the local offline artifacts. Wall-clock/git only at this tool boundary."""
from __future__ import annotations

import json
import os

_EVIDENCE = "evidence"
_DRY_RUN_DB = os.path.join("data", "cache", "dry_run.db")


def _git_sha() -> str:
    try:
        head = open(os.path.join(".git", "HEAD"), encoding="utf-8").read().strip()
        if head.startswith("ref:"):
            return open(os.path.join(".git", head.split(" ", 1)[1].strip()), encoding="utf-8").read().strip()[:12]
        return head[:12]
    except Exception:
        return "unknown"


def build_dry_run_digest() -> dict:
    """Digest the deployable dry-run book conclusions (return/sharpe/maxdd) + the reconciliation
    residual bound — the most-cited load-bearing numbers in the docs."""
    from services.execution.dry_run_store import DryRunStore
    books = {}
    for r in DryRunStore(_DRY_RUN_DB).all_runs():
        pnl = r["pnl"] if isinstance(r["pnl"], dict) else json.loads(r["pnl"])
        books[r["strategy_id"]] = {
            "total_return": round(float(pnl["total_return"]), 6),
            "sharpe": round(float(pnl["sharpe"]), 4),
            "max_drawdown": round(float(pnl["max_drawdown"]), 4),
        }
    return {
        "artifact": _DRY_RUN_DB.replace("\\", "/"),
        "kind": "dry_run_db",
        "code_version": _git_sha(),
        "description": "Deployable dry-run book conclusions; reconciliation residual must stay ~0.",
        "residual_max": 1e-6,
        "tolerances": {"total_return": 0.02, "sharpe": 0.02, "max_drawdown": 0.01},
        "books": books,
    }


def main() -> None:
    os.makedirs(_EVIDENCE, exist_ok=True)
    digests = {"dry_run_books.json": build_dry_run_digest()}
    for name, digest in digests.items():
        path = os.path.join(_EVIDENCE, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(digest, fh, indent=2, sort_keys=True)
        print(f"wrote {path}: {len(digest.get('books', {}))} books · code {digest['code_version']}", flush=True)


if __name__ == "__main__":
    main()
