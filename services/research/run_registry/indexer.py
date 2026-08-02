"""indexer — adapt the existing cached research-result JSONs into ResearchRun records.

Each backtest/search tool already writes a structured JSON to data/cache/. These adapters pull the
headline numbers + chartable series + verdict + caveats out of each, so every run we have ALREADY done
appears in the research-ops console immediately. Pure stdlib; deterministic (timestamp injected by caller).

Series shape: {name: [{"label": str, "value": number}, ...]} — self-describing for the generic viewer.
The FIRST entry of `metrics` is treated as the headline by the console API.
"""
from __future__ import annotations

import json
import os

from .contract import ResearchRun, make_run_id
from .registry import RunRegistry

_CARRY_CAVEATS = ["历史回测≠未来", "幸存者偏差", "滑点为建模值", "拥挤 yield 会衰减", "尾部/爆仓未建模", "容量有限"]


def _y(label_dict):
    """{'2022': 2.2, ...} -> [{'label':'22','value':2.2}, ...] sorted by year."""
    out = []
    for k in sorted(label_dict):
        out.append({"label": str(k)[-2:], "value": round(float(label_dict[k]), 3)})
    return out


def adapt_carry(j: dict) -> dict:
    return {
        "title": "资金费 Carry 策略",
        "params": {"n_instruments": j.get("n_instruments"), "n_days": j.get("n_days")},
        "metrics": {"net_sharpe_5bps": j.get("sharpe_net_5bps"),
                    "funding_bps_day": j.get("avg_funding_bps_day"),
                    "price_plus_funding": j.get("sharpe_price_plus_funding"),
                    "turnover": j.get("avg_turnover"),
                    # merged in from crypto_carry_voltarget.json by _merge_carry_voltarget (same
                    # strategy, sibling research artifact) — real max_drawdown/worst_day/raw sharpe
                    # instead of the console hardcoding its own transcribed copy of these numbers.
                    "max_drawdown": j.get("max_drawdown"),
                    "worst_day": j.get("worst_day"),
                    "sharpe_raw": j.get("sharpe_raw"),
                    "ann_return": j.get("ann_return")},
        "series": {"carry_buildup": [
            {"label": "price", "value": j.get("sharpe_price_only")},
            {"label": "+funding", "value": j.get("sharpe_price_plus_funding")},
            {"label": "@5bps", "value": j.get("sharpe_net_5bps")}]},
        "verdict": "DEPLOYABLE",
        "caveats": _CARRY_CAVEATS,
        "provenance": "tools/crypto_carry_strategy.py",
    }


_CARRY_VOLTARGET_FILE = "crypto_carry_voltarget.json"


def _merge_carry_voltarget(cache_dir: str, j: dict) -> dict:
    """Merge the real drawdown/worst-day/raw-sharpe figures from crypto_carry_voltarget.json (the
    SAME funding-carry strategy's vol-target sibling artifact) into the carry_strategy bundle, so
    the console can bind these instead of transcribing them as hardcoded literals. Fail-soft: a
    missing/malformed sibling file just means those three fields stay None (adapt_carry already
    handles None via the frontend's OBSERVED-constant fallback) — never fabricates a value."""
    vt_path = os.path.join(cache_dir, _CARRY_VOLTARGET_FILE)
    if not os.path.exists(vt_path):
        return j
    try:
        vt = json.load(open(vt_path, encoding="utf-8"))
    except Exception:
        return j
    raw = vt.get("raw") if isinstance(vt.get("raw"), dict) else {}
    merged = dict(j)
    merged["max_drawdown"] = raw.get("max_drawdown")
    merged["worst_day"] = raw.get("worst_day")
    merged["sharpe_raw"] = raw.get("sharpe")
    merged["ann_return"] = raw.get("ann_return")
    return merged


def adapt_oi_research(j: dict) -> dict:
    factors = j.get("factors", [])
    top = sorted(factors, key=lambda f: -(f.get("incremental_ir_vs_carry") or -9))[:8]
    crowd = next((f for f in factors if f.get("name") == "crowd_contra"), None)
    return {
        "title": "持仓因子 vs carry 正交研究",
        "params": {"universe": len(j.get("universe", [])), "n_days": j.get("n_days"), "quantile": j.get("quantile")},
        "metrics": {"carry_econ_sharpe": j.get("carry_econ_sharpe"),
                    "carry_price_sharpe": j.get("carry_price_sharpe"),
                    "n_factors": len(factors),
                    # real crowd_contra-vs-carry orthogonality (was a hardcoded "0.5%" literal on the console)
                    "crowd_contra_r2_vs_carry": None if crowd is None else crowd.get("explained_r2_by_carry"),
                    "crowd_contra_price_sharpe": None if crowd is None else crowd.get("price_sharpe")},
        "series": {"incremental_ir": [{"label": f.get("name", "?")[:10],
                                       "value": f.get("incremental_ir_vs_carry")} for f in top]},
        "verdict": j.get("verdict", "—"),
        "caveats": ["截面相对边", "需对抗验证", "幸存者偏差"],
        "provenance": "tools/crypto_oi_research.py",
    }


