"""Read-only HTTP route for the A-share micro-cap alpha research stack.

Serves the persisted research artifacts under ``data/stocks/snapshots/``
(alpha_snapshot.json + paper_track_snapshot.json + klines_snapshot.json) as one
compact, read-only console snapshot. Fail-closed: when the artifacts are absent
(CI / fresh checkout) the route returns the honest ``SYNTHETIC_DETERMINISTIC``
marker with the same top-level shape — never fabricated figures.

The basket block is champion-consistent: stats come from the train-selected
turnover-cap champion (``turnover_cap_sensitivity.train_champion_cap``) and the
equity curve is compounded from the paper-track weekly net returns, so
basket / dry_run / paper_track / variants all describe the SAME cap30 champion.
holdings_latest is the uncapped baseline basket's final-week weights (the only
persisted holdings surface) — labelled in honest_limitations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter

from quantos.runtime_paths import runtime_state_root

router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SNAPSHOT_NAMES = (
    "alpha_snapshot.json",
    "paper_track_snapshot.json",
    "klines_snapshot.json",
)

REAL_PROVENANCE = "REAL_ASHARE_ALPHA_SNAPSHOT"
_INSTITUTIONAL_SEARCH_PATH = _REPO_ROOT / "evidence" / "ashare_alpha_search_tdx_20260830.json"


def _snapshot_paths(root: Path) -> tuple[Path, Path, Path]:
    directory = root / "data" / "stocks" / "snapshots"
    return tuple(directory / name for name in _SNAPSHOT_NAMES)


def _load_bundle(root: Path) -> tuple[dict, dict, dict] | None:
    loaded = tuple(_load(str(path)) for path in _snapshot_paths(Path(root)))
    if all(value is not None for value in loaded):
        return loaded  # type: ignore[return-value]
    return None


def _ashare_snapshot_bundle() -> tuple[dict, dict, dict] | None:
    state_bundle = _load_bundle(runtime_state_root(cwd=_REPO_ROOT))
    return state_bundle if state_bundle is not None else _load_bundle(_REPO_ROOT)


def _load(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _compound(weekly: List[dict], key: str) -> List[float]:
    """Cum-product a weekly return series into an equity curve (starts at 1.0)."""
    equity = [1.0]
    for row in weekly:
        try:
            equity.append(equity[-1] * (1.0 + float(row[key])))
        except (KeyError, TypeError, ValueError):
            return []
    return [round(v, 6) for v in equity]


def _synthetic_payload(reason: str) -> Dict:
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "provenance": "SYNTHETIC_DETERMINISTIC",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": False,
        "generated_at": "UNKNOWN",
        "data_freshness": {"stocks": 0, "bars": 0, "last_date": 0,
                           "sources": {"wind": 0, "ifind": 0}},
        "universe": {"file_count": 0, "used": 0},
        "factors": [],
        "combo": {"champion": "", "weights": {}, "h1_test_ic": 0.0, "h1_test_t": 0.0,
                  "h5_test_ic": 0.0, "h5_test_t": 0.0, "hit_rate": 0.0},
        "basket": {"n": 0, "aum_cny": 0.0, "cost_bps": 0.0,
                   "test": {"total_return": 0.0, "sharpe": 0.0, "max_dd": 0.0, "avg_turnover": 0.0},
                   "bench_test": {"total_return": 0.0, "sharpe": 0.0, "max_dd": 0.0},
                   "equity_test": [], "bench_equity_test": []},
        "variants": [],
        "dry_run": {"run_id": "", "total_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                    "n_approved": 0, "n_rejected": 0, "residual": 0.0},
        "paper_track": {"alpha_id": "", "n_weeks": 0, "cum_return": 0.0, "sharpe": 0.0,
                        "weekly_ic_mean": 0.0, "weekly_ic_t": 0.0, "kind": "BACKFILL"},
        "cost_grid": [],
        "turnover_caps": [],
        "holdings_latest": [],
        "honest_limitations": [reason],
    }


def _data_freshness(klines: Optional[dict], alpha: dict) -> Dict:
    klines = klines or {}
    per_stock = klines.get("per_stock") or {}
    last_date = 0
    for row in per_stock.values():
        if isinstance(row, dict):
            try:
                last_date = max(last_date, int(row.get("date_max") or 0))
            except (TypeError, ValueError):
                continue
    if not last_date:
        rng = (alpha.get("panel") or {}).get("range") or [0, 0]
        last_date = int(rng[-1]) if rng else 0
    return {
        "stocks": int(klines.get("stocks_with_data") or (alpha.get("universe") or {}).get("used") or 0),
        "bars": int(klines.get("total_rows") or 0),
        "last_date": last_date,
        "sources": {"wind": int(klines.get("wind_stocks") or 0),
                    "ifind": int(klines.get("ifind_stocks") or 0)},
    }


def _factors_block(alpha: dict) -> List[Dict]:
    kept = set((alpha.get("hygiene") or {}).get("kept") or [])
    rows = []
    for name, block in (alpha.get("factor_ic") or {}).items():
        h1 = (block or {}).get("h1") or {}
        train, test = h1.get("train") or {}, h1.get("test") or {}
        rows.append({
            "name": str(name),
            "train_ic": float(train.get("mean_ic") or 0.0),
            "train_t": float(train.get("t_stat") or 0.0),
            "test_ic": float(test.get("mean_ic") or 0.0),
            "test_t": float(test.get("t_stat") or 0.0),
            "kept": name in kept,
        })
    return rows


def _combo_block(alpha: dict) -> Dict:
    combo = alpha.get("combo") or {}
    ic = combo.get("ic") or {}
    h1t, h5t = (ic.get("h1") or {}).get("test") or {}, (ic.get("h5") or {}).get("test") or {}
    return {
        "champion": str(combo.get("champion") or ""),
        "weights": {str(k): float(v) for k, v in (combo.get("weights") or {}).items()},
        "h1_test_ic": float(h1t.get("mean_ic") or 0.0),
        "h1_test_t": float(h1t.get("t_stat") or 0.0),
        "h5_test_ic": float(h5t.get("mean_ic") or 0.0),
        "h5_test_t": float(h5t.get("t_stat") or 0.0),
        "hit_rate": float(h1t.get("hit_rate") or 0.0),
    }


def _variants_block(alpha: dict) -> List[Dict]:
    tv = alpha.get("turnover_variants") or {}
    champion = tv.get("champion")
    rows = []
    for name, cand in (tv.get("candidates") or {}).items():
        train = (cand or {}).get("train_15bps") or {}
        test15 = ((cand or {}).get("test_cost_grid") or {}).get("15") or {}
        rows.append({
            "name": str(name),
            "train_sharpe": float(train.get("ann_sharpe") or 0.0),
            "test_return": float(test15.get("total_return") or 0.0),
            "test_sharpe": float(test15.get("ann_sharpe") or 0.0),
            "test_mdd": float(test15.get("max_drawdown") or 0.0),
            "turnover": float((cand or {}).get("test_avg_turnover") or 0.0),
            "is_champion": name == champion,
        })
    return rows


def _basket_block(alpha: dict, paper: Optional[dict]) -> Dict:
    basket = alpha.get("basket") or {}
    bench = ((basket.get("test") or {}).get("benchmark")) or {}
    out = {
        "n": int(basket.get("n") or 0),
        "aum_cny": float(basket.get("aum_cny") or 0.0),
        "cost_bps": float(basket.get("cost_bps") or 0.0),
        "bench_test": {
            "total_return": float(bench.get("total_return") or 0.0),
            "sharpe": float(bench.get("ann_sharpe") or 0.0),
            "max_dd": float(bench.get("max_drawdown") or 0.0),
        },
    }
    # Champion-consistent stats: the train-selected turnover cap's test row.
    tcs = alpha.get("turnover_cap_sensitivity") or {}
    cap = tcs.get("train_champion_cap")
    cap_row = (tcs.get("grid") or {}).get(str(cap)) or {}
    test15 = cap_row.get("test_15bps") or {}
    weekly = (paper or {}).get("weekly") or []
    equity = _compound(weekly, "paper_ret") if weekly else []
    bench_equity = _compound(weekly, "bench_ret") if weekly else []
    if test15 and equity:
        out["test"] = {
            "total_return": float(test15.get("total_return") or 0.0),
            "sharpe": float(test15.get("ann_sharpe") or 0.0),
            "max_dd": float(test15.get("max_drawdown") or 0.0),
            "avg_turnover": float(cap_row.get("test_avg_turnover") or 0.0),
        }
        out["equity_test"] = equity
        out["bench_equity_test"] = bench_equity
    else:
        # fallback: the uncapped baseline basket (equity arrays are only persisted for it)
        strat = ((basket.get("test") or {}).get("strategy")) or {}
        out["test"] = {
            "total_return": float(strat.get("total_return") or 0.0),
            "sharpe": float(strat.get("ann_sharpe") or 0.0),
            "max_dd": float(strat.get("max_drawdown") or 0.0),
            "avg_turnover": float((basket.get("test") or {}).get("avg_turnover") or 0.0),
        }
        out["equity_test"] = list((basket.get("test") or {}).get("equity") or [])
        out["bench_equity_test"] = list((basket.get("test") or {}).get("bench_equity") or [])
    return out


def _ashare_alpha_payload() -> Dict:
    bundle = _ashare_snapshot_bundle()
    if bundle is None:
        return _synthetic_payload(
            "未找到完整的 data/stocks/snapshots 三件套 —— 先运行 "
            "tools/ashare_alpha_research.py(+ tools/ashare_paper_track.py)生成研究快照"
        )
    alpha, paper, klines = bundle

    dry = alpha.get("dry_run_champion_variant") or alpha.get("dry_run") or {}
    paper_summary = (paper or {}).get("summary") or {}
    paper_prov = (paper or {}).get("provenance") or {}
    weekly = (paper or {}).get("weekly") or []

    # cost grid: champion variant's own cost sweep (fall back to the baseline grid)
    tv = alpha.get("turnover_variants") or {}
    champion = tv.get("champion")
    champ_grid = (((tv.get("candidates") or {}).get(champion) or {}).get("test_cost_grid")) or {}
    grid_src = champ_grid or (alpha.get("basket") or {}).get("cost_grid_test") or {}
    cost_grid = [
        {"cost_bps": float(k), "total_return": float(v.get("total_return") or 0.0),
         "sharpe": float(v.get("ann_sharpe") or 0.0)}
        for k, v in sorted(grid_src.items(), key=lambda kv: float(kv[0]))
        if isinstance(v, dict)
    ]

    caps = []
    for k, v in ((alpha.get("turnover_cap_sensitivity") or {}).get("grid") or {}).items():
        try:
            cap_value = float(k)
        except (TypeError, ValueError):
            continue   # baseline_inf = uncapped; it appears in `variants` as "baseline"
        test15 = (v or {}).get("test_15bps") or {}
        caps.append({
            "cap": cap_value,
            "train_sharpe": float((v or {}).get("train_sharpe_15bps") or 0.0),
            "test_return": float(test15.get("total_return") or 0.0),
            "test_sharpe": float(test15.get("ann_sharpe") or 0.0),
        })
    caps.sort(key=lambda r: r["cap"])

    weights_last = ((alpha.get("basket") or {}).get("test") or {}).get("weights_last") or {}
    holdings = [
        {"code": str(code), "weight": float(w)}
        for code, w in sorted(weights_last.items(), key=lambda kv: -float(kv[1]))
    ]

    limitations = [str(x) for x in (alpha.get("honest_limitations") or [])]
    limitations.append(
        "basket 块为冠军一致口径:test 统计取 train 选出的 cap30 换手上限行,"
        "equity_test 由 paper-track 周度净收益复利而成(与回测引擎不分叉);"
        "holdings_latest 是 uncapped baseline 篮子的最后一周权重(cap30 冠军末周持仓未单独落快照)")
    limitations.append(
        "已注册进 experiment_registry(DRAFT,verdict=REQUIRES_HUMAN_REVIEW):"
        "证据未过 evaluator 门禁,非人 actor 永不晋级;初筛诊断,不驱动任何自动晋级/下单")
    if paper is None:
        limitations.append("paper_track_snapshot.json 缺失:paper_track 块为零值占位")

    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "provenance": REAL_PROVENANCE,
        "data_provenance": REAL_PROVENANCE,
        "snapshot_present": True,
        "generated_at": str((klines or {}).get("fetched_at") or "UNKNOWN"),
        "data_freshness": _data_freshness(klines, alpha),
        "universe": {
            "file_count": int((alpha.get("universe") or {}).get("file_count") or 0),
            "used": int((alpha.get("universe") or {}).get("used") or 0),
        },
        "factors": _factors_block(alpha),
        "combo": _combo_block(alpha),
        "basket": _basket_block(alpha, paper),
        "variants": _variants_block(alpha),
        "dry_run": {
            "run_id": str(dry.get("run_id") or ""),
            "total_return": float(dry.get("total_return") or 0.0),
            "sharpe": float(dry.get("sharpe") or 0.0),
            "max_drawdown": float(dry.get("max_drawdown") or 0.0),
            "n_approved": int(dry.get("n_approved") or 0),
            "n_rejected": int(dry.get("n_rejected") or 0),
            "residual": float(dry.get("residual") or 0.0),
        },
        "paper_track": {
            "alpha_id": str((paper or {}).get("alpha_id") or ""),
            "n_weeks": int(paper_summary.get("n_weeks") or 0),
            "cum_return": float(paper_summary.get("paper_cum_return") or 0.0),
            "sharpe": float(paper_summary.get("paper_sharpe_weekly_annualized") or 0.0),
            "weekly_ic_mean": float(paper_summary.get("paper_ic_mean") or 0.0),
            "weekly_ic_t": float(paper_summary.get("paper_ic_t_stat") or 0.0),
            "kind": str(paper_prov.get("source_field") or "BACKFILL"),
        },
        "cost_grid": cost_grid,
        "turnover_caps": caps,
        "holdings_latest": holdings,
        "honest_limitations": limitations,
    }


@router.get("/api/v1/console/ashare-alpha")
async def ashare_alpha() -> Dict:
    return _ashare_alpha_payload()


def _surface_summary(name: str, value: dict) -> dict:
    by_cost = value.get("confirmation_cost_sensitivity") or {}
    primary = ((by_cost.get("30") or {}).get("primary_phase") or {}).get("excess") or {}
    robustness = (by_cost.get("30") or {}).get("phase_robustness") or {}
    return {
        "surface": name,
        "candidate": value.get("candidate_selected_without_confirmation"),
        "stable_positive_before_confirmation": bool(
            value.get("stable_positive_before_confirmation")
        ),
        "confirmation_excess_sharpe": primary.get("annualized_sharpe"),
        "confirmation_excess_return": primary.get("annualized_return"),
        "confirmation_median_excess_sharpe": robustness.get("median_excess_sharpe"),
        "bonferroni_adjusted_p": value.get("bonferroni_adjusted_p"),
        "candidate_usefulness": value.get("candidate_usefulness", "NOT_VALIDATED"),
    }


def _ashare_institutional_search_payload() -> Dict:
    document = _load(str(_INSTITUTIONAL_SEARCH_PATH))
    if document is None:
        return {
            "source": "BACKEND_COMPUTED_SNAPSHOT",
            "read_only": True,
            "api_version": "0.1.0",
            "data_provenance": "REPOSITORY_EVIDENCE",
            "snapshot_present": False,
            "authoritative": False,
            "diagnostic_only": True,
            "evidence_status": "UNAVAILABLE",
            "availability_mode": "SEARCH_EVIDENCE_NOT_PUBLISHED",
            "candidate_usefulness": "UNAVAILABLE",
            "source_summary": {},
            "surface_coverage": {},
            "surface_results": [],
            "untouched_forward_observation": {},
            "limitations": ["Run the admitted read-only A-share institutional surface search."],
        }
    source = document.get("source") or {}
    surface_results = [
        _surface_summary(str(name), value)
        for name, value in sorted((document.get("surface_confirmations") or {}).items())
        if isinstance(value, dict)
    ]
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REPOSITORY_EVIDENCE",
        "snapshot_present": True,
        "authoritative": False,
        "diagnostic_only": True,
        "evidence_status": document.get("evidence_status", "TECHNICAL_VALIDATION_ONLY"),
        "availability_mode": "READ_ONLY_TECHNICAL_RESEARCH",
        "generated_at": document.get("generated_at"),
        "candidate_usefulness": document.get("candidate_usefulness", "NOT_VALIDATED"),
        "source_summary": {
            "rows": source.get("rows"),
            "instruments": source.get("instruments"),
            "first_date": source.get("first_date"),
            "last_date": source.get("last_date"),
            "sqlite_quick_check": source.get("sqlite_quick_check"),
            "future_known_rows": source.get("future_known_rows"),
            "bad_bar_count": source.get("bad_bar_count"),
            "historical_universe_semantics": (
                (source.get("intake_contract") or {}).get("historical_universe_semantics")
            ),
        },
        "trial_accounting": document.get("trial_accounting") or {},
        "surface_coverage": document.get("requested_surface_coverage") or {},
        "surface_results": surface_results,
        "untouched_forward_observation": document.get("untouched_forward_observation") or {},
        "limitations": document.get("limitations") or [],
    }


@router.get("/api/v1/console/ashare-alpha-search")
async def ashare_alpha_search() -> Dict:
    return _ashare_institutional_search_payload()
