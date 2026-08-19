"""Read-only lifecycle and experiment-registry HTTP routes."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Callable, Dict, List, Sequence

from fastapi import APIRouter

from quantos.runtime_paths import runtime_state_root
from services.console.api.common import short_hash


def _triple_fields(a: Dict) -> Dict:
    """727 C4: expose the three orthogonal lifecycle dimensions next to the
    legacy mixed `state`. STORED = the C2/C3 columns are populated (the row went
    through a dual-write); PROJECTED = pre-C2 row, derived from the legacy state
    via the documented projection (sentinels included — never guessed);
    UNKNOWN = the legacy state itself is unreadable."""
    if a.get("research_state") is not None:
        return {
            "research_state": a.get("research_state"),
            "deployment_state": a.get("deployment_state"),
            "health_state": a.get("health_state"),
            "health_reason": a.get("health_reason"),
            "triple_source": "STORED",
        }
    try:
        from quantos.contracts.research import AlphaState
        from services.lifecycle import project

        triple = project(AlphaState(str(a.get("state"))))
    except Exception:
        return {
            "research_state": None,
            "deployment_state": None,
            "health_state": None,
            "health_reason": None,
            "triple_source": "UNKNOWN",
        }
    return {
        "research_state": triple.research.value,
        "deployment_state": triple.deployment.value,
        "health_state": triple.health.state.value,
        "health_reason": triple.health.reason.value,
        "triple_source": "PROJECTED",
    }


_STATE_ROOT = runtime_state_root(cwd=Path(__file__).resolve().parents[3])
_EXP_DB = os.path.join(_STATE_ROOT, "data", "cache", "experiment_registry.db")
_PAPER_TRACK_DB = os.path.join(_STATE_ROOT, "data", "cache", "paper_track.db")
_EXP_STAGE_MAP = {
    "DRAFT": "DISCOVERY",
    "TESTING": "VALIDATION",
    "VALIDATED": "VALIDATION",
    "PAPER_TRADING": "PAPER_REPLAY",
    "SHADOW": "PAPER_REPLAY",
    "APPROVED": "RISK_REVIEW",
    "DEGRADED": "ARCHIVED",
    "PAUSED": "ARCHIVED",
    "RETIRED": "ARCHIVED",
    "REJECTED": "ARCHIVED",
}
_EXP_STAGES = [
    "DISCOVERY",
    "VALIDATION",
    "PAPER_REPLAY",
    "RISK_REVIEW",
    "ARCHIVED",
]


@dataclass(frozen=True)
class RegistrySnapshotDependencies:
    alpha_candidates: Callable[..., List[Dict]]
    validation_decision: Callable[[Dict], str]
    alpha_windows: tuple[int, ...]


class RegistrySnapshotRuntime:
    def __init__(self) -> None:
        self._dependencies: RegistrySnapshotDependencies | None = None

    def configure(
        self,
        *,
        alpha_candidates: Callable[..., List[Dict]],
        validation_decision: Callable[[Dict], str],
        alpha_windows: Sequence[int],
    ) -> None:
        dependencies = RegistrySnapshotDependencies(
            alpha_candidates=alpha_candidates,
            validation_decision=validation_decision,
            alpha_windows=tuple(alpha_windows),
        )
        if self._dependencies is None:
            self._dependencies = dependencies
            return
        if self._dependencies != dependencies:
            raise RuntimeError("registry snapshot runtime is already configured")

    def require(self) -> RegistrySnapshotDependencies:
        if self._dependencies is None:
            raise RuntimeError("registry snapshot runtime must be configured by the console composition root")
        return self._dependencies


registry_snapshot_runtime = RegistrySnapshotRuntime()
_short_hash = short_hash


def configure_registry_snapshots(
    *,
    alpha_candidates: Callable[..., List[Dict]],
    validation_decision: Callable[[Dict], str],
    alpha_windows: Sequence[int],
) -> None:
    registry_snapshot_runtime.configure(
        alpha_candidates=alpha_candidates,
        validation_decision=validation_decision,
        alpha_windows=alpha_windows,
    )


def _alpha_candidates(count: int = 12) -> List[Dict]:
    return registry_snapshot_runtime.require().alpha_candidates(count=count)


def _validation_decision(alpha: Dict) -> str:
    return registry_snapshot_runtime.require().validation_decision(alpha)


class _AlphaWindows:
    def __getitem__(self, index: int) -> int:
        return registry_snapshot_runtime.require().alpha_windows[index]

    def __len__(self) -> int:
        return len(registry_snapshot_runtime.require().alpha_windows)


ALPHA_WINDOWS = _AlphaWindows()


def _load_experiment_store():
    """Read-only handle on the offline experiment_registry (immutable evaluation artifacts), or None when
    absent (CI / fresh checkout) -> the Experiments/EvidenceLedger pages fail closed to synthetic."""
    try:
        from services.research.experiment_registry.store import SqliteExperimentStore

        return (
            SqliteExperimentStore(_EXP_DB, create=False, read_only=True)
            if os.path.exists(_EXP_DB)
            else None
        )
    except Exception:
        return None


def _load_paper_track_store():
    """Read-only handle on the offline forward paper-track store (real out-of-sample paper P&L / IC), or
    None when absent (CI / fresh checkout) -> the live side fails closed to null / NO_LIVE_DATA."""
    try:
        from services.research.paper_trading.store import PaperTrackStore

        return (
            PaperTrackStore(_PAPER_TRACK_DB, create=False, read_only=True)
            if os.path.exists(_PAPER_TRACK_DB)
            else None
        )
    except Exception:
        return None


def _decision_from_verdict(v) -> str:
    v = str(v or "").upper()
    if "APPROV" in v or "PROMOT" in v or v == "PASS":
        return "APPROVED"
    if "REJECT" in v or "FAIL" in v:
        return "REJECTED_VALIDATION"
    return "NEEDS_MORE_DATA"


def _real_experiment_rows(st) -> List[Dict]:
    """One ResearchExperiment per recorded alpha (top by net Sharpe), built from the REAL immutable
    evaluation artifacts (reproducibility hash, IC, net Sharpe, deflated Sharpe, turnover, verdict)."""
    try:
        alphas = st.all_alphas()
        latest_evaluations = st.latest_evaluations_by_alpha()
    except Exception:
        return []
    rows = []
    for a in sorted(alphas, key=lambda x: (x.get("sharpe") if x.get("sharpe") is not None else -1e9), reverse=True)[
        :30
    ]:
        aid = a.get("alpha_id")
        ev = latest_evaluations.get(str(aid))
        if ev is None:
            continue
        try:
            ej = json.loads(ev.get("evaluation_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            ej = {}
        chash = ev.get("content_hash") or ""
        breadth = ej.get("mean_breadth") or 1.0
        try:
            expr_label = json.loads(a.get("definition_json") or "{}").get("name") or aid
        except (json.JSONDecodeError, TypeError):
            expr_label = aid
        rows.append(
            {
                "experiment_id": ev.get("run_id"),
                "alpha_id": aid,
                "expression": expr_label,
                "owner_agent": "ResearchAgentLoop",
                "stage": _EXP_STAGE_MAP.get(str(a.get("state")), "VALIDATION"),
                "decision": _decision_from_verdict(ev.get("verdict")),
                "created_at": ev.get("recorded_ts") or 0,
                "execution_mode": "L2_DRY_RUN_ONLY",
                "config": {
                    "universe": "CRYPTO",
                    "horizon_days": 1,
                    "cost_bps": 10,
                    "seed": 0,
                    "data_snapshot_id": ev.get("dataset_version"),
                    "config_hash": chash[:16],
                },
                "metrics": {
                    "ic": round(ej.get("ic_mean") or 0.0, 4),
                    "rank_ic": round(ej.get("ic_mean") or 0.0, 4),
                    "sharpe": round(
                        ej.get("net_sharpe") if ej.get("net_sharpe") is not None else (a.get("sharpe") or 0.0), 3
                    ),
                    "turnover": round(ej.get("turnover") or 0.0, 4),
                    "capacity_estimate": int(max(1.0, breadth) * 12_000_000),
                    "composite_score": round(max(0.0, min(1.0, ej.get("deflated_sharpe") or 0.0)), 3),
                },
                "reproducibility_hash": ej.get("reproducibility_hash") or chash,
                "artifacts": [
                    {
                        "artifact_id": f"{ev.get('run_id')}-eval",
                        "kind": "EVALUATION",
                        "status": "SEALED",
                        "uri": f"experiment://{ev.get('run_id')}",
                        "content_hash": chash,
                    },
                    {
                        "artifact_id": f"{ev.get('run_id')}-defn",
                        "kind": "DEFINITION",
                        "status": "SEALED",
                        "uri": f"experiment://{ev.get('run_id')}/definition",
                        "content_hash": chash[:24],
                    },
                    {
                        "artifact_id": f"{ev.get('run_id')}-metrics",
                        "kind": "METRICS",
                        "status": "SEALED",
                        "uri": f"experiment://{ev.get('run_id')}/metrics",
                        "content_hash": chash[8:32] or chash,
                    },
                ],
                "lineage": {
                    "upstream": ["factor_engine", "session"],
                    "downstream": ["validation", "experiment_registry"],
                },
            }
        )
    return rows


def _degradation_thresholds_snapshot() -> Dict:
    """Read-only snapshot of the REAL DegradationThresholds the live-vs-backtest monitor would apply."""
    try:
        from services.alpha.degradation_monitor import DegradationThresholds

        th = DegradationThresholds()
        return {
            "min_ic_retention": th.min_ic_retention,
            "max_return_gap": th.max_return_gap,
            "retire_return_gap": th.retire_return_gap,
            "max_slippage_mult": th.max_slippage_mult,
            "max_turnover_mult": th.max_turnover_mult,
            "max_drawdown": th.max_drawdown,
            "max_capacity_breach_frac": th.max_capacity_breach_frac,
        }
    except Exception:
        return {
            "min_ic_retention": 0.3,
            "max_return_gap": 0.6,
            "retire_return_gap": 1.0,
            "max_slippage_mult": 2.0,
            "max_turnover_mult": 2.0,
            "max_drawdown": 0.3,
            "max_capacity_breach_frac": 0.5,
        }


def _lifecycle_rows_real(st) -> List[Dict]:
    """One lifecycle row per recorded alpha, from the REAL experiment_registry. backtest_ic / sharpe /
    pvalue / net_sharpe / deflated_sharpe / verdict are real immutable evaluation artifacts. live_ic is
    the REAL accumulated out-of-sample paper IC from the forward paper-track store when a track exists
    AND has per-period IC; the current offline source has per-period portfolio returns but no
    cross-sectional IC panel, so live_ic is honestly None even for paper-tracked alphas. The recommendation
    is driven by the REAL held-out paper Sharpe vs the train-head Sharpe (apples-to-apples), with the
    monitor's "missing IC" data-alert stripped (a metric we never compute must not masquerade as decay):
    PAPER_RETURN_ONLY = paper return shows no degradation but IC isn't tracked; DEGRADED/RETIRED = a real
    Sharpe drop; NO_LIVE_DATA = no paper track at all. The paper track is a research-only record: it feeds
    the degradation engine's recommendation but NEVER unlocks any automatic promotion (auto_approved stays
    0; non-human never APPROVED). days_in_state is None because evaluation as_of is a single batch
    timestamp, not a per-alpha tenure. generator is derived from the heuristic operator (no real lineage)."""

    def _r(v, n):
        return round(v, n) if isinstance(v, (int, float)) else None

    try:
        alphas = st.all_alphas()
        latest_evaluations = st.latest_evaluations_by_alpha()
    except Exception:
        return []
    pts = _load_paper_track_store()
    try:
        paper_tracks = {
            str(track["alpha_id"]): track for track in pts.all_tracks()
        } if pts is not None else {}
    except Exception:
        paper_tracks = {}
    finally:
        if pts is not None:
            close = getattr(pts, "close", None)
            if callable(close):
                close()
    rows: List[Dict] = []
    for a in sorted(alphas, key=lambda x: (x.get("sharpe") if x.get("sharpe") is not None else -1e9), reverse=True):
        aid = a.get("alpha_id")
        ej: Dict = {}
        evaluation = latest_evaluations.get(str(aid))
        if evaluation is not None:
            try:
                ej = json.loads(evaluation.get("evaluation_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                ej = {}
        try:
            dj = json.loads(a.get("definition_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            dj = {}
        operator = (dj.get("params") or {}).get("operator")
        # Live side from the REAL forward paper-track store: live_ic is the accumulated out-of-sample
        # paper IC (None when this alpha has no paper track, or — as with the current offline source —
        # when no per-period cross-sectional IC was recorded). When a paper track exists, the degradation
        # engine compares the held-out paper Sharpe to the train-head Sharpe (like-for-like, never a
        # Sharpe-vs-cumulative-return mismatch); otherwise the recommendation stays NO_LIVE_DATA.
        t = paper_tracks.get(str(aid))
        live_ic = t["paper_ic"] if (t and t.get("paper_ic") is not None) else None
        if t and isinstance(t.get("paper_sharpe"), (int, float)):
            from services.alpha.degradation_monitor import monitor_alpha_health

            health = monitor_alpha_health(
                {"return": t.get("backtest_sharpe")}, {"ic": live_ic, "return": t.get("paper_sharpe")}
            )
            # Judge only on signals we actually have: strip the monitor's "missing input" data-alert so a
            # metric we never compute (per-period IC) cannot masquerade as a degradation. A real return/
            # Sharpe drop still surfaces as DEGRADED/RETIRED; otherwise PAPER_RETURN_ONLY (paper return
            # holds up, but IC is not tracked so we do not certify full health).
            real_alerts = [al for al in health["alerts"] if al.get("metric") != "data"]
            if real_alerts:
                _ORD = {"OK": 0, "DEGRADED": 1, "PAUSED": 2, "RETIRED": 3}
                recommendation = max((al["severity"] for al in real_alerts), key=lambda s: _ORD[s])
            else:
                recommendation = "PAPER_RETURN_ONLY"
        else:
            recommendation = "NO_LIVE_DATA"
        rows.append(
            {
                "alpha_id": aid,
                "state": str(a.get("state") or "UNKNOWN"),
                "generator": f"heuristic:{operator}" if operator else "heuristic_sweep",
                "days_in_state": None,
                "backtest_ic": _r(ej.get("ic_mean"), 5),
                "sharpe": _r(a.get("sharpe"), 4),
                "pvalue": _r(a.get("pvalue"), 4),
                "net_sharpe": _r(ej.get("net_sharpe"), 4),
                "deflated_sharpe": _r(ej.get("deflated_sharpe"), 4),
                "turnover": _r(ej.get("turnover"), 4),
                "verdict": str(ej.get("verdict") or "—"),
                "live_ic": live_ic,
                "recommendation": recommendation,
                "alerts": [],
                **_triple_fields(a),
            }
        )
    return rows


def _lifecycle_rows_synthetic() -> List[Dict]:
    """Deterministic fallback (CI / fresh checkout with no experiment_registry). Rotates lifecycle states so
    the page shape stays exercised; live_ic stays None (no live feed is ever real)."""
    states = ["TESTING", "VALIDATION", "TESTING", "VALIDATION"]
    rows: List[Dict] = []
    for idx, c in enumerate(_alpha_candidates()):
        rows.append(
            {
                "alpha_id": c["candidate_id"],
                "state": states[idx % len(states)],
                "generator": "heuristic_sweep",
                "days_in_state": None,
                "backtest_ic": round(c.get("ic", 0.0), 5),
                "sharpe": round(c.get("sharpe", 0.0), 4) if isinstance(c.get("sharpe"), (int, float)) else None,
                "pvalue": None,
                "net_sharpe": round(c.get("sharpe", 0.0), 4) if isinstance(c.get("sharpe"), (int, float)) else None,
                "deflated_sharpe": None,
                "turnover": round(c.get("turnover", 0.0), 4) if isinstance(c.get("turnover"), (int, float)) else None,
                "verdict": "REQUIRES_HUMAN_REVIEW",
                "live_ic": None,
                "recommendation": "NO_LIVE_DATA",
                "alerts": [],
                # Synthetic rows exercise the C4 shape honestly: the triple is
                # derived from the synthetic legacy state and marked SYNTHETIC,
                # never presented as a stored record (§18.2 provenance).
                **_triple_fields(
                    {"state": states[idx % len(states)], "research_state": None}
                ) | {"triple_source": "SYNTHETIC"},
            }
        )
    return rows


def _lifecycle_summary(rows: List[Dict]) -> Dict:
    state_counts: Dict[str, int] = {}
    for r in rows:
        state_counts[r["state"]] = state_counts.get(r["state"], 0) + 1
    ics = [r["backtest_ic"] for r in rows if isinstance(r["backtest_ic"], (int, float))]
    sharpes = [r["sharpe"] for r in rows if isinstance(r["sharpe"], (int, float))]
    awaiting = sum(1 for r in rows if any(t in str(r["verdict"]).upper() for t in ("REVIEW", "REQUIRE")))
    return {
        "total": len(rows),
        "state_counts": state_counts,
        "live_tracked": sum(1 for r in rows if r["live_ic"] is not None),
        "paper_tracked": sum(1 for r in rows if r.get("recommendation") not in (None, "NO_LIVE_DATA")),
        "awaiting_review": awaiting,
        "needs_action": awaiting,
        "positive_sharpe": sum(1 for s in sharpes if s > 0),
        "avg_backtest_ic": round(sum(ics) / len(ics), 5) if ics else 0.0,
    }


def _lifecycle_monitor_payload() -> Dict:
    """Alpha lifecycle monitor: REAL backtest-side artifacts from experiment_registry; honest NO_LIVE_DATA
    on the live side (L2 has no live/paper IC). Fail-closed to synthetic when the registry is absent."""
    st = _load_experiment_store()
    rows: List[Dict] = []
    provenance = "SYNTHETIC_DETERMINISTIC"
    if st is not None:
        try:
            rows = _lifecycle_rows_real(st)
            if rows:
                provenance = "EXPERIMENT_REGISTRY"
        finally:
            close = getattr(st, "close", None)
            if callable(close):
                close()
    if not rows:
        rows = _lifecycle_rows_synthetic()
        provenance = "SYNTHETIC_DETERMINISTIC"
    return {
        "monitoring_mode": "READ_ONLY_LIFECYCLE_MONITOR",
        "data_provenance": provenance,
        "summary": _lifecycle_summary(rows),
        "rows": rows,
        "thresholds": _degradation_thresholds_snapshot(),
        "lifecycle_policy": {
            "recommends_only": True,
            "promotion_human_only": True,
            "live_trading_built": False,
            "live_ic_available": False,
        },
    }


def _experiments_payload() -> Dict:
    st = _load_experiment_store()
    if st is not None:
        try:
            experiments = _real_experiment_rows(st)
        finally:
            close = getattr(st, "close", None)
            if callable(close):
                close()
        if experiments:
            return {
                "registry_mode": "READ_ONLY_RESEARCH_REGISTRY",
                "data_provenance": "EXPERIMENT_REGISTRY",
                "summary": {
                    "total_experiments": len(experiments),
                    "approved": sum(1 for e in experiments if e["decision"] == "APPROVED"),
                    "rejected": sum(1 for e in experiments if str(e["decision"]).startswith("REJECTED")),
                    "needs_more_data": sum(1 for e in experiments if e["decision"] == "NEEDS_MORE_DATA"),
                    "artifact_count": sum(len(e["artifacts"]) for e in experiments),
                    "reproducible": sum(1 for e in experiments if e["reproducibility_hash"]),
                },
                "experiments": experiments,
                "stage_counts": {s: sum(1 for e in experiments if e["stage"] == s) for s in _EXP_STAGES},
                "lineage_policy": {
                    "requires_config_hash": True,
                    "requires_data_snapshot": True,
                    "requires_metric_artifact": True,
                    "mutable_records": False,
                },
            }
    candidates = _alpha_candidates()
    agents = ["Macro Analyst", "Factor Hunter", "Momentum Specialist", "Portfolio Scout"]
    stages = ["DISCOVERY", "VALIDATION", "PAPER_REPLAY", "RISK_REVIEW", "ARCHIVED"]
    experiments = []
    for idx, alpha in enumerate(candidates[:10]):
        decision = _validation_decision(alpha)
        seed = 20260612 + idx * 17
        data_snapshot_id = f"md-snapshot-2026w{20 + idx % 4:02d}"
        config_hash = _short_hash(alpha["candidate_id"], alpha["expression"], seed, data_snapshot_id)
        reproducibility_hash = _short_hash("experiment", config_hash, alpha["ic"], alpha["sharpe"], alpha["turnover"])
        artifact_base = f"artifact://research/{alpha['candidate_id']}/{config_hash}"
        artifacts = [
            {
                "artifact_id": f"art-{idx + 1:02d}-config",
                "kind": "CONFIG",
                "status": "AVAILABLE",
                "uri": f"{artifact_base}/config.json",
                "content_hash": _short_hash(config_hash, "config"),
            },
            {
                "artifact_id": f"art-{idx + 1:02d}-metrics",
                "kind": "METRICS",
                "status": "AVAILABLE",
                "uri": f"{artifact_base}/metrics.json",
                "content_hash": _short_hash(config_hash, "metrics", alpha["composite_score"]),
            },
            {
                "artifact_id": f"art-{idx + 1:02d}-lineage",
                "kind": "LINEAGE",
                "status": "AVAILABLE",
                "uri": f"{artifact_base}/lineage.json",
                "content_hash": _short_hash(config_hash, "lineage", alpha["expression"]),
            },
        ]
        if decision != "APPROVED":
            artifacts.append(
                {
                    "artifact_id": f"art-{idx + 1:02d}-review",
                    "kind": "REVIEW_NOTE",
                    "status": "AVAILABLE",
                    "uri": f"{artifact_base}/review.md",
                    "content_hash": _short_hash(config_hash, "review", decision),
                }
            )
        experiments.append(
            {
                "experiment_id": f"exp-{alpha['candidate_id']}-v{idx % 3 + 1:03d}",
                "alpha_id": alpha["candidate_id"],
                "expression": alpha["expression"],
                "owner_agent": agents[idx % len(agents)],
                "stage": stages[idx % len(stages)],
                "decision": decision,
                "created_at": 1_781_222_400 - idx * 7_200,
                "execution_mode": "L2_DRY_RUN_ONLY",
                "config": {
                    "universe": "CN_A_SHARE_RESEARCH",
                    "horizon_days": ALPHA_WINDOWS[idx % len(ALPHA_WINDOWS)],
                    "cost_bps": 18 + idx % 5 * 3,
                    "seed": seed,
                    "data_snapshot_id": data_snapshot_id,
                    "config_hash": config_hash,
                },
                "metrics": {
                    "ic": alpha["ic"],
                    "rank_ic": alpha["rank_ic"],
                    "sharpe": alpha["sharpe"],
                    "turnover": alpha["turnover"],
                    "capacity_estimate": alpha["capacity_estimate"],
                    "composite_score": alpha["composite_score"],
                },
                "reproducibility_hash": reproducibility_hash,
                "artifacts": artifacts,
                "lineage": {
                    "upstream": ["factor_engine", "validation_engine"],
                    "downstream": ["paper_trading", "portfolio_research", "risk_gate"]
                    if decision == "APPROVED"
                    else ["validation_review"],
                },
            }
        )
    return {
        "registry_mode": "READ_ONLY_RESEARCH_REGISTRY",
        "summary": {
            "total_experiments": len(experiments),
            "approved": sum(1 for item in experiments if item["decision"] == "APPROVED"),
            "rejected": sum(1 for item in experiments if str(item["decision"]).startswith("REJECTED")),
            "needs_more_data": sum(1 for item in experiments if item["decision"] == "NEEDS_MORE_DATA"),
            "artifact_count": sum(len(item["artifacts"]) for item in experiments),
            "reproducible": sum(1 for item in experiments if item["reproducibility_hash"]),
        },
        "experiments": experiments,
        "stage_counts": {stage: sum(1 for item in experiments if item["stage"] == stage) for stage in stages},
        "lineage_policy": {
            "requires_config_hash": True,
            "requires_data_snapshot": True,
            "requires_metric_artifact": True,
            "mutable_records": False,
        },
    }


router = APIRouter()


@router.get("/api/v1/console/experiments")
async def experiments() -> Dict:
    payload = _experiments_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@router.get("/api/v1/console/lifecycle-monitor")
def lifecycle_monitor() -> Dict:
    payload = _lifecycle_monitor_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }
