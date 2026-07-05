"""RUN_KINDS — the research-ONLY whitelist the runner may execute.

Each kind maps to a RESEARCH entrypoint (a tool/research main), a small TYPED param schema (no free-form
strings that reach a path/shell), an adapter (tool-output JSON -> ResearchRun fields, reusing the indexer
adapters), and a title. There is NO order/broker/credential entry here — that is machine-enforced by
tests/governance/test_research_runner_boundary.py. Tool imports are deferred into the runner callables so
this module stays import-light (and services/ network-import-clean)."""
from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from typing import Callable, Optional

from services.research.run_registry.indexer import (adapt_carry, adapt_combo, adapt_factory,
                                                    adapt_oi_research, adapt_survivorship)

_CACHE = os.path.join("data", "cache")


@dataclass
class RunSpec:
    entrypoint: str                       # "module:func" — research-only; scanned by the governance test
    param_schema: dict                    # allowed param keys -> type (small + typed; never free-form)
    adapter: Callable                     # tool-output dict -> ResearchRun field dict
    title: str
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
    "carry_strategy": RunSpec("tools.crypto_carry_strategy:main", {}, adapt_carry, "资金费 Carry 策略",
                              runner=_tool_then_json("tools.crypto_carry_strategy", "crypto_carry_strategy.json")),
    "oi_research": RunSpec("tools.crypto_oi_research:main", {}, adapt_oi_research, "持仓因子研究",
                           runner=_tool_then_json("tools.crypto_oi_research", "crypto_oi_research.json")),
    "combo_book": RunSpec("tools.crypto_combo_book:main", {}, adapt_combo, "双因子组合",
                          runner=_tool_then_json("tools.crypto_combo_book", "crypto_combo_book.json")),
    "survivorship": RunSpec("tools.crypto_oi_survivorship:main", {}, adapt_survivorship, "幸存者重测",
                            runner=_tool_then_json("tools.crypto_oi_survivorship", "crypto_oi_survivorship.json")),
    "factory_orthogonal": RunSpec("tools.crypto_factory_orthogonal:main",
                                  {"generations": int, "population": int},
                                  lambda j: adapt_factory("factory_orthogonal", j), "自主正交发现(放大)",
                                  runner=_tool_then_json("tools.crypto_factory_orthogonal", "crypto_factory_orthogonal.json"),
                                  # 2026-07-06 安全审查 punch-list:此前无上限,可提交一个跑到天荒地老的任务
                                  # (默认 generations=8/population=64,给"放大"留够头寸但不放行天文数字)
                                  param_bounds={"generations": (1, 500), "population": (1, 5000)}),
    "factory_loop": RunSpec("tools.crypto_factory_loop:main", {},
                            lambda j: adapt_factory("factory_loop", j), "自主正交发现(loop-until-dry)",
                            runner=_tool_then_json("tools.crypto_factory_loop", "crypto_factory_loop.json")),
    "data_refresh": RunSpec("tools.binance_oi_metrics_fetch:main", {"intraday": bool}, _adapt_refresh,
                            "刷新持仓数据", runner=_refresh_runner),
    "ic_panel": RunSpec("tools.ic_panel_research:main", {}, _adapt_ic_panel, "IC 面板",
                        runner=_tool_then_json("tools.ic_panel_research", "ic_panel_snapshot.json")),
    "wide_gauntlet": RunSpec("tools.wide_factor_gauntlet:main", {}, _adapt_wide_gauntlet,
                             "宽宇宙候选证伪",
                             runner=_tool_then_json("tools.wide_factor_gauntlet", "wide_factor_gauntlet.json")),
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
