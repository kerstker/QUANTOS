"""Deterministic, fail-closed validation DAG for alpha candidates.

The DAG is deliberately an orchestration boundary.  Mathematical validation
belongs to the existing validation/realism engines; callers provide thin stage
adapters through :class:`ValidationPolicy`.  A missing adapter result is
 evidence of failure, never an invitation to continue.
"""
from __future__ import annotations

from dataclasses import dataclass, field, is_dataclass, asdict
import hashlib
import json
from typing import Any, Mapping, Protocol, runtime_checkable
from weakref import WeakKeyDictionary

from services.research.alpha_identity import alpha_id as canonical_alpha_id
from services.research.alpha_family import family_id as canonical_family_id
from services.research.trial_ledger import TrialLedger, TrialRecord

_STAGE_ORDER = ("cheap", "oos", "realism")
_TERMINAL = {"PASS", "BLOCK"}
_ARTIFACT_CACHE: WeakKeyDictionary[TrialLedger, dict[str, "ValidationArtifact"]] = WeakKeyDictionary()


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _jsonable(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float) and value == value and abs(value) != float("inf"):
        return value
    raise TypeError(f"value is not canonical JSON: {type(value).__name__}")


def _digest(value: Any) -> str:
    encoded = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@runtime_checkable
class ValidationStage(Protocol):
    name: str
    version: str

    def evaluate(self, context: "ValidationContext") -> "StageEvidence": ...


@dataclass(frozen=True, slots=True)
class StageEvidence:
    stage: str
    version: str
    outcome: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.stage not in _STAGE_ORDER:
            raise ValueError("unknown validation stage")
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("stage version is required")
        if self.outcome not in _TERMINAL:
            raise ValueError("stage outcome must be PASS or BLOCK")
        if not isinstance(self.evidence, Mapping) or not self.evidence:
            raise ValueError("stage evidence is required")
        _jsonable(self.evidence)
        if self.outcome == "BLOCK" and not self.reason:
            object.__setattr__(self, "reason", "stage blocked")

    @property
    def evidence_hash(self) -> str:
        return _digest({"stage": self.stage, "version": self.version,
                        "outcome": self.outcome, "evidence": self.evidence,
                        "reason": self.reason})


@dataclass(frozen=True, slots=True)
class ValidationContext:
    candidate: Any
    panel: Any
    policy: "ValidationPolicy"
    trial_id: str
    panel_digest: str
    policy_digest: str


class _PanelEvidenceStage:
    """Default adapter: consume caller-produced evidence, never synthesize it."""

    def __init__(self, name: str):
        self.name = name
        self.version = "1"

    def evaluate(self, context: ValidationContext) -> StageEvidence:
        panel = context.panel
        raw = panel.get(self.name) if isinstance(panel, Mapping) else None
        if isinstance(raw, StageEvidence):
            return raw
        if not isinstance(raw, Mapping):
            return StageEvidence(self.name, self.version, "BLOCK",
                                 {"missing": self.name},
                                 f"missing {self.name} evidence")
        outcome = raw.get("outcome")
        evidence = raw.get("evidence")
        if outcome not in _TERMINAL or not isinstance(evidence, Mapping) or not evidence:
            return StageEvidence(self.name, self.version, "BLOCK",
                                 {"invalid": self.name},
                                 f"invalid {self.name} evidence")
        return StageEvidence(self.name, str(raw.get("version", self.version)), outcome,
                             evidence, raw.get("reason"))


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    stages: tuple[ValidationStage, ...] = field(default_factory=tuple)
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        stages = self.stages or tuple(_PanelEvidenceStage(name) for name in _STAGE_ORDER)
        stages = tuple(stages)
        if tuple(getattr(s, "name", None) for s in stages) != _STAGE_ORDER:
            raise ValueError("validation stages must be exactly cheap, oos, realism")
        if any(not isinstance(getattr(s, "version", None), str) or not s.version for s in stages):
            raise ValueError("every validation stage needs a version")
        _jsonable(self.settings)
        object.__setattr__(self, "stages", stages)

    @property
    def digest(self) -> str:
        return _digest({"stages": [{"name": s.name, "version": s.version} for s in self.stages],
                        "settings": self.settings})


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

    def __post_init__(self) -> None:
        if self.verdict not in _TERMINAL:
            raise ValueError("artifact verdict must be PASS or BLOCK")
        if set(self.stage_evidence_hashes) - set(_STAGE_ORDER):
            raise ValueError("unknown stage evidence")
        if self.effective_trial_count < 0:
            raise ValueError("effective trial count cannot be negative")


