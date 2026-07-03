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
_CACHE = os.path.join("data", "cache")


# --- shared numeric extractors (the gate test imports these to re-verify the live artifact the
#     SAME way the digest was built — one source of extraction logic, no drift between build/verify) ---

def extract_ic_panel(snap: dict) -> dict:
    """The crowd_contra cross-sectional IC finding (the famous t-stat 4.32)."""
    row = next(r for r in snap["rows"]
               if r.get("name") == "crowd_contra" and r.get("universe") == "crypto_oi_majors")
    return {"crowd_contra.t_stat": float(row["t_stat"]),
            "crowd_contra.mean_ic": float(row["mean_ic"]),
            "crowd_contra.icir": float(row["icir"])}


def extract_capacity(snap: dict) -> dict:
    """Honest capacity = participation-bound (carry ~$2M / crowd ~$7M); impact-only overstates it
    to ~$B (the ~300x finding). Digest the binding caps + the overstatement ratio."""
    b = snap["books"]
    out = {}
    for name in ("carry", "crowd_contra"):
        cap = float(b[name]["participation_cap_aum"])
        out[f"{name}.participation_cap_aum"] = cap
        out[f"{name}.impact_over_participation_ratio"] = float(b[name]["impact_aum_high"]) / cap
    return out


EXTRACTORS = {"ic_panel": extract_ic_panel, "capacity": extract_capacity}


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


def build_json_digest(artifact_name: str, extractor: str, rel_tol: float, abs_tol: float) -> dict:
    """Digest numeric findings from an offline snapshot JSON via a named shared extractor. Each
    number passes if within max(abs_tol, rel_tol*|value|) — relative tol suits large AUM numbers."""
    artifact = os.path.join(_CACHE, artifact_name)
    snap = json.load(open(artifact, encoding="utf-8"))
    numbers = {}
    for key, value in EXTRACTORS[extractor](snap).items():
        numbers[key] = {"value": round(float(value), 6), "rel_tol": rel_tol, "abs_tol": abs_tol}
    return {"artifact": os.path.join(_CACHE, artifact_name).replace("\\", "/"),
            "kind": "json_numbers", "extractor": extractor, "code_version": _git_sha(),
            "numbers": numbers}


def main() -> None:
    os.makedirs(_EVIDENCE, exist_ok=True)
    digests = {
        "dry_run_books.json": build_dry_run_digest(),
        "ic_panel.json": build_json_digest("ic_panel_snapshot.json", "ic_panel", rel_tol=0.005, abs_tol=0.001),
        "capacity.json": build_json_digest("capacity_snapshot.json", "capacity", rel_tol=0.01, abs_tol=1.0),
    }
    for name, digest in digests.items():
        path = os.path.join(_EVIDENCE, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(digest, fh, indent=2, sort_keys=True)
        n = len(digest.get("books", digest.get("numbers", {})))
        print(f"wrote {path}: {n} numbers · code {digest['code_version']}", flush=True)


if __name__ == "__main__":
    main()
