"""RUN_KINDS — the research-only tool-adapter whitelist the runner may execute.

Each kind maps to a RESEARCH entrypoint (a tool/research main), a small TYPED param schema (no free-form
strings that reach a path/shell), an adapter (tool-output JSON -> ResearchRun fields, reusing the indexer
adapters), and a title. There is NO order/broker/credential entry here — that is machine-enforced by
tests/governance/test_research_runner_boundary.py. Tool imports are deferred into the runner callables so
this module stays import-light (and services/ network-import-clean)."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research.run_registry.indexer import (adapt_carry, adapt_combo, adapt_factory,
                                                    adapt_oi_research, adapt_survivorship)

_CACHE = os.path.join("data", "cache")
GLOBAL_TRIAL_LEDGER = "EXPERIMENT_REGISTRY_GLOBAL_TRIAL_LEDGER"


class RunPurpose(str, Enum):
    """How a runner result may be used by downstream research governance."""

    SELECTION_BEARING = "SELECTION_BEARING"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"


@dataclass(frozen=True)
class ProvenancePolicy:
    purpose: RunPurpose
    evidence_authority: Optional[str] = None

    def __post_init__(self) -> None:
        if self.purpose is RunPurpose.SELECTION_BEARING:
            if self.evidence_authority != GLOBAL_TRIAL_LEDGER:
                raise ValueError("selection-bearing runs require the global trial ledger")
        elif self.evidence_authority is not None:
            raise ValueError("diagnostic-only runs cannot claim a selection evidence authority")


SELECTION_BEARING = ProvenancePolicy(
    RunPurpose.SELECTION_BEARING,
    GLOBAL_TRIAL_LEDGER,
)
DIAGNOSTIC_ONLY = ProvenancePolicy(RunPurpose.DIAGNOSTIC_ONLY)

# These historical factory drivers still depend on the invalidated perpetual
# ledger/timing path.  Keep the classification exact: selection-bearing is a
# provenance policy, not by itself a reason to quarantine a future independent
# implementation or a test-injected runner.
LEGACY_QUARANTINED_RUN_KINDS = frozenset({
    "factory_orthogonal",
    "factory_loop",
})


def enforce_run_kind_authority(kind: str) -> None:
    """Fail before a quarantined runner can import tools or touch data/output."""

    if kind in LEGACY_QUARANTINED_RUN_KINDS:
        quarantine_legacy_backtest(f"services.research_runner.{kind}")


@dataclass(frozen=True)
class RunSpec:
    entrypoint: str                       # "module:func" — research-only; scanned by the governance test
    param_schema: dict                    # allowed param keys -> type (small + typed; never free-form)
    adapter: Callable                     # tool-output dict -> ResearchRun field dict
    title: str
    provenance_policy: ProvenancePolicy
    runner: Optional[Callable] = None     # injected (tests) or resolved from entrypoint at run time
    param_bounds: Optional[dict] = None   # optional key -> (min, max) inclusive; unbounded if key absent

    def resolve(self) -> Callable:
        if self.runner is not None:
            return self.runner
        mod, fn = self.entrypoint.split(":")
        return getattr(importlib.import_module(mod), fn)


def _tool_then_json(module: str, out_json: str) -> Callable:
    """Run a tool's main(**params) (it writes its own cache JSON), then load + return that JSON. Params are
    the kind's typed, validated keys (empty for most kinds) — passed as kwargs to the tool's main."""
    def run(params):
        importlib.import_module(module).main(**params)
        with open(os.path.join(_CACHE, out_json), encoding="utf-8") as f:
            return json.load(f)
    return run


def _quarantined_run_kind(kind: str) -> Callable:
    """Return a no-import runner for an explicitly invalidated public kind."""

    def run(_params):
        enforce_run_kind_authority(kind)

    return run


_ASHARE_TOOL_TIMEOUT_S = 7200   # A 股研究驱动器(面板+因子+干跑)可能跑数十分钟


def _module_subprocess_then_json(module: str, snapshot_relpath: str) -> Callable:
    """Run a tools.* research module as ``python -m <module>`` then load the snapshot JSON it wrote.

    The child process is launched with an explicit argv list and shell=False (never a shell
    string), capturing stdout so it streams into the run's live log via the caller's
    redirect_stdout. Paths stay cwd-relative like every other kind (the runner service is
    started from the repo root). Fail-closed: a non-zero exit raises with the stderr tail;
    a missing/invalid snapshot raises instead of serving stale data."""
    def run(params):
        argv = [sys.executable, "-m", module]
        completed = subprocess.run(
            argv, shell=False, capture_output=True, text=True,
            timeout=_ASHARE_TOOL_TIMEOUT_S, check=False,
        )
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "")[-800:]
            raise RuntimeError(f"{module} exited {completed.returncode}: {tail}")
        with open(snapshot_relpath, encoding="utf-8") as fh:
            return json.load(fh)
    return run


def _adapt_ashare_alpha(j: dict) -> dict:
    combo = j.get("combo") or {}
    h1 = ((combo.get("ic") or {}).get("h1") or {}).get("test") or {}
    basket = j.get("basket") or {}
    strat = (basket.get("test") or {}).get("strategy") or {}
    dry = j.get("dry_run_champion_variant") or j.get("dry_run") or {}
    equity = ((j.get("_equity_test")) or (basket.get("test") or {}).get("equity")) or []
    return {"title": "A股微盘复合 alpha 研究", "params": {},
            "metrics": {"universe_used": (j.get("universe") or {}).get("used"),
                        "n_factors_kept": len((j.get("hygiene") or {}).get("kept") or []),
                        "h1_test_ic": h1.get("mean_ic"),
                        "h1_test_t": h1.get("t_stat"),
                        "basket_test_return": strat.get("total_return"),
                        "basket_test_sharpe": strat.get("ann_sharpe"),
                        "basket_test_max_dd": strat.get("max_drawdown"),
                        "dry_run_sharpe": dry.get("sharpe"),
                        "dry_run_return": dry.get("total_return")},
            "series": {"basket_test_equity": [
                {"label": str(i), "value": v} for i, v in enumerate(equity)]},
            "verdict": "DIAGNOSTIC",
            "caveats": ["初筛诊断,非晋级门禁——未过 evaluator 门禁,不驱动任何自动晋级/下单",
                        "宇宙为幸存者截面;价格为前复权;成交额为代理",
                        "L2_DRY_RUN_ONLY;无券商/凭证/下单路径"],
            "provenance": "tools/ashare_alpha_research.py"}


def _adapt_ashare_paper_track(j: dict) -> dict:
    summary = j.get("summary") or {}
    weekly = j.get("weekly") or []
    return {"title": "A股微盘 alpha paper-track 追踪", "params": {"variant": j.get("variant")},
            "metrics": {"n_weeks": summary.get("n_weeks"),
                        "paper_cum_return": summary.get("paper_cum_return"),
                        "paper_sharpe_weekly_annualized": summary.get("paper_sharpe_weekly_annualized"),
                        "paper_ic_mean": summary.get("paper_ic_mean"),
                        "paper_ic_t_stat": summary.get("paper_ic_t_stat"),
                        "bench_total_return": summary.get("bench_total_return"),
                        "perf_gap_vs_train": summary.get("perf_gap_vs_train")},
            "series": {"weekly_ic": [
                {"label": str(w.get("date")), "value": w.get("weekly_ic")}
                for w in weekly if isinstance(w, dict)]},
            "verdict": "DIAGNOSTIC",
            "caveats": ["全部行为 BACKFILL 历史回填,非真 FORWARD 在线 advance",
                        "初筛诊断,非晋级门禁——不驱动任何自动晋级/下单"],
            "provenance": "tools/ashare_paper_track.py"}


def _load_cache_object(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _paper_cycle_runner(params: dict) -> dict:
    """Run one fixed, public-data paper cycle without accepting paths or shell input.

    The UI may choose only the isolated profile bit. All stores and output
    locations stay pinned to the two audited L2 profiles, and the driver is
    always invoked with one cycle, a paper refresh, and the fail-closed health
    check. The tool returns an exit code rather than raising for a health
    failure; the adapter keeps that failure visible in the registry result.
    """
    small_10k = bool(params.get("small_10k", False))
    if small_10k:
        profile = "small_10k"
        aum = "10000"
        paper_store = os.path.join(_CACHE, "paper_order_ledger_10k.db")
        realtime_store = os.path.join(_CACHE, "paper_realtime_10k.db")
        output = os.path.join(_CACHE, "paper_realtime_10k_status.json")
        operator_receipt = os.path.join(_CACHE, "l2_paper_operator_10k.json")
        health_output = os.path.join(_CACHE, "paper_health_10k.json")
    else:
        profile = "default"
        aum = "1000000"
        paper_store = os.path.join(_CACHE, "paper_order_ledger.db")
        realtime_store = os.path.join(_CACHE, "paper_realtime.db")
        output = os.path.join(_CACHE, "paper_realtime_status.json")
        operator_receipt = os.path.join(_CACHE, "l2_paper_operator.json")
        health_output = os.path.join(_CACHE, "paper_health.json")

    tool = importlib.import_module("tools.paper_realtime")
    exit_code = tool.main([
        "--cycles", "1",
        "--refresh-paper",
        "--health-check",
        "--aum", aum,
        "--paper-store", paper_store,
        "--store", realtime_store,
        "--output", output,
        "--operator-receipt", operator_receipt,
        "--health-output", health_output,
    ])
    return {
        "profile": profile,
        "exit_code": int(exit_code),
        "report": _load_cache_object(output),
        "health": _load_cache_object(health_output),
    }


def _adapt_paper_cycle(payload: dict) -> dict:
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    report_verdict = report.get("verdict") if isinstance(report.get("verdict"), dict) else {}
    health_verdict = health.get("verdict") if isinstance(health.get("verdict"), dict) else {}
    persistence = report.get("persistence") if isinstance(report.get("persistence"), dict) else {}
    blockers = health.get("blockers") if isinstance(health.get("blockers"), list) else []
    alpha_rows = report.get("alphas") if isinstance(report.get("alphas"), dict) else {}
    profile = str(payload.get("profile") or "default")
    try:
        exit_code = int(payload.get("exit_code", 1))
    except (TypeError, ValueError):
        exit_code = 1
    cycle_ok = bool(
        exit_code == 0
        and report_verdict.get("cycle_completed") is True
        and health_verdict.get("paper_ready") is True
    )
    metrics = {
        "cycle_completed": 1.0 if report_verdict.get("cycle_completed") is True else 0.0,
        "paper_ready": 1.0 if health_verdict.get("paper_ready") is True else 0.0,
        "new_snapshots": float(persistence.get("new_snapshots", 0) or 0),
        "n_alphas": float(len(alpha_rows)),
        "health_blockers": float(len(blockers)),
        "exit_code": float(exit_code),
    }
    series = {"net_pnl": []}
    for alpha_id, row in sorted(alpha_rows.items()):
        if not isinstance(row, dict):
            continue
        value = row.get("net_pnl")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            series["net_pnl"].append({"label": str(alpha_id), "value": float(value)})
    caveats = [
        f"profile={profile}",
        "L2_DRY_RUN_ONLY; public market data; no broker or credential path",
    ]
    caveats.extend(str(item) for item in blockers if str(item))
    return {
        "title": f"Paper cycle ({profile})",
        "params": {"profile": profile},
        "metrics": metrics,
        "series": series,
        "verdict": "PAPER_CYCLE_OK" if cycle_ok else "PAPER_CYCLE_BLOCKED",
        "caveats": caveats,
        "provenance": "tools/paper_realtime.py",
    }


def _refresh_runner(params):
    """data_refresh: pull the positioning metrics (eod, or intraday-derived) via the public fetcher."""
    f = importlib.import_module("tools.binance_oi_metrics_fetch")
    if params.get("intraday"):
        f.main(parse_fn=f.parse_metrics_zip_intraday, cache_path=f._CACHE_INTRADAY, snap_path=f._SNAP_INTRADAY)
        snap = f._SNAP_INTRADAY
    else:
        f.main()
        snap = f._SNAP
    with open(snap, encoding="utf-8") as fh:
        return json.load(fh)


def _adapt_refresh(j: dict) -> dict:
    return {"title": "刷新持仓数据", "params": {}, "metrics": {"n_tickers": j.get("n_tickers")},
            "series": {}, "verdict": "REFRESHED", "caveats": ["仅研究取数,不下单/不碰实盘"],
            "provenance": "tools/binance_oi_metrics_fetch.py"}


def _adapt_ic_panel(j: dict) -> dict:
    rows = j.get("rows", []) or []
    scored = [r for r in rows if r.get("t_stat") is not None]
    best = max(scored, key=lambda r: abs(r["t_stat"])) if scored else None
    return {"title": "IC 面板(全因子横向比较)", "params": {},
            "metrics": {"n_factors": len(rows),
                        "best_factor": best["name"] if best else None,
                        "best_t_stat": best["t_stat"] if best else None},
            "series": {}, "verdict": "DIAGNOSTIC",
            "caveats": ["初筛诊断,非晋级门禁——不驱动任何自动晋级"],
            "provenance": "tools/ic_panel_research.py"}


def _adapt_wide_gauntlet(j: dict) -> dict:
    results = j.get("results", []) or []
    metrics = {}
    for r in results:
        net = ((r.get("by_cost") or {}).get("25bps") or {}).get("ann_sharpe")
        metrics[f"{r['name']}_net25bps_sharpe"] = net
        metrics[f"{r['name']}_verdict"] = r.get("verdict")
    return {"title": "宽宇宙候选证伪(成本扫描)", "params": {}, "metrics": metrics,
            "series": {}, "verdict": "DIAGNOSTIC",
            "caveats": ["周度 L/S 分位 + 换手计费;无冲击模型、未评估空腿可行性——初筛证伪,非晋级门禁"],
            "provenance": "tools/wide_factor_gauntlet.py"}


RUN_KINDS: dict = {
    "carry_strategy": RunSpec("tools.crypto_carry_strategy:main", {}, adapt_carry, "资金费 Carry 策略", DIAGNOSTIC_ONLY,
                              runner=_tool_then_json("tools.crypto_carry_strategy", "crypto_carry_strategy.json")),
    "oi_research": RunSpec("tools.crypto_oi_research:main", {}, adapt_oi_research, "持仓因子研究", DIAGNOSTIC_ONLY,
                           runner=_tool_then_json("tools.crypto_oi_research", "crypto_oi_research.json")),
    "combo_book": RunSpec("tools.crypto_combo_book:main", {}, adapt_combo, "双因子组合", DIAGNOSTIC_ONLY,
                          runner=_tool_then_json("tools.crypto_combo_book", "crypto_combo_book.json")),
    "survivorship": RunSpec("tools.crypto_oi_survivorship:main", {}, adapt_survivorship, "幸存者重测", DIAGNOSTIC_ONLY,
                            runner=_tool_then_json("tools.crypto_oi_survivorship", "crypto_oi_survivorship.json")),
    "factory_orthogonal": RunSpec("tools.crypto_factory_orthogonal:main",
                                  {"generations": int, "population": int},
                                  lambda j: adapt_factory("factory_orthogonal", j), "自主正交发现(放大)",
                                  SELECTION_BEARING,
                                  runner=_quarantined_run_kind("factory_orthogonal"),
                                  # 2026-07-06 安全审查 punch-list:此前无上限,可提交一个跑到天荒地老的任务
                                  # (默认 generations=8/population=64,给"放大"留够头寸但不放行天文数字)
                                  param_bounds={"generations": (1, 500), "population": (1, 5000)}),
    "factory_loop": RunSpec("tools.crypto_factory_loop:main", {},
                            lambda j: adapt_factory("factory_loop", j), "自主正交发现(loop-until-dry)",
                            SELECTION_BEARING,
                            runner=_quarantined_run_kind("factory_loop")),
    "data_refresh": RunSpec("tools.binance_oi_metrics_fetch:main", {"intraday": bool}, _adapt_refresh,
                            "刷新持仓数据", DIAGNOSTIC_ONLY, runner=_refresh_runner),
    "ic_panel": RunSpec("tools.ic_panel_research:main", {}, _adapt_ic_panel, "IC 面板", DIAGNOSTIC_ONLY,
                        runner=_tool_then_json("tools.ic_panel_research", "ic_panel_snapshot.json")),
    "wide_gauntlet": RunSpec("tools.wide_factor_gauntlet:main", {}, _adapt_wide_gauntlet,
                             "宽宇宙候选证伪", DIAGNOSTIC_ONLY,
                             runner=_tool_then_json("tools.wide_factor_gauntlet", "wide_factor_gauntlet.json")),
    "paper_cycle": RunSpec("tools.paper_realtime:main", {"small_10k": bool}, _adapt_paper_cycle,
                           "Paper real-time cycle", DIAGNOSTIC_ONLY, runner=_paper_cycle_runner),
    "ashare_alpha": RunSpec("tools.ashare_alpha_research:main", {}, _adapt_ashare_alpha,
                            "A股微盘复合 alpha 研究", DIAGNOSTIC_ONLY,
                            runner=_module_subprocess_then_json(
                                "tools.ashare_alpha_research",
                                os.path.join("data", "stocks", "snapshots", "alpha_snapshot.json"))),
    "ashare_paper_track": RunSpec("tools.ashare_paper_track:main", {}, _adapt_ashare_paper_track,
                                  "A股微盘 alpha paper-track 追踪", DIAGNOSTIC_ONLY,
                                  runner=_module_subprocess_then_json(
                                      "tools.ashare_paper_track",
                                      os.path.join("data", "stocks", "snapshots",
                                                   "paper_track_snapshot.json"))),
}


def validate_params(kind: str, params: dict) -> dict:
    """Reject an unknown kind (KeyError), any param outside the kind's typed schema, or a value outside
    the kind's declared bounds (ValueError) — e.g. `factory_orthogonal`'s generations/population, which
    without a bound could be handed an unbounded value and run essentially forever."""
    spec = RUN_KINDS[kind]
    bounds = spec.param_bounds or {}
    out = {}
    for k, v in params.items():
        if k not in spec.param_schema:
            raise ValueError(f"unknown param {k!r} for kind {kind!r}")
        if not isinstance(v, spec.param_schema[k]):
            raise ValueError(f"param {k!r} must be {spec.param_schema[k].__name__}")
        if k in bounds:
            lo, hi = bounds[k]
            if not (lo <= v <= hi):
                raise ValueError(f"param {k!r} must be between {lo} and {hi}, got {v!r}")
        out[k] = v
    return out