def adapt_oi_verify(j: dict) -> dict:
    return {
        "title": "crowd_contra 证伪 battery",
        "params": {"universe": len(j.get("universe", []))},
        "metrics": {"price_sharpe": j.get("base", {}).get("price_sharpe") if isinstance(j.get("base"), dict) else j.get("price_sharpe"),
                    "incremental_ir": j.get("combine", {}).get("incremental_ir_vs_carry") if isinstance(j.get("combine"), dict) else None},
        "series": {"exec_lag": [{"label": f"lag{k}", "value": v}
                                for k, v in sorted((j.get("price_sharpe_by_lag") or {}).items())]},
        "verdict": j.get("verdict", "—"),
        "caveats": ["多重检验", "近端软化", "幸存者"],
        "provenance": "tools/crypto_oi_verify.py",
    }


def adapt_combo(j: dict) -> dict:
    return {
        "title": "carry × crowd 双因子组合",
        "params": {"universe": len(j.get("universe", [])), "n_days": j.get("n_days"),
                   "weights": j.get("weights")},
        "metrics": {"combo_sharpe": j.get("combo_econ_sharpe"),
                    "max_drawdown": j.get("combo_max_drawdown"),
                    "net_10bps": (j.get("combo_net_sharpe_by_fee") or {}).get("10"),
                    "lift_vs_best": j.get("lift_vs_best_alone")},
        "series": {"per_year": _y(j.get("combo_per_year", {})),
                   "net_by_fee": [{"label": f"{k}bps", "value": v}
                                  for k, v in sorted((j.get("combo_net_sharpe_by_fee") or {}).items(),
                                                     key=lambda kv: int(kv[0]))]},
        "verdict": j.get("verdict", "—"),
        "caveats": ["继承 crowd 幸存者折扣", "历史回测≠未来"],
        "provenance": "tools/crypto_combo_book.py",
    }


def adapt_survivorship(j: dict) -> dict:
    return {
        "title": "crowd_contra 幸存者完整重测",
        "params": {"n_survivors": j.get("n_survivors"), "n_complete": j.get("n_complete"),
                   "dead": j.get("dead_present")},
        "metrics": {"complete_sharpe_raw": j.get("raw_complete_sharpe"),
                    "survivors_only": j.get("survivors_only_sharpe"),
                    "n_dead_shorted": j.get("n_dead_shorted")},
        "series": {"per_year": _y(j.get("per_year_complete", {})),
                   "survivors_vs_complete": [
                       {"label": "survivors", "value": j.get("survivors_only_sharpe")},
                       {"label": "+dead", "value": j.get("raw_complete_sharpe")}]},
        "verdict": j.get("verdict", "—"),
        "caveats": ["~0.3 幸存者折扣", "非崩盘对冲", "WEAK_LEAD"],
        "provenance": "tools/crypto_oi_survivorship.py",
    }


def adapt_factory(kind: str, j: dict) -> dict:
    disc = j.get("discoveries") or j.get("discovered_chain") or []
    title = "自主正交发现(放大)" if kind == "factory_orthogonal" else "自主正交发现(loop-until-dry)"
    return {
        "title": title,
        "params": {"universe": len(j.get("universe", [])), "fields": len(j.get("fields", [])),
                   "generations": j.get("generations"), "rounds": j.get("rounds")},
        "metrics": {"oos_mean": j.get("oos_mean"), "n_evaluated": j.get("n_evaluated"),
                    "n_discoveries": len(disc)},
        "series": {},
        "verdict": j.get("verdict", "—"),
        "caveats": ["选择膨胀", "lead 非确认", "需完整验证"],
        "provenance": f"tools/crypto_{kind}.py",
    }


# filename -> (kind, adapter)
SOURCES = {
    "crypto_carry_strategy.json": ("carry_strategy", adapt_carry),
    "crypto_oi_research.json": ("oi_research", adapt_oi_research),
    "crypto_oi_verify.json": ("oi_research", adapt_oi_verify),
    "crypto_combo_book.json": ("combo_book", adapt_combo),
    "crypto_oi_survivorship.json": ("survivorship", adapt_survivorship),
    "crypto_factory_orthogonal.json": ("factory_orthogonal", lambda j: adapt_factory("factory_orthogonal", j)),
    "crypto_factory_loop.json": ("factory_loop", lambda j: adapt_factory("factory_loop", j)),
}


def index_existing(cache_dir: str, registry: RunRegistry, *, now: int) -> int:
    """Ingest every present cached result JSON into the registry as a done ResearchRun. Returns count."""
    n = 0
    for fname, (kind, adapter) in SOURCES.items():
        path = os.path.join(cache_dir, fname)
        if not os.path.exists(path):
            continue
        try:
            j = json.load(open(path, encoding="utf-8"))
            if fname == "crypto_carry_strategy.json":
                j = _merge_carry_voltarget(cache_dir, j)
            a = adapter(j)
        except Exception:
            continue                                         # skip a malformed file, never fabricate
        run = ResearchRun(run_id=make_run_id(kind, {"source": fname}, now), kind=kind,
                          status="done", created_ts=now, finished_ts=now,
                          title=a["title"], params=a["params"], metrics=a["metrics"], series=a["series"],
                          verdict=a["verdict"], caveats=a["caveats"], provenance=a["provenance"])
        registry.upsert(run)
        n += 1
    return n
