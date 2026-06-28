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

    def resolve(self) -> Callable:
        if self.runner is not None:
            return self.runner
        mod, fn = self.entrypoint.split(":")
        return getattr(importlib.import_module(mod), fn)


def _tool_then_json(module: str, out_json: str) -> Callable:
    """Run a tool's main() (it writes its own cache JSON), then load + return that JSON for the adapter."""
    def run(_params):
        importlib.import_module(module).main()
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


RUN_KINDS: dict = {
    "carry_strategy": RunSpec("tools.crypto_carry_strategy:main", {}, adapt_carry, "资金费 Carry 策略",
                              runner=_tool_then_json("tools.crypto_carry_strategy", "crypto_carry_strategy.json")),
    "oi_research": RunSpec("tools.crypto_oi_research:main", {}, adapt_oi_research, "持仓因子研究",
                           runner=_tool_then_json("tools.crypto_oi_research", "crypto_oi_research.json")),
    "combo_book": RunSpec("tools.crypto_combo_book:main", {}, adapt_combo, "双因子组合",
                          runner=_tool_then_json("tools.crypto_combo_book", "crypto_combo_book.json")),
    "survivorship": RunSpec("tools.crypto_oi_survivorship:main", {}, adapt_survivorship, "幸存者重测",
                            runner=_tool_then_json("tools.crypto_oi_survivorship", "crypto_oi_survivorship.json")),
    "factory_orthogonal": RunSpec("tools.crypto_factory_orthogonal:main", {},
                                  lambda j: adapt_factory("factory_orthogonal", j), "自主正交发现(放大)",
                                  runner=_tool_then_json("tools.crypto_factory_orthogonal", "crypto_factory_orthogonal.json")),
    "factory_loop": RunSpec("tools.crypto_factory_loop:main", {},
                            lambda j: adapt_factory("factory_loop", j), "自主正交发现(loop-until-dry)",
                            runner=_tool_then_json("tools.crypto_factory_loop", "crypto_factory_loop.json")),
    "data_refresh": RunSpec("tools.binance_oi_metrics_fetch:main", {"intraday": bool}, _adapt_refresh,
                            "刷新持仓数据", runner=_refresh_runner),
}


def validate_params(kind: str, params: dict) -> dict:
    """Reject an unknown kind (KeyError) or any param outside the kind's typed schema (ValueError)."""
    spec = RUN_KINDS[kind]
    out = {}
    for k, v in params.items():
        if k not in spec.param_schema:
            raise ValueError(f"unknown param {k!r} for kind {kind!r}")
        if not isinstance(v, spec.param_schema[k]):
            raise ValueError(f"param {k!r} must be {spec.param_schema[k].__name__}")
        out[k] = v
    return out