def _candidate_id(candidate: Any, attr: str, fallback: str) -> str:
    value = getattr(candidate, attr, None)
    if isinstance(value, str) and len(value) == 64:
        return value
    definition = getattr(candidate, "definition", None)
    if definition is not None:
        return canonical_alpha_id(definition) if attr == "alpha_id" else canonical_family_id(definition)
    return fallback


def _declared_policy_digest(candidate: Any) -> str | None:
    declared = getattr(candidate, "validation_policy_digest", None)
    if declared is not None:
        return declared
    definition = getattr(candidate, "definition", None)
    policy = getattr(definition, "validation_policy", None)
    if isinstance(policy, Mapping):
        for key in ("policy_digest", "digest"):
            if key in policy:
                return policy[key]
    return None


def _append(ledger: TrialLedger, trial_id: str, alpha: str, family: str,
            policy_digest: str, status: str, index: int) -> None:
    ledger.append(TrialRecord(trial_id, alpha, family, alpha, policy_digest, status, index, 0))


def run_validation_dag(candidate: Any, panel: Any, policy: ValidationPolicy,
                       ledger: TrialLedger) -> ValidationArtifact:
    """Run cheap → OOS → realism exactly once for a candidate/panel/policy."""
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
    trial_id = _digest({"alpha_id": alpha, "panel_digest": panel_digest,
                        "policy_digest": policy.digest})
    cache = _ARTIFACT_CACHE.setdefault(ledger, {})
    if trial_id in cache:
        return cache[trial_id]

    _append(ledger, trial_id, alpha, family, policy.digest, "PLANNED", 0)
    _append(ledger, trial_id, alpha, family, policy.digest, "RUNNING", 1)
    context = ValidationContext(candidate, panel, policy, trial_id, panel_digest, policy.digest)
    hashes: dict[str, str] = {}
    verdict = "PASS"
    reason = None
    final_index = 2
    try:
        for stage in policy.stages:
            try:
                evidence = stage.evaluate(context)
                if not isinstance(evidence, StageEvidence) or evidence.stage != stage.name:
                    evidence = StageEvidence(stage.name, stage.version, "BLOCK",
                                             {"invalid": "provider result"},
                                             "invalid provider evidence")
            except Exception as exc:  # provider failures are evidence failures
                evidence = StageEvidence(stage.name, stage.version, "BLOCK",
                                         {"exception": type(exc).__name__},
                                         f"provider exception: {exc}")
            hashes[stage.name] = evidence.evidence_hash
            if evidence.outcome == "BLOCK":
                verdict = "BLOCK"
                reason = evidence.reason or f"{stage.name} blocked"
                break
    except Exception as exc:
        verdict = "BLOCK"
        reason = f"provider exception: {exc}"
    status = "PASSED" if verdict == "PASS" else "FAILED"
    _append(ledger, trial_id, alpha, family, policy.digest, status, final_index)
    count = ledger.effective_trial_count(family).count
    artifact = ValidationArtifact(trial_id, alpha, panel_digest, policy.digest,
                                  dict(hashes), count, verdict, reason)
    cache[trial_id] = artifact
    return artifact


__all__ = ["ValidationStage", "ValidationPolicy", "ValidationContext", "StageEvidence",
           "ValidationArtifact", "run_validation_dag"]
