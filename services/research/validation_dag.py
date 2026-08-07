"""Fail-closed validation DAG over explicit, versioned engine adapters."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
import json
import re
import threading
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from services.research.alpha_identity import alpha_id as canonical_alpha_id
from services.research.alpha_family import family_id as canonical_family_id
from services.research.trial_ledger import TrialLedger, TrialLedgerConflict, TrialRecord

_STAGE_ORDER = ("cheap", "oos", "realism")
_TERMINAL = {"PASS", "BLOCK"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RUN_LOCKS: dict[tuple[int, str], threading.RLock] = {}
_RUN_LOCK_GUARD = threading.Lock()


def _freeze(value: Any) -> Any:
    if is_dataclass(value):
        return MappingProxyType({f.name: _freeze(getattr(value, f.name)) for f in fields(value)})
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise TypeError("mapping keys must be strings")
        return MappingProxyType({k: _freeze(value[k]) for k in sorted(value)})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float) and value == value and abs(value) != float("inf"):
        return value
    raise TypeError(f"value is not canonical JSON: {type(value).__name__}")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise TypeError("mapping keys must be strings")
        return {k: _jsonable(value[k]) for k in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float) and value == value and abs(value) != float("inf"):
        return value
    raise TypeError(f"value is not canonical JSON: {type(value).__name__}")


def _digest(value: Any) -> str:
    encoded = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


@runtime_checkable
class ValidationStage(Protocol):
    name: str
    version: str
    implementation_id: str

    def evaluate(self, context: "ValidationContext") -> "StageEvidence": ...


@dataclass(frozen=True, slots=True)
class StageEvidence:
    stage: str
    version: str
    outcome: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    reason: str | None = None

    def __post_init__(self):
        if self.stage not in _STAGE_ORDER or not isinstance(self.version, str) or not self.version:
            raise ValueError("stage and version are required")
        if self.outcome not in _TERMINAL:
            raise ValueError("stage outcome must be PASS or BLOCK")
        frozen = _freeze(self.evidence)
        if not isinstance(frozen, Mapping) or not frozen:
            raise ValueError("stage evidence is required")
        object.__setattr__(self, "evidence", frozen)
        if self.outcome == "BLOCK" and not self.reason:
            object.__setattr__(self, "reason", "stage blocked")

    @property
    def evidence_hash(self) -> str:
        return _digest({"stage": self.stage, "version": self.version, "outcome": self.outcome, "evidence": self.evidence, "reason": self.reason})


@dataclass(frozen=True, slots=True)
class ValidationContext:
    candidate: Any
    panel: Any
    policy: "ValidationPolicy"
    trial_id: str
    panel_digest: str
    policy_digest: str


class EngineAdapter:
    """Thin adapter: identity is immutable and math remains in the engine."""
    __slots__ = ("name", "version", "implementation_id", "_provider", "_sealed")

    def __setattr__(self, key, value):
        if getattr(self, "_sealed", False) and key in {"name", "version", "implementation_id", "_provider"}:
            raise AttributeError("engine adapter identity is immutable")
        object.__setattr__(self, key, value)

    def __init__(self, name: str, version: str, implementation_id: str, provider: Callable[[ValidationContext], StageEvidence]):
        if name not in _STAGE_ORDER or not version or not implementation_id:
            raise ValueError("invalid engine adapter identity")
        self.name, self.version, self.implementation_id = name, version, implementation_id
        self._provider = provider
        self._sealed = True

    def evaluate(self, context: ValidationContext) -> StageEvidence:
        result = self._provider(context)
        if not isinstance(result, StageEvidence):
            raise TypeError("engine adapter returned non-evidence")
        if result.stage != self.name or result.version != self.version:
            raise ValueError("engine adapter result identity mismatch")
        return result


class BuiltinEngineAdapter(EngineAdapter):
    """Adapter for an existing engine function; it never reimplements its math."""

    __slots__ = ()

    def __init__(self, name: str, version: str, implementation_id: str, engine: Callable[..., Any], input_key: str):
        def provider(context):
            inputs = context.panel.get("engine_inputs", {}).get(input_key) if isinstance(context.panel, Mapping) else None
            if not isinstance(inputs, Mapping):
                return StageEvidence(name, version, "BLOCK", {"missing_input": input_key}, f"missing {input_key} engine inputs")
            try:
                result = engine(**dict(inputs))
            except (TypeError, ValueError, KeyError) as exc:
                return StageEvidence(name, version, "BLOCK", {"engine_error": type(exc).__name__}, f"{input_key} engine unavailable")
            if result is None or result is False:
                return StageEvidence(name, version, "BLOCK", {"engine_result": result}, f"{input_key} engine blocked")
            return StageEvidence(name, version, "PASS", {"engine_result": result})
        super().__init__(name, version, implementation_id, provider)


def production_adapters(*, leakage_engine: Callable[..., Any], oos_engine: Callable[..., Any], realism_engine: Callable[..., Any]) -> tuple[ValidationStage, ...]:
    """Build the explicit default boundary around already-tested engine functions."""
    return (
        BuiltinEngineAdapter("cheap", "1", "services.research.expression_leakage.compute_leakage_ok:1", leakage_engine, "leakage"),
        BuiltinEngineAdapter("oos", "1", "services.alpha.walk_forward.walk_forward_oos:1;services.validation.deflated_sharpe:1;services.alpha.multiple_testing:1", oos_engine, "oos"),
        BuiltinEngineAdapter("realism", "1", "quantos.contracts.cost;services.research.realism.capacity_curve;services.research.realism.tail_risk:1", realism_engine, "realism"),
    )


def _missing(name: str) -> EngineAdapter:
    def provider(context):
        return StageEvidence(name, "1", "BLOCK", {"missing_input": name}, f"missing {name} engine inputs")
    return EngineAdapter(name, "1", f"quantos.validation.{name}:1", provider)


_DEFAULT_ADAPTERS = tuple(_missing(name) for name in _STAGE_ORDER)


def _implementation_id(stage):
    value = getattr(stage, "implementation_id", None)
    return value if isinstance(value, str) and value else f"{type(stage).__module__}.{type(stage).__qualname__}"


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    stages: tuple[ValidationStage, ...] = field(default_factory=tuple)
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        stages = tuple(self.stages) or _DEFAULT_ADAPTERS
        if tuple(getattr(s, "name", None) for s in stages) != _STAGE_ORDER:
            raise ValueError("validation stages must be exactly cheap, oos, realism")
        for stage in stages:
            if not isinstance(getattr(stage, "version", None), str) or not stage.version:
                raise ValueError("every validation stage needs a version")
        object.__setattr__(self, "stages", stages)
        object.__setattr__(self, "settings", _freeze(self.settings))

    @property
    def digest(self):
        return _digest({"stages": [{"name": s.name, "version": s.version, "implementation_id": _implementation_id(s)} for s in self.stages], "settings": self.settings})


@dataclass(frozen=True, slots=True)
class ValidationArtifact:
    trial_id: str
    alpha_id: str
    panel_digest: str
    policy_digest: str
    stage_evidence_hashes: Mapping[str, str]
    effective_trial_count: int
    verdict: str
    reason: str | None = None

    def __post_init__(self):
        if not _HEX64.fullmatch(self.trial_id) or not _HEX64.fullmatch(self.alpha_id) or self.verdict not in _TERMINAL:
            raise ValueError("invalid validation artifact identity")
        object.__setattr__(self, "stage_evidence_hashes", _freeze(self.stage_evidence_hashes))
        if set(self.stage_evidence_hashes) - set(_STAGE_ORDER) or self.effective_trial_count < 0:
            raise ValueError("invalid validation artifact")


def _candidate_id(candidate, attr: str, fallback: str) -> str:
    value = getattr(candidate, attr, None)
    definition = getattr(candidate, "definition", None)
    if definition is not None:
        try:
            computed = canonical_alpha_id(definition) if attr == "alpha_id" else canonical_family_id(definition)
        except Exception as exc:
            raise ValueError(f"{attr} identity cannot be computed") from exc
        if value is not None and value != computed:
            raise ValueError(f"{attr} identity mismatch")
        return computed
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        return fallback
    return value


def _declared_policy_digest(candidate):
    declared = getattr(candidate, "validation_policy_digest", None)
    return declared


def _append(ledger, trial_id, alpha, family, policy_digest, status, index):
    try:
        ledger.append(TrialRecord(trial_id, alpha, family, alpha, policy_digest, status, index, 0))
    except TrialLedgerConflict:
        aggregate = ledger.load(trial_id)
        if aggregate.events[-1].status not in {status, "RUNNING", "PASSED", "FAILED"}:
            raise


def _exception_evidence(stage, version, exc):
    return StageEvidence(stage, version, "BLOCK", {"exception_type": type(exc).__name__, "exception_code": type(exc).__name__}, f"provider exception: {type(exc).__name__}")


def run_validation_dag(candidate: Any, panel: Any, policy: ValidationPolicy, ledger: TrialLedger) -> ValidationArtifact:
    if not isinstance(policy, ValidationPolicy):
        raise TypeError("policy must be ValidationPolicy")
    if not isinstance(ledger, TrialLedger):
        raise TypeError("ledger must be TrialLedger")
    declared = _declared_policy_digest(candidate)
    if declared is not None and declared != policy.digest:
        raise ValueError("candidate validation policy digest mismatch")
    alpha = _candidate_id(candidate, "alpha_id", _digest(candidate))
    family = _candidate_id(candidate, "family_id", alpha)
    panel_digest = _digest(panel)
    trial_id = _digest({"alpha_id": alpha, "panel_digest": panel_digest, "policy_digest": policy.digest})
    with _RUN_LOCK_GUARD:
        lock = _RUN_LOCKS.setdefault((id(ledger), trial_id), threading.RLock())
    with lock:
        try: _append(ledger, trial_id, alpha, family, policy.digest, "PLANNED", 0)
        except TrialLedgerConflict:
            aggregate = ledger.load(trial_id)
            if aggregate.status not in {"PLANNED", "RUNNING", "PASSED", "FAILED"}:
                raise
        aggregate = ledger.load(trial_id)
        if aggregate.status in {"PASSED", "FAILED"}:
            receipt = ledger.validation_receipt(trial_id).get("__artifact__")
            if receipt:
                payload = receipt["payload"]
                return ValidationArtifact(**payload)
        if aggregate.status == "PLANNED":
            _append(ledger, trial_id, alpha, family, policy.digest, "RUNNING", 1)
        context = ValidationContext(candidate, _freeze(panel), policy, trial_id, panel_digest, policy.digest)
        hashes = {}
        verdict, reason = "PASS", None
        for stage in policy.stages:
            claim = ledger.claim_validation_stage(trial_id, stage.name)
            if claim == "DONE":
                receipt = ledger.validation_receipt(trial_id)[stage.name]
                hashes[stage.name] = receipt["payload"]["evidence_hash"]
                continue
            if claim == "IN_PROGRESS":
                raise RuntimeError("validation stage is owned by another provider")
            try:
                evidence = stage.evaluate(context)
                if evidence.stage != stage.name or evidence.version != stage.version:
                    raise ValueError("stage/version identity mismatch")
                provider_detail = None
            except Exception as exc:
                provider_detail = f"provider exception: {type(exc).__name__}: {exc}"
                evidence = _exception_evidence(stage.name, stage.version, exc)
            hashes[stage.name] = evidence.evidence_hash
            ledger.complete_validation_stage(trial_id, stage.name, "DONE" if evidence.outcome == "PASS" else "BLOCKED", {"evidence_hash": evidence.evidence_hash, "stage": stage.name, "version": stage.version, "outcome": evidence.outcome, "evidence": evidence.evidence, "reason": evidence.reason})
            if evidence.outcome == "BLOCK":
                verdict, reason = "BLOCK", provider_detail or evidence.reason
                break
        status = "PASSED" if verdict == "PASS" else "FAILED"
        current = ledger.load(trial_id)
        if current.status == "RUNNING":
            _append(ledger, trial_id, alpha, family, policy.digest, status, 2)
        count = ledger.effective_trial_count(family).count
        artifact = ValidationArtifact(trial_id, alpha, panel_digest, policy.digest, hashes, count, verdict, reason)
        ledger.save_validation_artifact(trial_id, {"trial_id": artifact.trial_id, "alpha_id": artifact.alpha_id, "panel_digest": artifact.panel_digest, "policy_digest": artifact.policy_digest, "stage_evidence_hashes": dict(artifact.stage_evidence_hashes), "effective_trial_count": artifact.effective_trial_count, "verdict": artifact.verdict, "reason": artifact.reason})
        return artifact


__all__ = ["ValidationStage", "ValidationPolicy", "ValidationContext", "StageEvidence", "ValidationArtifact", "EngineAdapter", "BuiltinEngineAdapter", "production_adapters", "run_validation_dag"]
