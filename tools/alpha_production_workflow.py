"""Reconcile all L2 Alpha stages into one append-only production workflow.

The tool reads existing immutable/attested research and paper artifacts.  It
does not recompute a verdict and cannot promote lifecycle state, access a
credential, connect to a broker, or submit an order.  Missing evidence remains
an explicit blocker; malformed existing evidence fails the reconciliation.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import math
import os
from pathlib import Path
import time
from typing import Any

from quantos.contracts.hashing import stable_hash
from services.research import alpha_expression as ax
from services.research.alpha_production_workflow import (
    AlphaProductionWorkflow,
    CandidateDefinitionKind,
    CandidateIdentity,
    EvidenceGate,
    EvidenceStatus,
    GateEvidence,
    MaterialChangeClass,
    ProductionStage,
    derive_stage,
)
from services.research.alpha_family_registry import AlphaFamilyRegistry
from services.research.paper_candidate_registry import PaperCandidateRegistry
from services.research.paper_trading.attested_store import AttestedForwardLedger
from services.research.portfolio_evidence import (
    PORTFOLIO_GATE_TOLERATED_MISSING,
    PortfolioGatePolicy,
    evaluate_portfolio_gate,
)
from services.research.ports.persistence import ForwardLedgerError
from services.research.unblinding import (
    RealizationWindow,
    UnblindingPolicy,
    evaluate_unblinding,
    window_regime_labels,
)
from services.research.realtime_alpha_handoff import (
    build_realtime_candidate_identities,
)
from tools.cache_io import atomic_write_json


DEFAULT_DB = Path("data/cache/alpha_production_workflow.db")
DEFAULT_OUTPUT = Path("data/cache/alpha_production_workflow.json")
DEFAULT_CANDIDATE_REGISTRY = Path("data/cache/paper_candidate_registry.db")
DEFAULT_HISTORICAL_EVALUATIONS = Path("evidence/paper_alpha_evaluations.json")
DEFAULT_FORWARD_EVALUATIONS = Path("data/cache/forward_alpha_evaluations.json")
DEFAULT_FORWARD_STATUS = Path("data/cache/forward_track_status.json")
DEFAULT_RISK_BUNDLE = Path("data/cache/paper_research_risk_inputs.json")
DEFAULT_PORTFOLIO_EVIDENCE = Path("data/cache/candidate_portfolio_evidence.json")
DEFAULT_PAPER_REALTIME = Path("data/cache/paper_realtime_status.json")
# The production workflow needs a per-candidate lifecycle row.  The older
# ``alpha_lifecycle_status.json`` is a global readiness/audit artifact and has
# no candidate map, so consuming it here silently made every lifecycle gate
# ``NOT_AVAILABLE``.  The cockpit is the read-only per-alpha evidence source;
# its scheduled refresh remains independent from this reconciliation worker.
DEFAULT_LIFECYCLE = Path("evidence/alpha_lifecycle_cockpit.json")
DEFAULT_PAPER_TRACK = Path("data/cache/paper_track.db")
DEFAULT_REGIME_MARKS = Path("data/cache/crypto_perp_mark_prices.json")
DEFAULT_REGIME_FUNDING = Path("data/cache/crypto_funding_rates.json")
DEFAULT_REALTIME_ALPHA_REGISTRY = Path("data/cache/realtime_alpha_registry.db")
DEFAULT_REALTIME_ALPHA_DISCOVERY = Path("data/cache/realtime_alpha_discovery.json")
# The engineering shadow remains diagnostic-only.  Formal realtime shadow
# evidence is produced by a physically separate authority and is consumed by
# this workflow through this distinct path.
DEFAULT_REALTIME_ALPHA_FORMAL_SHADOW = Path(
    "data/cache/realtime_alpha_formal_shadow.json"
)
DEFAULT_REALTIME_ALPHA_SHADOW = Path("data/cache/realtime_alpha_forward_shadow.json")
DEFAULT_REALTIME_ALPHA_EXECUTION = Path("data/cache/realtime_alpha_execution_paper.json")
DEFAULT_UNBLINDING_POLICY = UnblindingPolicy()
REQUIRED_FORWARD_PERIODS = 8
LEGACY_DIAGNOSTIC_IDS = frozenset(
    {"carry_oi_majors", "crowd_contra", "top_contra"}
)
# WP4 W4-2/W4-3:portfolio evidence 的容忍缺失集(唯一真源在纯核
# services/research/portfolio_evidence.PORTFOLIO_GATE_TOLERATED_MISSING,此处仅
# 再导出保持既有 import 兼容)。book 级多场所 SOR Paper TCA 是 canary 引用
# (非 per 候选实测),盘口深度历史缺口本切片显式容忍 MISSING;capacity/crowding/
# marginal_contribution/common_exposure 必须在场,缺一即
# PORTFOLIO_EVIDENCE_INCOMPLETE;W4-3 起完备行再过 portfolio_gate_policy.v1
# merit 判定(未过阈 → PORTFOLIO_GATE_*)。
PORTFOLIO_EVIDENCE_TOLERATED_MISSING = PORTFOLIO_GATE_TOLERATED_MISSING
# WP4 W4-3:组合门政策与容量比参照 AUM。$1M 与九引擎包生成端
# tools/paper_research_risk_bundle.DEFAULT_PAPER_AUM_USD 及
# tools/paper_order_preview.DEFAULT_AUM 同口径——同一纸面 book 规模,容量比
# capacity.low / reference_aum 才与「部署 ≤ 安全容量/3」(W7 §11.3)先例可比。
DEFAULT_PORTFOLIO_GATE_POLICY = PortfolioGatePolicy()
DEFAULT_PAPER_AUM = 1_000_000.0


def _read_optional(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    if not resolved.is_file():
        return {}
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"workflow evidence is unreadable: {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"workflow evidence is not an object: {resolved}")
    return value


def _read_soft(path: str | Path) -> dict[str, Any]:
    """Regime 证据读取器:缺失**或不可读** → {}(缺席 → SEALED,绝不因此崩溃)。

    与 `_read_optional` 的 fail-closed 不同:regime 序列是揭盲判定的输入,
    缺席的诚实后果是 MATURE 更晚到来(REGIME_EVIDENCE_MISSING),不是调和失败。
    """
    try:
        return _read_optional(path)
    except RuntimeError:
        return {}


def _series_from_document(document: Mapping[str, Any], symbol: str) -> dict[int, float]:
    """从本地行情文档提取 {ts_ms: value}(仅数值、有限、非负 ts,按 ts 升序)。

    marks 文档是 `{_meta, data: {symbol: {...}}}`,funding 文档是 `{symbol: {...}}`。
    """
    container = document.get("data")
    if not isinstance(container, Mapping):
        container = document
    series = container.get(symbol)
    points: dict[int, float] = {}
    if isinstance(series, Mapping):
        for ts, value in series.items():
            try:
                stamp = int(ts)
                point = float(value)
            except (TypeError, ValueError):
                continue
            if stamp < 0 or not math.isfinite(point):
                continue
            points[stamp] = point
    return dict(sorted(points.items()))


def _realization_windows(
    candidate_id: str,
    *,
    paper_track_path: str | Path | None,
) -> tuple[RealizationWindow, ...]:
    """attested 兑现窗口(decided_ts, as_of_ts);库缺席 → 空(诚实 SEALED),
    哈希链损坏 → fail-closed RuntimeError(与 registry 同款,绝不带伤继续)。"""
    if paper_track_path is None:
        return ()
    resolved = Path(paper_track_path)
    if not resolved.is_file():
        return ()
    try:
        ledger = AttestedForwardLedger(str(resolved), create=False)
        try:
            rows = ledger.attested_realizations(candidate_id)
        finally:
            ledger.close()
    except ForwardLedgerError as exc:
        raise RuntimeError(
            f"attested forward ledger failed closed: {resolved}: {exc}"
        ) from exc
    return tuple(
        RealizationWindow(
            decided_ts=int(row["decided_ts"]), as_of_ts=int(row["as_of_ts"])
        )
        for row in rows
    )


def _timestamp_ms(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = int(value)
    if result < 0:
        return None
    if result < 10_000_000_000:
        result *= 1000
    return result


def _document_timestamp(document: Mapping[str, Any]) -> int | None:
    for name in (
        "generated_at_ms",
        "generated_at",
        "ran_at",
        "observed_at",
        "timestamp",
    ):
        result = _timestamp_ms(document.get(name))
        if result is not None:
            return result
    return None


def _artifact_hash(document: Mapping[str, Any], row: object) -> str:
    return stable_hash(
        {
            "schema_version": document.get("schema_version"),
            "dataset_version": document.get("dataset_version"),
            "config_hash": document.get("config_hash"),
            "generated_at": document.get("generated_at"),
            "ran_at": document.get("ran_at"),
            "row": row,
        }
    )


def _gate(
    gate: EvidenceGate,
    status: EvidenceStatus,
    *,
    path: str | Path,
    document: Mapping[str, Any],
    row: object,
) -> GateEvidence:
    present = bool(document)
    return GateEvidence(
        gate=gate,
        status=status,
        reference=str(path).replace("\\", "/") if present else None,
        content_hash=_artifact_hash(document, row) if present else None,
        observed_at_ms=_document_timestamp(document) if present else None,
    )


def _field_ids(expression: object) -> tuple[str, ...]:
    fields: set[str] = set()

    def visit(node: object) -> None:
        if isinstance(node, (tuple, list)):
            if (
                len(node) == 2
                and node[0] == "field"
                and isinstance(node[1], str)
            ):
                fields.add(node[1])
            for child in node[1:]:
                visit(child)

    visit(expression)
    return tuple(sorted(fields)) or ("expression_only",)


def _mechanism(alpha_id: str) -> tuple[str, str | None, tuple[str, ...]]:
    if alpha_id.startswith("carry"):
        return (
            "CARRY",
            "Cross-sectional funding and positioning dispersion should earn a net carry spread.",
            (
                "Held-out net carry spread is non-positive after fees and funding.",
                "The edge fails across the pre-registered stress and regime partitions.",
            ),
        )
    if alpha_id.startswith("crowd"):
        return (
            "CROWDING_REVERSAL",
            "Crowded positioning should mean-revert cross-sectionally after realistic costs.",
            (
                "Held-out net long-short spread is non-positive.",
                "Forward rank IC and risk-adjusted return fail the pre-registered gates.",
            ),
        )
    if alpha_id.startswith("top_contra"):
        return (
            "TOP_TRADER_CROWDING_REVERSAL",
            "Crowded top-trader positioning should mean-revert cross-sectionally.",
            (
                "Held-out net long-short spread is non-positive.",
                "The signal does not survive independent forward evidence.",
            ),
        )
    return (f"UNDECLARED:{alpha_id}", None, ())


def build_candidate_identities(
    *,
    candidate_registry_path: str | Path = DEFAULT_CANDIDATE_REGISTRY,
    realtime_registry_path: str | Path = DEFAULT_REALTIME_ALPHA_REGISTRY,
    realtime_discovery_path: str | Path = DEFAULT_REALTIME_ALPHA_DISCOVERY,
) -> tuple[CandidateIdentity, ...]:
    """Build the bootstrap population from static preregistration + verified admission."""
    from tools.forward_track_advance import _ALPHAS, _alpha_spec

    registered: dict[str, object] = dict(_ALPHAS)
    dynamic_ids: list[str] = []
    dynamic: dict[str, Any] = {}
    registry_path = Path(candidate_registry_path)
    if registry_path.is_file():
        with PaperCandidateRegistry(registry_path, create=False) as registry:
            if not registry.chain_valid():
                raise RuntimeError("paper candidate registry hash chain is invalid")
            dynamic = {item.alpha_id: item for item in registry.all()}
    for alpha_id, admitted in dynamic.items():
        existing = registered.get(alpha_id)
        if existing is not None:
            if ax.serialize(_alpha_spec(existing)["expr"]) != admitted.expression:
                raise RuntimeError(
                    f"paper candidate identity conflicts with static alpha: {alpha_id}"
                )
            continue
        registered[alpha_id] = {
            "expr": ax.deserialize(admitted.expression),
            "quantile": admitted.quantile,
            "cadence_days": admitted.cadence_days,
        }
        dynamic_ids.append(alpha_id)
    identities: list[CandidateIdentity] = []
    for alpha_id, entry in registered.items():
        spec = _alpha_spec(entry)
        expression_object = spec["expr"]
        expression = ax.serialize(expression_object)
        admitted = dynamic.get(alpha_id)
        family_id, hypothesis, falsification = _mechanism(str(alpha_id))
        source_payload = {
            "candidate_id": str(alpha_id),
            "expression": expression,
            "quantile": float(spec["quantile"]),
            "cadence_days": int(spec["cadence_days"]),
            "source": "tools.forward_track_advance._ALPHAS",
        }
        identities.append(
            CandidateIdentity(
                candidate_id=str(alpha_id),
                expression=expression,
                quantile=float(spec["quantile"]),
                cadence_days=int(spec["cadence_days"]),
                universe="cs_majors",
                family_id=family_id,
                source_kind=(
                    "VERIFIED_FACTORY_PASS"
                    if alpha_id in dynamic_ids
                    else "STATIC_PREREGISTERED"
                ),
                source_ref=(
                    f"experiment:{admitted.evaluation_run_id}"
                    if admitted is not None
                    else "tools.forward_track_advance._ALPHAS"
                ),
                source_definition_hash=(
                    admitted.definition_hash
                    if admitted is not None
                    else stable_hash(source_payload)
                ),
                required_datasets=_field_ids(expression_object),
                economic_hypothesis=hypothesis,
                falsification_criteria=falsification,
                created_at_ms=(admitted.admitted_at_ms if admitted is not None else 0),
                predecessor_id=None,
                change_class=MaterialChangeClass.NEW_CANDIDATE,
            )
        )
    realtime_registry = Path(realtime_registry_path)
    realtime_discovery = Path(realtime_discovery_path)
    if realtime_registry.is_file() != realtime_discovery.is_file():
        raise RuntimeError(
            "realtime Alpha handoff requires both registry and discovery evidence"
        )
    if realtime_registry.is_file():
        family_registry = AlphaFamilyRegistry(str(realtime_registry), create=False)
        try:
            if not family_registry.verify_chain():
                raise RuntimeError("realtime Alpha family registry chain is invalid")
            realtime_identities = build_realtime_candidate_identities(
                family_registry.candidate_definitions(),
                _read_optional(realtime_discovery),
            )
        finally:
            family_registry.close()
        existing_ids = {item.candidate_id for item in identities}
        collisions = sorted(
            item.candidate_id
            for item in realtime_identities
            if item.candidate_id in existing_ids
        )
        if collisions:
            raise RuntimeError(
                "realtime Alpha ids conflict with canonical candidates: "
                + ",".join(collisions)
            )
        identities.extend(realtime_identities)
    return tuple(identities)


def _mapping_row(document: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    rows = document.get("alphas")
    if isinstance(rows, Mapping):
        row = rows.get(candidate_id)
        return dict(row) if isinstance(row, Mapping) else {}
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
        matches = [
            row
            for row in rows
            if isinstance(row, Mapping) and row.get("alpha_id") == candidate_id
        ]
        if len(matches) > 1:
            raise RuntimeError(f"workflow evidence duplicates candidate: {candidate_id}")
        return dict(matches[0]) if matches else {}
    return {}


def _sequence_row(
    document: Mapping[str, Any], candidate_id: str, *, field: str
) -> dict[str, Any]:
    rows = document.get(field)
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        return {}
    matches = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping) and row.get("candidate_id") == candidate_id
    ]
    if len(matches) > 1:
        raise RuntimeError(f"workflow evidence duplicates candidate: {candidate_id}")
    return matches[0] if matches else {}


def _realtime_historical_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
) -> GateEvidence:
    row = _sequence_row(document, candidate_id, field="candidates")
    admission = document.get("data_admission")
    formal_allowed = bool(
        isinstance(admission, Mapping)
        and admission.get("formal_validation_allowed") is True
    )
    if not row:
        status = EvidenceStatus.NOT_AVAILABLE
    elif not formal_allowed or row.get("evaluation_status") != "EVALUATED":
        status = EvidenceStatus.PENDING
    elif row.get("gate_pass") is True and row.get("verdict") == "PASS":
        status = EvidenceStatus.PASS
    else:
        status = EvidenceStatus.FAIL
    bound_row = {
        "candidate": row,
        "formal_validation_allowed": formal_allowed,
        "research_policy_hash": document.get("research_policy_hash"),
    }
    return _gate(
        EvidenceGate.HISTORICAL_OOS,
        status,
        path=path,
        document=document if row else {},
        row=bound_row,
    )


def _track_row(document: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    tracks = document.get("tracks")
    row = tracks.get(candidate_id) if isinstance(tracks, Mapping) else None
    return dict(row) if isinstance(row, Mapping) else {}


def _realtime_engineering_shadow_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
) -> tuple[GateEvidence, int | None]:
    row = _track_row(document, candidate_id)
    periods = row.get("n_realizations")
    observed = int(periods) if type(periods) is int and periods >= 0 else None
    # This stream is intentionally engineering shadow only.  It proves causal
    # data/label mechanics but can never satisfy the blinded formal shadow gate.
    status = EvidenceStatus.RUNNING if row else EvidenceStatus.NOT_AVAILABLE
    return (
        _gate(
            EvidenceGate.SIGNAL_SHADOW,
            status,
            path=path,
            document=document if row else {},
            row={
                "track": row,
                "evidence_class": "ENGINEERING_SHADOW_NON_FORMAL",
            },
        ),
        observed,
    )


def _realtime_formal_shadow_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
) -> tuple[GateEvidence, int | None]:
    """Validate the independent formal shadow authority without unblinding it.

    The authority is intentionally narrow: it proves that an immutable,
    separate forward ledger is running and that its outcomes are not exposed
    to the adaptive selector.  It does not turn an exploratory result into a
    pass.  Maturity additionally requires the candidate's frozen historical
    validation to have passed and the required forward observations to exist.
    """
    authority = document.get("authority")
    safety = document.get("safety")
    verdict = document.get("verdict")
    ledger = document.get("ledger")
    authority_valid = (
        document.get("mode") == "AUTONOMOUS_REALTIME_FORMAL_BLINDED_SHADOW"
        and isinstance(authority, Mapping)
        and authority.get("name") == "FORMAL_BLINDED_SHADOW"
        and authority.get("formal_blinded") is True
        and authority.get("selector_feedback_consumed") is False
        and authority.get("outcome_feedback_feed") == "NONE"
        and authority.get("ledger_separate_from_engineering") is True
        and isinstance(safety, Mapping)
        and safety.get("formal_blinded") is True
        and safety.get("selector_feedback_consumed") is False
        and safety.get("outcome_feedback_feed") == "NONE"
        and isinstance(verdict, Mapping)
        and verdict.get("promotion_eligible") is False
        and verdict.get("live_allowed") is False
        and verdict.get("order_submission_allowed") is False
        and isinstance(ledger, Mapping)
        and ledger.get("chain_valid") is True
    )
    row = _track_row(document, candidate_id)
    periods = row.get("n_realizations")
    observed = int(periods) if type(periods) is int and periods >= 0 else None
    if not authority_valid or not row:
        status = EvidenceStatus.NOT_AVAILABLE
        bound_row: dict[str, Any] = {}
    elif row.get("errors"):
        status = EvidenceStatus.BLOCKED
        bound_row = {"track": row, "authority_valid": True}
    elif row.get("formal_maturity") is True:
        status = EvidenceStatus.MATURE
        bound_row = {"track": row, "authority_valid": True}
    else:
        status = EvidenceStatus.RUNNING
        bound_row = {"track": row, "authority_valid": True}
    return (
        _gate(
            EvidenceGate.SIGNAL_SHADOW,
            status,
            path=path,
            document=document if authority_valid and row else {},
            row=bound_row,
        ),
        observed,
    )


def _realtime_formal_shadow_required(
    candidate_id: str,
    *,
    discovery_document: Mapping[str, Any],
) -> bool:
    """Require formal shadow only after discovery admits a candidate to it.

    Exploratory rows are intentionally visible before the formal shadow
    authority admits them.  Treating that normal pre-admission state as a
    missing-artifact failure makes the lifecycle report claim a broken
    pipeline for candidates that have not yet passed the three-day/evaluation
    handoff.  ``gate_pass`` is the discovery handoff's explicit boundary:
    once true, a missing formal track is a real production defect; before
    that, ``SIGNAL_SHADOW_NOT_MATURE`` is the honest blocker.
    """
    rows = discovery_document.get("candidates")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if isinstance(row, Mapping) and row.get("candidate_id") == candidate_id:
            return (
                row.get("evaluation_status") == "EVALUATED"
                and row.get("gate_pass") is True
            )
    return False


def _realtime_engineering_execution_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
) -> GateEvidence:
    row = _track_row(document, candidate_id)
    status = EvidenceStatus.RUNNING if row else EvidenceStatus.NOT_AVAILABLE
    return _gate(
        EvidenceGate.EXECUTION_PAPER,
        status,
        path=path,
        document=document if row else {},
        row={
            "track": row,
            "evidence_class": "ENGINEERING_EXECUTION_PAPER_NON_FORMAL",
        },
    )


def _historical_gate(
    candidate_id: str,
    *,
    historical_document: Mapping[str, Any],
    historical_path: str | Path,
    forward_document: Mapping[str, Any],
    forward_path: str | Path,
) -> GateEvidence:
    row = _mapping_row(historical_document, candidate_id)
    document = historical_document
    path = historical_path
    if not row:
        row = _mapping_row(forward_document, candidate_id)
        document = forward_document
        path = forward_path
    if not row:
        return _gate(
            EvidenceGate.HISTORICAL_OOS,
            EvidenceStatus.NOT_AVAILABLE,
            path=path,
            document={},
            row={},
        )
    evaluation = row.get("evaluation")
    evaluation = evaluation if isinstance(evaluation, Mapping) else {}
    verified = row.get("status") == "VERIFIED" and evaluation.get("verified") is True
    if verified and evaluation.get("verdict") == "PASS":
        status = EvidenceStatus.PASS
    elif verified:
        status = EvidenceStatus.FAIL
    else:
        status = EvidenceStatus.PENDING
    return _gate(
        EvidenceGate.HISTORICAL_OOS,
        status,
        path=path,
        document=document,
        row=row,
    )


def _shadow_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
    policy: UnblindingPolicy,
    paper_track_path: str | Path | None,
    funding_by_ts: Mapping[int, float],
    prices_by_ts: Mapping[int, float],
) -> tuple[GateEvidence, int | None, dict[str, Any]]:
    """影子门 = 预注册揭盲纪律(WP3 W3-1):MATURE 要求三重条件全过——

    原始期数 ≥ min_periods ∧ 有效独立观察数 ≥ min_independent_periods ∧
    regime 覆盖双条件(类数 + 单类份额)。任一不满足 → RUNNING/BLOCKED,
    `unblinding` 子块(政策哈希 + 实测值 + 未满足项)并入 gate row 进 content
    hash,未满足项同时以 `UNBLINDING_*` blocker 落进持久 bundle。本门只会让
    MATURE 更晚到来,绝不更早;判定只消费计数与覆盖,绝不读收益/IC。
    """
    row = _mapping_row(document, candidate_id)
    if not row:
        return (
            _gate(
                EvidenceGate.SIGNAL_SHADOW,
                EvidenceStatus.NOT_AVAILABLE,
                path=path,
                document={},
                row={},
            ),
            None,
            {},
        )
    periods = row.get("n_forward_periods")
    observed = int(periods) if type(periods) is int and periods >= 0 else None
    windows = _realization_windows(candidate_id, paper_track_path=paper_track_path)
    labels = (
        window_regime_labels(
            windows, funding_by_ts=funding_by_ts, prices_by_ts=prices_by_ts
        )
        if windows
        else ()
    )
    detail = evaluate_unblinding(
        n_periods=observed if observed is not None else 0,
        windows=windows,
        regime_labels=labels,
        policy=policy,
    )
    row = dict(row)
    row["unblinding"] = detail
    if observed is not None and detail["status"] == "READY_TO_UNBLIND":
        status = EvidenceStatus.MATURE
    elif row.get("evidence_state") in {"FAILED", "BLOCKED"}:
        status = EvidenceStatus.BLOCKED
    else:
        status = EvidenceStatus.RUNNING
    return (
        _gate(
            EvidenceGate.SIGNAL_SHADOW,
            status,
            path=path,
            document=document,
            row=row,
        ),
        observed,
        detail,
    )


def _portfolio_missing(row: Mapping[str, Any]) -> tuple[str, ...]:
    """portfolio evidence 行的非容忍 missing 清单;形状畸形 → fail-closed
    RuntimeError(带伤证据绝不当完备放行)。"""
    missing = row.get("missing", [])
    if not isinstance(missing, list) or any(
        not isinstance(name, str) for name in missing
    ):
        raise RuntimeError("portfolio evidence row has a malformed 'missing' list")
    return tuple(
        sorted(set(missing) - PORTFOLIO_EVIDENCE_TOLERATED_MISSING)
    )


def _risk_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
    portfolio_document: Mapping[str, Any] | None = None,
    portfolio_policy: PortfolioGatePolicy,
    portfolio_reference_aum: float,
) -> tuple[GateEvidence, list[str]]:
    """RESEARCH_RISK 门(WP4 W4-2/W4-3):READY = 九引擎包 READY ∧ portfolio
    evidence 行完备 ∧ 组合门 merit PASS(portfolio_gate_policy.v1)。

    缺 artifact/缺行/缺非容忍子项 → 状态降 PENDING +
    PORTFOLIO_EVIDENCE_INCOMPLETE(+逐子项 PORTFOLIO_EVIDENCE_MISSING:<sub>);
    完备而未过阈 → EvidenceStatus.FAIL + 逐失败码 PORTFOLIO_GATE_<CODE>
    (RESEARCH_RISK_NOT_READY 由调和器按非 READY 统一追加);判定 dict 并入
    gate row(portfolio_evidence + portfolio_gate)→ 进 content hash 绑定。
    缺席 → PENDING 绝不崩,畸形 → RuntimeError fail-closed(经 _portfolio_missing
    与 evaluate_portfolio_gate 双重校验)。"""
    row = _mapping_row(document, candidate_id)
    blockers: list[str] = []
    if not row:
        status = EvidenceStatus.NOT_AVAILABLE
    elif row.get("status") == "READY":
        status = EvidenceStatus.READY
    elif row.get("status") in {"FAIL", "FAILED", "BLOCKED"}:
        status = EvidenceStatus.FAIL
    else:
        status = EvidenceStatus.PENDING
    if status is EvidenceStatus.READY:
        portfolio_row = _mapping_row(portfolio_document or {}, candidate_id)
        row = dict(row)
        row["portfolio_evidence"] = portfolio_row
        if not portfolio_row:
            status = EvidenceStatus.PENDING
            blockers.append("PORTFOLIO_EVIDENCE_INCOMPLETE")
        else:
            _portfolio_missing(portfolio_row)  # 畸形 missing 清单 → RuntimeError
            evaluation = evaluate_portfolio_gate(
                portfolio_row,
                portfolio_policy,
                reference_aum=portfolio_reference_aum,
            )
            row["portfolio_gate"] = evaluation
            if evaluation["status"] == "INCOMPLETE":
                status = EvidenceStatus.PENDING
                blockers.append("PORTFOLIO_EVIDENCE_INCOMPLETE")
                blockers.extend(
                    f"PORTFOLIO_EVIDENCE_MISSING:{name}"
                    for name in evaluation["missing"]
                )
            elif evaluation["status"] == "FAIL":
                status = EvidenceStatus.FAIL
                blockers.extend(
                    f"PORTFOLIO_GATE_{code}" for code in evaluation["failures"]
                )
    return (
        _gate(
            EvidenceGate.RESEARCH_RISK,
            status,
            path=path,
            document=document if row else {},
            row=row,
        ),
        blockers,
    )


def _execution_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
) -> tuple[GateEvidence, bool]:
    row = _mapping_row(document, candidate_id)
    marked = row.get("status") == "PAPER_MARKED"
    legacy = bool(marked and candidate_id in LEGACY_DIAGNOSTIC_IDS)
    if legacy:
        status = EvidenceStatus.LEGACY_DIAGNOSTIC
    elif marked:
        status = EvidenceStatus.PAPER_MARKED
    elif row:
        status = EvidenceStatus.PENDING
    else:
        status = EvidenceStatus.NOT_AVAILABLE
    return (
        _gate(
            EvidenceGate.EXECUTION_PAPER,
            status,
            path=path,
            document=document if row else {},
            row=row,
        ),
        legacy,
    )


def _lifecycle_gate(
    candidate_id: str,
    *,
    document: Mapping[str, Any],
    path: str | Path,
) -> GateEvidence:
    row = _mapping_row(document, candidate_id)
    states = " ".join(str(value) for value in row.values()).upper()
    if "RETIRED" in states:
        status = EvidenceStatus.RETIRED
    elif any(value in states for value in ("FAILED", "DEGRADED", "BLOCKED")):
        status = EvidenceStatus.BLOCKED
    elif row:
        status = EvidenceStatus.RUNNING
    else:
        status = EvidenceStatus.NOT_AVAILABLE
    return _gate(
        EvidenceGate.LIFECYCLE,
        status,
        path=path,
        document=document if row else {},
        row=row,
    )


def reconcile_workflow(
    *,
    workflow: AlphaProductionWorkflow,
    identities: Sequence[CandidateIdentity],
    historical_document: Mapping[str, Any],
    historical_path: str | Path,
    forward_evaluations_document: Mapping[str, Any],
    forward_evaluations_path: str | Path,
    forward_status_document: Mapping[str, Any],
    forward_status_path: str | Path,
    risk_document: Mapping[str, Any],
    risk_path: str | Path,
    realtime_document: Mapping[str, Any],
    realtime_path: str | Path,
    lifecycle_document: Mapping[str, Any],
    lifecycle_path: str | Path,
    release_commit: str | None,
    realtime_discovery_document: Mapping[str, Any] | None = None,
    realtime_discovery_path: str | Path = DEFAULT_REALTIME_ALPHA_DISCOVERY,
    realtime_formal_shadow_document: Mapping[str, Any] | None = None,
    realtime_formal_shadow_path: str | Path = DEFAULT_REALTIME_ALPHA_FORMAL_SHADOW,
    realtime_shadow_document: Mapping[str, Any] | None = None,
    realtime_shadow_path: str | Path = DEFAULT_REALTIME_ALPHA_SHADOW,
    realtime_execution_document: Mapping[str, Any] | None = None,
    realtime_execution_path: str | Path = DEFAULT_REALTIME_ALPHA_EXECUTION,
    paper_track_path: str | Path | None = None,
    marks_document: Mapping[str, Any] | None = None,
    funding_document: Mapping[str, Any] | None = None,
    unblinding_policy: UnblindingPolicy | None = None,
    portfolio_document: Mapping[str, Any] | None = None,
    portfolio_policy: PortfolioGatePolicy | None = None,
    portfolio_reference_aum: float | None = None,
) -> dict[str, Any]:
    inserted_candidates = 0
    inserted_events = 0
    policy = unblinding_policy or DEFAULT_UNBLINDING_POLICY
    gate_policy = portfolio_policy or DEFAULT_PORTFOLIO_GATE_POLICY
    gate_reference_aum = (
        DEFAULT_PAPER_AUM if portfolio_reference_aum is None else portfolio_reference_aum
    )
    funding_by_ts = _series_from_document(funding_document or {}, "BTC")
    prices_by_ts = _series_from_document(marks_document or {}, "BTC")
    source_documents = (
        historical_document,
        forward_evaluations_document,
        forward_status_document,
        risk_document,
        realtime_document,
        lifecycle_document,
        realtime_discovery_document or {},
        realtime_formal_shadow_document or {},
        realtime_shadow_document or {},
        realtime_execution_document or {},
    )
    source_times = [
        timestamp
        for document in source_documents
        if (timestamp := _document_timestamp(document)) is not None
    ]
    as_of_ms = max(source_times, default=0)
    for identity in identities:
        inserted_candidates += int(workflow.register_candidate(identity))
        realtime_identity = (
            identity.definition_kind is CandidateDefinitionKind.REALTIME_MECHANISM
        )
        if realtime_identity:
            historical = _realtime_historical_gate(
                identity.candidate_id,
                document=realtime_discovery_document or {},
                path=realtime_discovery_path,
            )
            shadow, periods = _realtime_formal_shadow_gate(
                identity.candidate_id,
                document=realtime_formal_shadow_document or {},
                path=realtime_formal_shadow_path,
            )
            unblinding: dict[str, Any] = {}
        else:
            historical = _historical_gate(
                identity.candidate_id,
                historical_document=historical_document,
                historical_path=historical_path,
                forward_document=forward_evaluations_document,
                forward_path=forward_evaluations_path,
            )
            shadow, periods, unblinding = _shadow_gate(
                identity.candidate_id,
                document=forward_status_document,
                path=forward_status_path,
                policy=policy,
                paper_track_path=paper_track_path,
                funding_by_ts=funding_by_ts,
                prices_by_ts=prices_by_ts,
            )
        risk, risk_blockers = _risk_gate(
            identity.candidate_id,
            document=risk_document,
            path=risk_path,
            portfolio_document=portfolio_document,
            portfolio_policy=gate_policy,
            portfolio_reference_aum=gate_reference_aum,
        )
        if realtime_identity:
            execution = _realtime_engineering_execution_gate(
                identity.candidate_id,
                document=realtime_execution_document or {},
                path=realtime_execution_path,
            )
            legacy = False
        else:
            execution, legacy = _execution_gate(
                identity.candidate_id,
                document=realtime_document,
                path=realtime_path,
            )
        lifecycle = _lifecycle_gate(
            identity.candidate_id,
            document=lifecycle_document,
            path=lifecycle_path,
        )
        blockers: list[str] = []
        if identity.economic_hypothesis is None:
            blockers.append("ECONOMIC_HYPOTHESIS_MISSING")
        if not identity.falsification_criteria:
            blockers.append("FALSIFICATION_CRITERIA_MISSING")
        if historical.status is not EvidenceStatus.PASS:
            blockers.append("HISTORICAL_OOS_NOT_PASS")
        if shadow.status is not EvidenceStatus.MATURE:
            blockers.append("SIGNAL_SHADOW_NOT_MATURE")
            if (
                realtime_identity
                and shadow.status is EvidenceStatus.NOT_AVAILABLE
                and _realtime_formal_shadow_required(
                    identity.candidate_id,
                    discovery_document=realtime_discovery_document or {},
                )
            ):
                blockers.append("FORMAL_BLINDED_SIGNAL_SHADOW_NOT_AVAILABLE")
            else:
                for item in unblinding.get("blockers", ()):
                    blockers.append(f"UNBLINDING_{item}")
        if risk.status is not EvidenceStatus.READY:
            blockers.append("RESEARCH_RISK_NOT_READY")
        blockers.extend(risk_blockers)
        if release_commit is None:
            blockers.append("RELEASE_IDENTITY_MISSING")
        if legacy:
            blockers.append("LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE")
        if realtime_identity and execution.status is EvidenceStatus.RUNNING:
            blockers.append("ENGINEERING_EXECUTION_PAPER_NOT_FORMAL_EVIDENCE")
        previous = workflow.latest_bundle(identity.candidate_id)
        bundle = derive_stage(
            gates=(historical, shadow, risk, execution, lifecycle),
            identity_hash=identity.identity_hash,
            candidate_id=identity.candidate_id,
            observed_forward_periods=periods,
            required_forward_periods=REQUIRED_FORWARD_PERIODS,
            blockers=tuple(sorted(set(blockers))),
            release_commit=release_commit,
            legacy_diagnostic=legacy,
            as_of_ms=as_of_ms,
            previous_stage=previous.stage if previous is not None else None,
        )
        inserted_events += int(workflow.record_evidence(bundle))
    snapshot = workflow.snapshot()
    realtime_handoff_count = sum(
        identity.definition_kind is CandidateDefinitionKind.REALTIME_MECHANISM
        for identity in identities
    )
    snapshot.update(
        {
            "generated_at_ms": int(time.time_ns() // 1_000_000),
            "reconciliation": {
                "inserted_candidates": inserted_candidates,
                "inserted_events": inserted_events,
                "source_as_of_ms": as_of_ms,
                "release_commit": release_commit,
                "unblinding_policy_hash": policy.content_hash(),
                "unblinding_policy_version": policy.policy_version,
                "portfolio_gate_policy_hash": gate_policy.content_hash(),
                "portfolio_gate_policy_version": gate_policy.policy_version,
                "portfolio_reference_aum": float(gate_reference_aum),
                "realtime_handoff_count": realtime_handoff_count,
                "realtime_research_policy_hash": (
                    (realtime_discovery_document or {}).get("research_policy_hash")
                ),
            },
            "source_hashes": {
                "historical_evaluations": stable_hash(historical_document),
                "forward_evaluations": stable_hash(forward_evaluations_document),
                "forward_status": stable_hash(forward_status_document),
                "research_risk": stable_hash(risk_document),
                "portfolio_evidence": stable_hash(portfolio_document or {}),
                "paper_realtime": stable_hash(realtime_document),
                "lifecycle": stable_hash(lifecycle_document),
                "regime_marks": stable_hash(marks_document or {}),
                "regime_funding": stable_hash(funding_document or {}),
                "realtime_alpha_discovery": stable_hash(
                    realtime_discovery_document or {}
                ),
                "realtime_alpha_formal_shadow": stable_hash(
                    realtime_formal_shadow_document or {}
                ),
                "realtime_alpha_shadow": stable_hash(realtime_shadow_document or {}),
                "realtime_alpha_execution": stable_hash(
                    realtime_execution_document or {}
                ),
            },
        }
    )
    return snapshot


def run(
    *,
    db_path: str | Path = DEFAULT_DB,
    output_path: str | Path = DEFAULT_OUTPUT,
    candidate_registry_path: str | Path = DEFAULT_CANDIDATE_REGISTRY,
    realtime_registry_path: str | Path = DEFAULT_REALTIME_ALPHA_REGISTRY,
    realtime_discovery_path: str | Path = DEFAULT_REALTIME_ALPHA_DISCOVERY,
    realtime_formal_shadow_path: str | Path = DEFAULT_REALTIME_ALPHA_FORMAL_SHADOW,
    realtime_shadow_path: str | Path = DEFAULT_REALTIME_ALPHA_SHADOW,
    realtime_execution_path: str | Path = DEFAULT_REALTIME_ALPHA_EXECUTION,
    historical_evaluations_path: str | Path = DEFAULT_HISTORICAL_EVALUATIONS,
    forward_evaluations_path: str | Path = DEFAULT_FORWARD_EVALUATIONS,
    forward_status_path: str | Path = DEFAULT_FORWARD_STATUS,
    risk_path: str | Path = DEFAULT_RISK_BUNDLE,
    portfolio_evidence_path: str | Path = DEFAULT_PORTFOLIO_EVIDENCE,
    realtime_path: str | Path = DEFAULT_PAPER_REALTIME,
    lifecycle_path: str | Path = DEFAULT_LIFECYCLE,
    paper_track_path: str | Path = DEFAULT_PAPER_TRACK,
    regime_marks_path: str | Path = DEFAULT_REGIME_MARKS,
    regime_funding_path: str | Path = DEFAULT_REGIME_FUNDING,
    unblinding_policy: UnblindingPolicy | None = None,
    portfolio_policy: PortfolioGatePolicy | None = None,
    portfolio_reference_aum: float | None = None,
    release_commit: str | None = None,
) -> dict[str, Any]:
    commit = release_commit or os.environ.get("QUANTOS_RELEASE_COMMIT")
    if commit is not None:
        commit = commit.strip().lower()
        if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
            raise RuntimeError("QUANTOS_RELEASE_COMMIT is invalid")
    identities = build_candidate_identities(
        candidate_registry_path=candidate_registry_path,
        realtime_registry_path=realtime_registry_path,
        realtime_discovery_path=realtime_discovery_path,
    )
    with AlphaProductionWorkflow(db_path) as workflow:
        snapshot = reconcile_workflow(
            workflow=workflow,
            identities=identities,
            historical_document=_read_optional(historical_evaluations_path),
            historical_path=historical_evaluations_path,
            forward_evaluations_document=_read_optional(forward_evaluations_path),
            forward_evaluations_path=forward_evaluations_path,
            forward_status_document=_read_optional(forward_status_path),
            forward_status_path=forward_status_path,
            risk_document=_read_optional(risk_path),
            risk_path=risk_path,
            portfolio_document=_read_optional(portfolio_evidence_path),
            realtime_document=_read_optional(realtime_path),
            realtime_path=realtime_path,
            lifecycle_document=_read_optional(lifecycle_path),
            lifecycle_path=lifecycle_path,
            release_commit=commit,
            realtime_discovery_document=_read_optional(realtime_discovery_path),
            realtime_discovery_path=realtime_discovery_path,
            realtime_formal_shadow_document=_read_optional(
                realtime_formal_shadow_path
            ),
            realtime_formal_shadow_path=realtime_formal_shadow_path,
            realtime_shadow_document=_read_optional(realtime_shadow_path),
            realtime_shadow_path=realtime_shadow_path,
            realtime_execution_document=_read_optional(realtime_execution_path),
            realtime_execution_path=realtime_execution_path,
            paper_track_path=paper_track_path,
            marks_document=_read_soft(regime_marks_path),
            funding_document=_read_soft(regime_funding_path),
            unblinding_policy=unblinding_policy,
            portfolio_policy=portfolio_policy,
            portfolio_reference_aum=portfolio_reference_aum,
        )
    atomic_write_json(str(output_path), snapshot, indent=2)
    return snapshot


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--candidate-registry", default=str(DEFAULT_CANDIDATE_REGISTRY))
    parser.add_argument("--realtime-registry", default=str(DEFAULT_REALTIME_ALPHA_REGISTRY))
    parser.add_argument("--realtime-discovery", default=str(DEFAULT_REALTIME_ALPHA_DISCOVERY))
    parser.add_argument(
        "--realtime-formal-shadow", default=str(DEFAULT_REALTIME_ALPHA_FORMAL_SHADOW)
    )
    parser.add_argument("--realtime-shadow", default=str(DEFAULT_REALTIME_ALPHA_SHADOW))
    parser.add_argument("--realtime-execution", default=str(DEFAULT_REALTIME_ALPHA_EXECUTION))
    parser.add_argument(
        "--historical-evaluations", default=str(DEFAULT_HISTORICAL_EVALUATIONS)
    )
    parser.add_argument("--forward-evaluations", default=str(DEFAULT_FORWARD_EVALUATIONS))
    parser.add_argument("--forward-status", default=str(DEFAULT_FORWARD_STATUS))
    parser.add_argument("--risk-bundle", default=str(DEFAULT_RISK_BUNDLE))
    parser.add_argument("--portfolio-evidence", default=str(DEFAULT_PORTFOLIO_EVIDENCE))
    parser.add_argument("--paper-realtime", default=str(DEFAULT_PAPER_REALTIME))
    parser.add_argument("--lifecycle", default=str(DEFAULT_LIFECYCLE))
    parser.add_argument("--paper-track", default=str(DEFAULT_PAPER_TRACK))
    parser.add_argument("--regime-marks", default=str(DEFAULT_REGIME_MARKS))
    parser.add_argument("--regime-funding", default=str(DEFAULT_REGIME_FUNDING))
    parser.add_argument("--release-commit", default=None)
    args = parser.parse_args(argv)
    try:
        snapshot = run(
            db_path=args.db,
            output_path=args.output,
            candidate_registry_path=args.candidate_registry,
            realtime_registry_path=args.realtime_registry,
            realtime_discovery_path=args.realtime_discovery,
            realtime_formal_shadow_path=args.realtime_formal_shadow,
            realtime_shadow_path=args.realtime_shadow,
            realtime_execution_path=args.realtime_execution,
            historical_evaluations_path=args.historical_evaluations,
            forward_evaluations_path=args.forward_evaluations,
            forward_status_path=args.forward_status,
            risk_path=args.risk_bundle,
            portfolio_evidence_path=args.portfolio_evidence,
            realtime_path=args.paper_realtime,
            lifecycle_path=args.lifecycle,
            paper_track_path=args.paper_track,
            regime_marks_path=args.regime_marks,
            regime_funding_path=args.regime_funding,
            release_commit=args.release_commit,
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"alpha production workflow blocked: {exc}")
        return 2
    print(
        "alpha production workflow: "
        f"status={snapshot['status']} population={snapshot['population_count']} "
        f"eligible={len(snapshot['execution_paper_eligible_ids'])} "
        f"execution={len(snapshot['execution_paper_ids'])}"
    )
    return 0 if snapshot["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_DB",
    "DEFAULT_OUTPUT",
    "DEFAULT_PAPER_AUM",
    "DEFAULT_PAPER_TRACK",
    "DEFAULT_PORTFOLIO_EVIDENCE",
    "DEFAULT_PORTFOLIO_GATE_POLICY",
    "DEFAULT_REGIME_FUNDING",
    "DEFAULT_REGIME_MARKS",
    "DEFAULT_REALTIME_ALPHA_DISCOVERY",
    "DEFAULT_REALTIME_ALPHA_EXECUTION",
    "DEFAULT_REALTIME_ALPHA_FORMAL_SHADOW",
    "DEFAULT_REALTIME_ALPHA_REGISTRY",
    "DEFAULT_REALTIME_ALPHA_SHADOW",
    "DEFAULT_UNBLINDING_POLICY",
    "LEGACY_DIAGNOSTIC_IDS",
    "PORTFOLIO_EVIDENCE_TOLERATED_MISSING",
    "REQUIRED_FORWARD_PERIODS",
    "build_candidate_identities",
    "reconcile_workflow",
    "run",
]
