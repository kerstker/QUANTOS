"""Read-only alpha library, DSL, graph, and crypto HTTP views."""

from __future__ import annotations

import json
import math
import os
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List

from fastapi import APIRouter

from services.alpha.factor_set import select_synergistic_set
from services.console.api.registry_snapshots import (
    _load_experiment_store,
    _load_paper_track_store,
)
from services.research.factor_engine.domain.model import compute_max_drawdown


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CRYPTO_SNAPSHOT_PATH = os.path.join(_REPO_ROOT, "data", "cache", "crypto_research_snapshot.json")


@dataclass(frozen=True)
class AlphaLibraryDependencies:
    load_real_factors: Callable[[], List[Dict]]
    alpha_candidates: Callable[..., List[Dict]]
    factor_values: Callable[[int], List[float]]
    forward_returns: Callable[[List[float], int], List[float]]
    input_provenance: Callable[[], str]
    lineage: List[Dict]


class AlphaLibraryRuntime:
    def __init__(self) -> None:
        self._dependencies: AlphaLibraryDependencies | None = None

    def configure(
        self,
        *,
        load_real_factors: Callable[[], List[Dict]],
        alpha_candidates: Callable[..., List[Dict]],
        factor_values: Callable[[int], List[float]],
        forward_returns: Callable[[List[float], int], List[float]],
        input_provenance: Callable[[], str],
        lineage: List[Dict],
    ) -> None:
        dependencies = AlphaLibraryDependencies(
            load_real_factors=load_real_factors,
            alpha_candidates=alpha_candidates,
            factor_values=factor_values,
            forward_returns=forward_returns,
            input_provenance=input_provenance,
            lineage=lineage,
        )
        if self._dependencies is None:
            self._dependencies = dependencies
            return
        if self._dependencies != dependencies:
            raise RuntimeError("alpha library runtime is already configured")

    def require(self) -> AlphaLibraryDependencies:
        if self._dependencies is None:
            raise RuntimeError("alpha library runtime must be configured by the console composition root")
        return self._dependencies


alpha_library_runtime = AlphaLibraryRuntime()


def configure_alpha_library_views(
    *,
    load_real_factors: Callable[[], List[Dict]],
    alpha_candidates: Callable[..., List[Dict]],
    factor_values: Callable[[int], List[float]],
    forward_returns: Callable[[List[float], int], List[float]],
    input_provenance: Callable[[], str],
    lineage: List[Dict],
) -> None:
    alpha_library_runtime.configure(
        load_real_factors=load_real_factors,
        alpha_candidates=alpha_candidates,
        factor_values=factor_values,
        forward_returns=forward_returns,
        input_provenance=input_provenance,
        lineage=lineage,
    )


def _load_real_factors() -> List[Dict]:
    return alpha_library_runtime.require().load_real_factors()


def _alpha_candidates(count: int = 12) -> List[Dict]:
    return alpha_library_runtime.require().alpha_candidates(count=count)


def _factor_values(idx: int) -> List[float]:
    return alpha_library_runtime.require().factor_values(idx)


def _forward_returns(factors: List[float], idx: int) -> List[float]:
    return alpha_library_runtime.require().forward_returns(factors, idx)


def _input_provenance() -> str:
    return alpha_library_runtime.require().input_provenance()


def _lineage() -> List[Dict]:
    return alpha_library_runtime.require().lineage


def _corr(a: List[float], b: List[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / (va**0.5 * vb**0.5) if va > 0 and vb > 0 else 0.0


def _inverse_vol_weights(ids: List[str], rbi: Dict[str, List[float]]) -> Dict[str, float]:
    inv = {a: 1.0 / (statistics.pstdev(rbi[a]) or 1e-9) for a in ids}
    tot = sum(inv.values()) or 1.0
    return {a: inv[a] / tot for a in ids}


def _weighted_max_dd(ids: List[str], rbi: Dict[str, List[float]]) -> float:
    """Max drawdown of the inverse-vol-weighted portfolio of `ids` (real per-factor net returns)."""
    if not ids:
        return 0.0
    w = _inverse_vol_weights(ids, rbi)
    n = min(len(rbi[a]) for a in ids)
    equity, curve = 1.0, []
    for d in range(n):
        equity *= 1.0 + sum(w[a] * rbi[a][d] for a in ids)
        curve.append(equity)
    return compute_max_drawdown(curve) or 0.0


def _population_optimizer_payload() -> Dict:
    """De-redundant SET selection (synergistic forward-selection by incremental IR) + inverse-vol weights +
    marginal IR / marginal drawdown + excluded-with-reasons + diversity (1−mean|corr|). Runs on the REAL
    per-factor return series when present; fail-closed to a deterministic synthetic set otherwise."""
    rf = _load_real_factors()
    if rf:
        rbi = {f["alpha_id"]: f["returns"] for f in rf}
        sel = select_synergistic_set(rbi, max_k=6, min_incremental_ir=0.0)
        ids = sel["selected_ids"]
        weights = _inverse_vol_weights(ids, rbi) if ids else {}
        selected, prev_dd, acc = [], 0.0, []
        for s in sel["selected"]:
            acc.append(s["id"])
            dd = _weighted_max_dd(acc, rbi)
            selected.append(
                {
                    "alpha_id": s["id"],
                    "weight": round(weights.get(s["id"], 0.0), 4),
                    "marginal_ir": round(s["incremental_ir"], 4),
                    "marginal_dd": round(dd - prev_dd, 4),
                }
            )  # Δ set max-DD as this factor is added
            prev_dd = dd
        pairs = [(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]
        diversity = 1.0 - (statistics.fmean([abs(_corr(rbi[a], rbi[b])) for a, b in pairs]) if pairs else 0.0)
        excluded = [{"alpha_id": aid, "reason": "REDUNDANT_CORRELATED"} for aid in sel["dropped_redundant"]]
        return {
            "selected": selected,
            "excluded": excluded,
            "diversity": round(diversity, 3),
            "gross": round(sum(abs(s["weight"]) for s in selected), 2),
            "method": sel["method"],
            "n_candidates": sel["n_candidates"],
        }
    # fail-closed synthetic (CI / no snapshot): deterministic from the synthetic candidate pool
    cands = sorted(_alpha_candidates(), key=lambda a: a["composite_score"], reverse=True)
    sel_c, exc_c = cands[:4], cands[4:7]
    tot = sum(c["composite_score"] for c in sel_c) or 1.0
    selected = [
        {
            "alpha_id": c["candidate_id"],
            "weight": round(c["composite_score"] / tot, 4),
            "marginal_ir": round(c["icir"], 4),
            "marginal_dd": round((c["correlation_penalty"] - 0.1) * 0.3, 4),
        }
        for c in sel_c
    ]
    excluded = [
        {
            "alpha_id": c["candidate_id"],
            "reason": "REDUNDANT_CORRELATED" if c["correlation_penalty"] > 0.1 else "DROPPED_LOW_INCREMENTAL_IR",
        }
        for c in exc_c
    ]
    return {
        "selected": selected,
        "excluded": excluded,
        "diversity": 0.71,
        "gross": round(sum(abs(s["weight"]) for s in selected), 2),
        "method": "FORWARD_SELECTION_INCREMENTAL_IR",
        "n_candidates": len(cands),
    }


def _short_factor_label(aid: str) -> str:
    s = aid
    for p in ("hx_product_", "hx_sum_", "hx_", "h_"):
        if s.startswith(p):
            s = s[len(p) :]
            break
    s = s.replace("_price-", "·").replace("price-", "").replace("_", "·")
    return s[:18]


def _expr_fields(expr) -> List[str]:
    out: set = set()

    def walk(t):
        if isinstance(t, tuple) and t:
            if t[0] == "field" and len(t) > 1:
                out.add(t[1])
            for a in t[1:]:
                walk(a)

    walk(expr)
    return sorted(out)


def _dsl_explorer_payload() -> Dict:
    """Cross-sectional alpha expressions with their REAL static analysis: the dsl_checker type/dimension
    verdict, the operator-weighted complexity, and the machine-proof PIT-leakage result (the real
    expression_leakage detector perturbs strictly-future rows). Verdicts are COMPUTED by the real engines,
    not hand-authored — the deterministic panel is only the substrate the leakage detector perturbs."""
    import services.research.alpha_expression as ax
    from services.research.dsl_checker import check_expression
    from services.research.complexity import complexity as _cx
    from services.research import expression_leakage as el

    insts, T = ["A", "B", "C", "D"], 16

    def _mat(base):
        return [[base + 0.5 * t + 0.1 * j for j in range(len(insts))] for t in range(T)]

    panel = ax.FieldPanel(insts, list(range(T)), {"close": _mat(100.0), "volume": _mat(10.0), "funding": _mat(0.01)})
    EXPRS = [
        (("cs_zscore", ("neg", ("decay_linear", ("field", "funding"), 7))), "资金费 carry 原语 / funding carry"),
        (("cs_rank", ("ts_delta", ("field", "close"), 5)), "价格动量 / price momentum"),
        (("cs_residualize", ("field", "close"), ("field", "volume")), "对量能中性化 / size-neutralized"),
        (("cs_zscore", ("ts_mean", ("field", "close"), 5)), "均值回复 / mean-reversion"),
        (("cs_rank", ("ts_lead", ("field", "close"), 1)), "读未来 → 检查器拒绝 / future-reading: rejected"),
    ]
    rows = []
    for expr, note in EXPRS:
        chk = check_expression(expr)
        try:
            cx_val = round(_cx(expr), 2)
        except Exception:
            cx_val = 0.0
        try:
            leak = el.compute_leakage_ok(expr, panel)
        except Exception:
            leak = None
        rows.append(
            {
                "expr": ax.serialize(expr),
                "valid": bool(chk.ok),
                "complexity": cx_val,
                "leakage_ok": leak,
                "deps": _expr_fields(expr),
                "note": note,
                "errors": list(chk.errors)[:3],
            }
        )
    return {"exprs": rows, "analysis_backend": "REAL_DSL_CHECKER+LEAKAGE+COMPLEXITY"}


# The full multi-dimensional factor-analysis schema surfaced by the Alpha Zoo. Each entry is one real
# dimension of an alpha's immutable evaluation artifact (experiment_registry), grouped for the UI. `fmt`
# tells the frontend how to render it; `good` is the direction that is better (for conditional colouring).
_ZOO_DIMENSIONS = [
    {"key": "ic", "zh": "IC", "en": "IC", "group": "预测力", "fmt": "signed3", "good": "high"},
    {"key": "icir", "zh": "ICIR", "en": "ICIR", "group": "预测力", "fmt": "signed2", "good": "high"},
    {"key": "ic_tstat", "zh": "IC t值", "en": "IC t-stat", "group": "预测力", "fmt": "signed2", "good": "high"},
    {"key": "hit_rate", "zh": "命中率", "en": "Hit rate", "group": "预测力", "fmt": "pct", "good": "high"},
    {"key": "net_sharpe", "zh": "净夏普", "en": "Net Sharpe", "group": "业绩", "fmt": "signed2", "good": "high"},
    {
        "key": "deflated_sharpe",
        "zh": "紧缩夏普",
        "en": "Deflated SR",
        "group": "过拟合",
        "fmt": "signed2",
        "good": "high",
    },
    {"key": "pbo", "zh": "PBO", "en": "PBO", "group": "过拟合", "fmt": "pct", "good": "low"},
    {"key": "turnover", "zh": "换手", "en": "Turnover", "group": "成本", "fmt": "signed2", "good": "low"},
    {"key": "marginal_ir", "zh": "增量IR", "en": "Marginal IR", "group": "正交", "fmt": "signed2", "good": "high"},
    {"key": "crowding_score", "zh": "拥挤度", "en": "Crowding", "group": "拥挤/容量", "fmt": "signed2", "good": "low"},
    {
        "key": "capacity_adj_sharpe",
        "zh": "容量调整夏普",
        "en": "Cap-adj SR",
        "group": "拥挤/容量",
        "fmt": "signed2",
        "good": "high",
    },
    {"key": "mean_breadth", "zh": "广度", "en": "Breadth", "group": "广度", "fmt": "num1", "good": "high"},
    {"key": "paper_ic", "zh": "纸面IC", "en": "Paper IC", "group": "时间/活体征", "fmt": "signed3", "good": "high"},
    {
        "key": "paper_sharpe",
        "zh": "纸面夏普",
        "en": "Paper SR",
        "group": "时间/活体征",
        "fmt": "signed2",
        "good": "high",
    },
    {
        "key": "perf_gap",
        "zh": "回测-纸面差",
        "en": "BT-paper gap",
        "group": "时间/活体征",
        "fmt": "signed2",
        "good": "low",
    },
    {
        "key": "n_paper_periods",
        "zh": "纸面期数",
        "en": "Paper periods",
        "group": "时间/活体征",
        "fmt": "num1",
        "good": "high",
    },
]
_ZOO_DIM_KEYS = tuple(d["key"] for d in _ZOO_DIMENSIONS)


def _zoo_tags(operator: str, ev: Dict) -> List[str]:
    """Honest, dimension-derived descriptors (not decoration): the operator family + factual flags."""
    tags = [operator] if operator else []
    tn = ev.get("turnover")
    if isinstance(tn, (int, float)) and tn < 0.3:
        tags.append("low-turnover")
    pb = ev.get("pbo")
    if isinstance(pb, (int, float)) and pb > 0.5:
        tags.append("overfit-risk")
    mir = ev.get("marginal_ir")
    if isinstance(mir, (int, float)) and mir > 0.3:
        tags.append("orthogonal")
    if (ev.get("mean_breadth") or 0) < 5:
        tags.append("thin")
    return tags


def _alpha_zoo_rows_real(st) -> List[Dict]:
    """One Zoo row per recorded alpha, carrying its FULL multi-dimensional evaluation (IC/ICIR/t-stat/
    hit-rate/turnover/PBO/deflated-SR/marginal-IR/crowding/capacity/breadth/verdict) from the immutable
    experiment_registry artifacts — the same source LifecycleMonitor reads. Generator + cluster + tags are
    derived from the (real) heuristic operator. Every numeric dimension is the real persisted value or None."""

    def _r(v, n):
        return round(v, n) if isinstance(v, (int, float)) else None

    try:
        alphas = st.all_alphas()
    except Exception:
        return []
    pts = _load_paper_track_store()
    rows: List[Dict] = []
    for a in sorted(alphas, key=lambda x: (x.get("sharpe") if x.get("sharpe") is not None else -1e9), reverse=True):
        aid = a.get("alpha_id")
        try:
            evs = st.list_evaluations(aid)
            ej = json.loads(evs[-1].get("evaluation_json") or "{}") if evs else {}
        except (json.JSONDecodeError, TypeError, Exception):
            ej = {}
        try:
            dj = json.loads(a.get("definition_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            dj = {}
        operator = (dj.get("params") or {}).get("operator") or ""
        pbo_evidence = ej.get("pbo_evidence") if isinstance(ej.get("pbo_evidence"), dict) else {}
        catalog_evidence = (
            ej.get("catalog_evidence")
            if isinstance(ej.get("catalog_evidence"), dict)
            else {}
        )
        candidate_hashes = pbo_evidence.get("candidate_definition_hashes")
        row = {
            "alpha_id": aid,
            "name": dj.get("name") or _short_factor_label(aid or ""),
            "generator": f"heuristic:{operator}" if operator else "heuristic_sweep",
            "status": str(a.get("state") or "TESTING"),
            "cluster_id": f"cl:{operator}" if operator else "cl:base",
            "verdict": str(ej.get("verdict") or "—"),
            "tags": _zoo_tags(operator, ej),
            # full multi-dimensional evaluation (real persisted values)
            "ic": _r(ej.get("ic_mean"), 4),
            "icir": _r(ej.get("icir"), 3),
            "ic_tstat": _r(ej.get("ic_tstat"), 2),
            "hit_rate": _r(ej.get("hit_rate"), 3),
            "net_sharpe": _r(ej.get("net_sharpe"), 3),
            "deflated_sharpe": _r(ej.get("deflated_sharpe"), 3),
            "pbo": _r(ej.get("pbo"), 3),
            # Redacted provenance only: the retained block matrix never leaves the store.
            "pbo_evidence_hash": ej.get("pbo_evidence_hash") or None,
            "pbo_alignment": pbo_evidence.get("alignment") or None,
            "pbo_candidate_count": (len(candidate_hashes)
                                    if isinstance(candidate_hashes, list) else None),
            "catalog_evidence_hash": ej.get("catalog_evidence_hash") or None,
            "catalog_snapshot_hash": (
                catalog_evidence.get("catalog_snapshot_hash") or None
            ),
            "catalog_trial_count": catalog_evidence.get("trial_count"),
            "catalog_effective_trial_count": catalog_evidence.get(
                "effective_trial_count"
            ),
            "catalog_mean_abs_correlation": _r(
                catalog_evidence.get("mean_abs_correlation"), 4
            ),
            "catalog_var_sr": _r(catalog_evidence.get("var_sr"), 6),
            "catalog_trial_set_hash": ej.get("catalog_trial_set_hash") or None,
            "turnover": _r(ej.get("turnover"), 3),
            "marginal_ir": _r(ej.get("marginal_ir"), 3),
            "crowding_score": _r(ej.get("crowding_score"), 3),
            "capacity_adj_sharpe": _r(ej.get("capacity_adj_sharpe"), 3),
            "mean_breadth": _r(ej.get("mean_breadth"), 1),
        }
        # 时间/活体征 dimensions from the REAL forward paper-track store (out-of-sample paper P&L / IC);
        # None when this alpha has no paper track (fail-closed). These are a research-only record and
        # NEVER unlock any automatic promotion (auto_approved stays 0; non-human never APPROVED).
        t = pts.track(aid) if pts is not None else None
        row["paper_ic"] = t.get("paper_ic") if t else None
        row["paper_sharpe"] = t.get("paper_sharpe") if t else None
        row["perf_gap"] = t.get("perf_gap") if t else None
        row["n_paper_periods"] = t.get("n_periods") if t else None
        rows.append(row)
    return rows


def _alpha_zoo_rows_synthetic() -> List[Dict]:
    """Deterministic fallback (CI / no registry): the candidate set with plausible per-dimension values so
    the multi-dimensional page shape stays exercised. Marked SYNTHETIC at the endpoint."""
    gens = ["cs_genome", "gp", "mcts", "evolutionary"]
    rows: List[Dict] = []
    for i, c in enumerate(_alpha_candidates()):
        ic = round(c.get("ic", 0.0), 4)
        rows.append(
            {
                "alpha_id": c["candidate_id"],
                "name": c["expression"],
                "generator": gens[i % len(gens)],
                "status": c["lifecycle_state"],
                "cluster_id": f"cl_{i % 3}",
                "verdict": "REQUIRES_HUMAN_REVIEW",
                "tags": ["synthetic"],
                "ic": ic,
                "icir": round(c.get("icir", 0.0), 3),
                "ic_tstat": round(ic * 30, 2),
                "hit_rate": round(0.5 + ic, 3),
                "net_sharpe": round(c.get("sharpe", 0.0), 3),
                "deflated_sharpe": round(min(1.0, c.get("composite_score", 0.0)), 3),
                "pbo": round((i % 5) * 0.1, 3),
                "pbo_evidence_hash": None,
                "pbo_alignment": None,
                "pbo_candidate_count": None,
                "catalog_evidence_hash": None,
                "catalog_snapshot_hash": None,
                "catalog_trial_count": None,
                "catalog_effective_trial_count": None,
                "catalog_mean_abs_correlation": None,
                "catalog_var_sr": None,
                "catalog_trial_set_hash": None,
                "turnover": round(c.get("turnover", 0.3), 3),
                "marginal_ir": round(c.get("rank_ic", 0.0), 3),
                "crowding_score": round((i % 4) * 0.15, 3),
                "capacity_adj_sharpe": round(c.get("sharpe", 0.0) * 0.9, 3),
                "mean_breadth": float(10 + i % 8),
                # 时间/活体征: synthetic fallback has no real forward paper track -> honest None
                "paper_ic": None,
                "paper_sharpe": None,
                "perf_gap": None,
                "n_paper_periods": None,
            }
        )
    return rows


def _alpha_zoo_payload() -> Dict:
    """The searchable, multi-dimensional Alpha catalog. Real path reads the immutable experiment_registry
    (full per-alpha evaluation across ~12 dimensions); fail-closed to a deterministic synthetic set."""
    st = _load_experiment_store()
    rows, provenance = [], "SYNTHETIC_DETERMINISTIC"
    if st is not None:
        rows = _alpha_zoo_rows_real(st)
        if rows:
            provenance = "EXPERIMENT_REGISTRY"
    if not rows:
        rows = _alpha_zoo_rows_synthetic()
        provenance = "SYNTHETIC_DETERMINISTIC"
    return {"rows": rows, "dimensions": _ZOO_DIMENSIONS, "data_provenance": provenance}


def _alpha_graph_payload() -> Dict:
    """Real correlation network: nodes = factors, edges = |return correlation|, clusters = connected
    components above RHO_CUT (redundancy groups). Uses the real services/alpha correlation_graph engine on
    the REAL per-factor return series when present, else on the synthetic candidate streams (same engine)."""
    from services.alpha.alpha_graph import correlation_graph

    rho_cut, edge_min = 0.7, 0.3
    rf = _load_real_factors()
    if rf:
        rbi = {f["alpha_id"]: tuple(f["returns"]) for f in rf}
    else:
        rbi = {f"alpha-{i + 1}": tuple(_forward_returns(_factor_values(i), i)) for i in range(7)}
    g = correlation_graph(rbi)
    comps = sorted(g.clusters(rho_cut), key=lambda c: sorted(c)[0])
    cluster_of = {n: ci for ci, comp in enumerate(comps) for n in comp}
    n_cl = max(1, len(comps))
    nodes = []
    for ci, comp in enumerate(comps):
        cx = 300 + 200 * math.cos(2 * math.pi * ci / n_cl)
        cy = 180 + 130 * math.sin(2 * math.pi * ci / n_cl)
        members = sorted(comp)
        for mi, mid in enumerate(members):
            ang = 2 * math.pi * mi / max(1, len(members))
            r = 0.0 if len(members) == 1 else 44.0
            nodes.append(
                {
                    "id": mid,
                    "label": _short_factor_label(mid),
                    "x": round(cx + r * math.cos(ang), 1),
                    "y": round(cy + r * math.sin(ang), 1),
                    "cluster": f"cl_{cluster_of[mid]}",
                }
            )
    edges = [{"a": a, "b": b, "corr": round(w, 3)} for a, b, w in g.near_duplicate_pairs(edge_min)]
    n_near = sum(1 for e in edges if e["corr"] >= rho_cut)
    return {
        "nodes": nodes,
        "edges": edges,
        "clusters": [{"name": f"cl_{i}", "count": len(c)} for i, c in enumerate(comps)],
        "rho_cut": rho_cut,
        "n_near_dup": n_near,
    }


_CRYPTO_EMPTY_META = {
    "provider": "",
    "asset_class": "CRYPTO",
    "vs_currency": "",
    "days": 0,
    "granularity": "",
    "fwd_horizon": 0,
    "n_instruments": 0,
    "n_periods": 0,
    "coins": [],
    "search_mode": "",
    "evo_generations": 0,
    "evo_population": 0,
    "evo_elite": 0,
    "operators": [],
    "windows": [],
}
_CRYPTO_EMPTY_SUMMARY = {
    "n_evaluated": 0,
    "trial_count": 0,
    "auto_approved": 0,
    "requires_human_review": 0,
    "rejected": 0,
    "max_net_sharpe": None,
    "max_deflated_sharpe": None,
    "top_alpha_id": None,
}


def _crypto_live_payload() -> Dict:
    """Serve the persisted REAL-crypto research snapshot (generated OUT OF GATE by
    tools/crypto_research_run.py from read-only CoinGecko public OHLC). This endpoint
    NEVER fetches: the no-network-import ban inside services/ is machine-enforced
    (tests/governance/test_crypto_data_boundary.py); it only reads the local artifact.

    Fail-closed: when the snapshot is absent (the gate / CI never commits raw research
    output — data/cache is gitignored) or corrupt, it returns a well-formed EMPTY
    payload so the frontend contract still holds and the page shows an honest
    'no live snapshot yet' state instead of 500-ing or rendering undefined.

    Provenance is labelled REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT — distinct from the
    SYNTHETIC_DETERMINISTIC demo endpoints — so the honesty marker stays truthful."""
    snap = None
    if os.path.exists(_CRYPTO_SNAPSHOT_PATH):
        try:
            with open(_CRYPTO_SNAPSHOT_PATH, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict) and isinstance(loaded.get("factors"), list):
                snap = loaded
        except (ValueError, OSError):
            snap = None  # corrupt artifact -> honest empty, never crash the read-only API
    present = snap is not None
    summary = snap.get("summary", _CRYPTO_EMPTY_SUMMARY) if present else _CRYPTO_EMPTY_SUMMARY
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT",
        "crypto_mode": "READ_ONLY_OFFLINE_SNAPSHOT",
        "snapshot_present": present,
        "generated_at": snap.get("generated_at") if present else None,
        "generated_at_iso": snap.get("generated_at_iso", "") if present else "",
        "dataset_version": snap.get("dataset_version", "") if present else "",
        "meta": snap.get("meta", _CRYPTO_EMPTY_META) if present else _CRYPTO_EMPTY_META,
        "summary": summary,
        "factors": snap.get("factors", []) if present else [],
        # out-of-sample (no-peek walk-forward) validation block, merged by
        # tools/crypto_oos_validation.py; None until that has been run.
        "oos": snap.get("oos") if present else None,
        # de-redundant best set (incremental-IR forward selection); None until a GP/evo run
        # that emits it has been written.
        "synergistic_set": snap.get("synergistic_set") if present else None,
        # top-factor diagnostics for the factor-factory dashboard: cost-stress curve +
        # regime split (merged by tools/crypto_daily_research.py); None until a daily run.
        "cost_stress": snap.get("cost_stress") if present else None,
        "regime": snap.get("regime") if present else None,
        "safety": {
            "network_in_services": False,
            "fetch_path": "OUT_OF_GATE tools/coingecko_fetch.py",
            "auto_approved": summary.get("auto_approved", 0),  # containment: must stay 0
            "agent_can_reach_approved": False,
            "live_trading": "DISALLOWED",
        },
    }


router = APIRouter()


@router.get("/api/v1/console/population-optimizer")
async def population_optimizer() -> Dict:
    payload = _population_optimizer_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": _input_provenance(),
        **payload,
        "lineage": [item for item in _lineage() if item["layer"] == "Alpha"],
    }


@router.get("/api/v1/console/dsl-explorer")
async def dsl_explorer() -> Dict:
    payload = _dsl_explorer_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
        "lineage": [item for item in _lineage() if item["layer"] == "Alpha"],
    }


@router.get("/api/v1/console/alpha-zoo")
async def alpha_zoo() -> Dict:
    payload = _alpha_zoo_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": _input_provenance(),
        **payload,
        "lineage": [item for item in _lineage() if item["layer"] == "Alpha"],
    }


@router.get("/api/v1/console/alpha-graph")
async def alpha_graph() -> Dict:
    payload = _alpha_graph_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": _input_provenance(),
        **payload,
        "lineage": [item for item in _lineage() if item["layer"] == "Alpha"],
    }


@router.get("/api/v1/console/crypto-live")
async def crypto_live() -> Dict:
    return _crypto_live_payload()
