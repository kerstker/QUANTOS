"""Rank #19 — reproducibility base: build committed `evidence/*.json` digests of the load-bearing
numbers that otherwise live ONLY in gitignored data/cache/. A digest records each number + a
tolerance + the code version that produced it, so the repo can self-verify its cited conclusions
(the gate test tests/reproducibility/test_evidence_snapshots.py checks them when the cache is
present, skips honestly in CI). Re-run this after a driver refresh to update the digests.

Network-free: reads only the local offline artifacts. Wall-clock/git only at this tool boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path

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


def _require_provenance(snap: dict, expected: str) -> None:
    actual = snap.get("provenance")
    if actual != expected:
        raise ValueError(f"snapshot provenance must be {expected!r}, got {actual!r}")


def extract_scenario_tail(snap: dict) -> dict:
    """Historical-replay crisis and endogenous-tail findings for both deployable books."""
    _require_provenance(snap, "REAL_HISTORICAL_REPLAY")
    out = {}
    for name in ("carry", "crowd_contra"):
        book = snap["books"][name]
        crises = book["crises"]
        out.update(
            {
                f"{name}.n_periods": float(book["n_periods"]),
                f"{name}.worst_drawdown": float(
                    book["worst_drawdown_window"]["drawdown"]
                ),
                f"{name}.cvar_95": float(book["var_cvar_95"]["cvar"]),
                f"{name}.hill_tail_index": float(book["hill_tail_index"]),
                f"{name}.lower_tail_dep_vs_btc": float(
                    book["lower_tail_dep_vs_btc"]
                ),
                f"{name}.luna_total_return": float(
                    crises["2022-05 LUNA"]["total_return"]
                ),
                f"{name}.ftx_total_return": float(
                    crises["2022-11 FTX"]["total_return"]
                ),
            }
        )
    return out


def extract_macro_regime(snap: dict) -> dict:
    """Retrospective macro-regime complementarity plus the honest coverage limits."""
    _require_provenance(snap, "REAL_FRED_MACRO")
    alfred_present = snap.get("alfred_present")
    if not isinstance(alfred_present, bool):
        raise ValueError("macro snapshot alfred_present must be a boolean")

    out = {"macro.alfred_present": float(alfred_present)}
    for name in ("carry", "crowd_contra"):
        book = snap["books"][name]
        dims = book["dims"]
        vix = dims["vol_VIX"]["buckets"]
        curve = dims["rates_2s10s"]["buckets"]
        out.update(
            {
                f"{name}.n_periods": float(book["n_periods"]),
                f"{name}.vix_high_sharpe": float(vix["high"]["ann_sharpe"]),
                f"{name}.vix_low_sharpe": float(vix["low"]["ann_sharpe"]),
                f"{name}.curve_normal_sharpe": float(
                    curve["normal"]["ann_sharpe"]
                ),
                f"{name}.curve_inverted_sharpe": float(
                    curve["inverted"]["ann_sharpe"]
                ),
                f"{name}.credit_covered_periods": float(
                    dims["credit_HYOAS"]["covered_periods"]
                ),
            }
        )
    return out


EXTRACTORS = {
    "ic_panel": extract_ic_panel,
    "capacity": extract_capacity,
    "scenario_tail": extract_scenario_tail,
    "macro_regime": extract_macro_regime,
}


def _git_sha(repo_root: str | os.PathLike[str] = ".") -> str:
    try:
        root = Path(repo_root)
        dot_git = root / ".git"
        if dot_git.is_file():
            marker = dot_git.read_text(encoding="utf-8").strip()
            if not marker.startswith("gitdir:"):
                return "unknown"
            declared = Path(marker.split(":", 1)[1].strip())
            git_dir = declared if declared.is_absolute() else (root / declared).resolve()
        else:
            git_dir = dot_git

        common_dir = git_dir
        common_marker = git_dir / "commondir"
        if common_marker.is_file():
            declared = Path(common_marker.read_text(encoding="utf-8").strip())
            common_dir = (
                declared if declared.is_absolute() else (git_dir / declared).resolve()
            )

        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:12]

        ref = head.split(":", 1)[1].strip()
        for base in dict.fromkeys((git_dir, common_dir)):
            loose_ref = base / ref
            if loose_ref.is_file():
                return loose_ref.read_text(encoding="utf-8").strip()[:12]
            packed_refs = base / "packed-refs"
            if packed_refs.is_file():
                for line in packed_refs.read_text(encoding="utf-8").splitlines():
                    if line.startswith(("#", "^")):
                        continue
                    sha, _, packed_ref = line.partition(" ")
                    if packed_ref == ref:
                        return sha[:12]
        return "unknown"
    except (OSError, UnicodeError, ValueError):
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
        "scenario_tail.json": build_json_digest(
            "scenario_tail_snapshot.json",
            "scenario_tail",
            rel_tol=0.005,
            abs_tol=0.001,
        ),
        "macro_regime.json": build_json_digest(
            "macro_regime_snapshot.json",
            "macro_regime",
            rel_tol=0.005,
            abs_tol=0.01,
        ),
    }
    for name, digest in digests.items():
        path = os.path.join(_EVIDENCE, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(digest, fh, indent=2, sort_keys=True)
        n = len(digest.get("books", digest.get("numbers", {})))
        print(f"wrote {path}: {n} numbers · code {digest['code_version']}", flush=True)


if __name__ == "__main__":
    main()
