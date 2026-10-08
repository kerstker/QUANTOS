"""Canonical contracts for offline model-risk governance.

These artifacts describe research models, prompts, tools, evaluations, red-team
results, and challenger reviews. They grant no execution or promotion authority.
All timestamps are supplied by the caller so evidence remains deterministic.
"""
from __future__ import annotations

import math
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .hashing import stable_hash


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ModelUseStatus(str, Enum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    REFUTED = "REFUTED"
    RETIRED = "RETIRED"


class PromptUseStatus(str, Enum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    DISABLED = "DISABLED"


class PromptLineageStatus(str, Enum):
    NO_RUNTIME_LLM_PROMPT_SURFACE = "NO_RUNTIME_LLM_PROMPT_SURFACE"
    REGISTERED = "REGISTERED"
    UNREGISTERED_RUNTIME_PROMPT = "UNREGISTERED_RUNTIME_PROMPT"


class EvaluationVerdict(str, Enum):
    PASS_RESEARCH_ONLY = "PASS_RESEARCH_ONLY"
    REFUTED_NO_EDGE = "REFUTED_NO_EDGE"
    FAIL = "FAIL"


class MetricDirection(str, Enum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"


class RedTeamVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class ChallengerVerdict(str, Enum):
    ACCEPT_RESEARCH_ONLY = "ACCEPT_RESEARCH_ONLY"
    REJECT_NO_IMPROVEMENT = "REJECT_NO_IMPROVEMENT"
    REJECT_ABSOLUTE_FLOOR = "REJECT_ABSOLUTE_FLOOR"


class ReviewIdentityAssurance(str, Enum):
    PROCESS_ROLE_ONLY = "PROCESS_ROLE_ONLY"
    EXTERNAL_STRONG_IDENTITY = "EXTERNAL_STRONG_IDENTITY"


class RuntimeInvocationOutcome(str, Enum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    ERROR = "ERROR"


class ExternalProviderBindingStatus(str, Enum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    DISABLED = "DISABLED"


def _canonical_payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _content_hash(model: BaseModel) -> str:
    return stable_hash(_canonical_payload(model))


def _normalize_digest(value: str) -> str:
    normalized = value.strip().lower()
    if _SHA256.fullmatch(normalized) is None:
        raise ValueError("must be a 64-character SHA-256 digest")
    return normalized


def _unique_nonempty(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} entries must be non-empty")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} entries must be unique")
    return values


class _FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    @property
    def content_hash(self) -> str:
        return _content_hash(self)


class ModelCard(_FrozenContract):
    model_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    owner_group: str = Field(min_length=1)
    model_type: str = Field(min_length=1)
    implementation_ref: str = Field(min_length=1)
    implementation_sha256: str
    dataset_ref: str = ""
    dataset_sha256: str = ""
    intended_use: str = Field(min_length=1)
    prohibited_uses: tuple[str, ...] = Field(min_length=1)
    prompt_lineage_status: PromptLineageStatus
    prompt_template_ref: str = ""
    tool_manifest_ref: str = ""
    status: ModelUseStatus
    limitations: tuple[str, ...] = ()
    recorded_at: int = Field(ge=0)

    @field_validator("implementation_sha256", mode="before")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _normalize_digest(value)

    @field_validator("dataset_sha256", mode="before")
    @classmethod
    def _validate_optional_dataset_digest(cls, value: str) -> str:
        return _normalize_digest(value) if value.strip() else ""

    @field_validator("prohibited_uses")
    @classmethod
    def _validate_prohibited_uses(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_nonempty(value, "prohibited_uses")

    @field_validator("limitations")
    @classmethod
    def _validate_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_nonempty(value, "limitations")

    @model_validator(mode="after")
    def _validate_prompt_lineage(self) -> "ModelCard":
        if bool(self.dataset_ref) != bool(self.dataset_sha256):
            raise ValueError("dataset_ref and dataset_sha256 must be supplied together")
        if (
            self.prompt_lineage_status == PromptLineageStatus.REGISTERED
            and not self.prompt_template_ref
        ):
            raise ValueError("registered prompt lineage requires prompt_template_ref")
        if (
            self.prompt_lineage_status
            == PromptLineageStatus.NO_RUNTIME_LLM_PROMPT_SURFACE
            and self.prompt_template_ref
        ):
            raise ValueError("a model without a runtime LLM prompt cannot name a prompt template")
        return self

    @property
    def model_ref(self) -> str:
        return f"{self.model_id}@{self.version}"

    @property
    def artifact_id(self) -> str:
        return f"model-card:{self.model_id}:{self.version}"


class PromptTemplateCard(_FrozenContract):
    prompt_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    owner_group: str = Field(min_length=1)
    template_ref: str = Field(min_length=1)
    template_sha256: str
    model_ref: str = Field(min_length=1)
    intended_use: str = Field(min_length=1)
    prohibited_uses: tuple[str, ...] = Field(min_length=1)
    tool_manifest_ref: str = ""
    status: PromptUseStatus
    recorded_at: int = Field(ge=0)

    @field_validator("template_sha256", mode="before")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _normalize_digest(value)

    @field_validator("prohibited_uses")
    @classmethod
    def _validate_prohibited_uses(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_nonempty(value, "prohibited_uses")

    @property
    def prompt_ref(self) -> str:
        return f"{self.prompt_id}@{self.version}"

    @property
    def artifact_id(self) -> str:
        return f"prompt-card:{self.prompt_id}:{self.version}"


class ToolManifest(_FrozenContract):
    manifest_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    implementation_ref: str = Field(min_length=1)
    implementation_sha256: str
    allowed_actions: tuple[str, ...] = Field(min_length=1)
    prohibited_actions: tuple[str, ...] = Field(min_length=1)
    recorded_at: int = Field(ge=0)

    @field_validator("implementation_sha256", mode="before")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _normalize_digest(value)

    @field_validator("allowed_actions")
    @classmethod
    def _validate_allowed(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_nonempty(value, "allowed_actions")

    @field_validator("prohibited_actions")
    @classmethod
    def _validate_prohibited(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_nonempty(value, "prohibited_actions")

    @model_validator(mode="after")
    def _validate_disjoint_actions(self) -> "ToolManifest":
        overlap = set(self.allowed_actions) & set(self.prohibited_actions)
        if overlap:
            raise ValueError("allowed and prohibited actions overlap: " + ", ".join(sorted(overlap)))
        return self

    @property
    def manifest_ref(self) -> str:
        return f"{self.manifest_id}@{self.version}"

    @property
    def artifact_id(self) -> str:
        return f"tool-manifest:{self.manifest_id}:{self.version}"


class ExternalProviderBinding(_FrozenContract):
    binding_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    provider_model_ref: str = Field(min_length=1)
    model_ref: str = Field(min_length=1)
    provider_deployment_ref: str = Field(min_length=1)
    verification_policy_ref: str = Field(min_length=1)
    allowed_actions: tuple[str, ...] = Field(min_length=1)
    status: ExternalProviderBindingStatus
    recorded_at: int = Field(ge=0)

    @field_validator("allowed_actions")
    @classmethod
    def _validate_allowed_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_nonempty(value, "allowed_actions")

    @model_validator(mode="after")
    def _reject_credential_material(self) -> "ExternalProviderBinding":
        payload = " ".join(
            (
                self.provider_id,
                self.provider_model_ref,
                self.provider_deployment_ref,
                self.verification_policy_ref,
            )
        ).lower()
        forbidden = ("api_key", "api-key", "secret=", "token=", "password=")
        if any(marker in payload for marker in forbidden):
            raise ValueError("provider bindings cannot contain credential material")
        return self

    @property
    def binding_ref(self) -> str:
        return f"{self.binding_id}@{self.version}"

    @property
    def artifact_id(self) -> str:
        return f"provider-binding:{self.binding_id}:{self.version}"


class ExternalProviderResponse(_FrozenContract):
    provider_id: str = Field(min_length=1)
    provider_model_ref: str = Field(min_length=1)
    external_request_id: str = Field(min_length=1)
    request_sha256: str
    response: Any
    response_sha256: str
    verification_receipt_ref: str = Field(min_length=1)
    received_at: int = Field(ge=0)

    @field_validator("request_sha256", "response_sha256", mode="before")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _normalize_digest(value)

    @model_validator(mode="after")
    def _validate_response_digest(self) -> "ExternalProviderResponse":
        if stable_hash(self.response) != self.response_sha256:
            raise ValueError("provider response digest does not match its payload")
        return self


class RuntimeInvocation(_FrozenContract):
    invocation_id: str = Field(min_length=1)
    model_ref: str = Field(min_length=1)
    prompt_ref: str = Field(min_length=1)
    tool_manifest_ref: str = Field(min_length=1)
    action: str = Field(min_length=1)
    input_sha256: str
    output_sha256: str = ""
    outcome: RuntimeInvocationOutcome
    recorded_at: int = Field(ge=0)

    @field_validator("input_sha256", mode="before")
    @classmethod
    def _validate_input_digest(cls, value: str) -> str:
        return _normalize_digest(value)

    @field_validator("output_sha256", mode="before")
    @classmethod
    def _validate_output_digest(cls, value: str) -> str:
        return _normalize_digest(value) if value.strip() else ""

    @model_validator(mode="after")
    def _validate_outcome(self) -> "RuntimeInvocation":
        if self.outcome == RuntimeInvocationOutcome.ALLOWED and not self.output_sha256:
            raise ValueError("an allowed runtime invocation requires an output digest")
        if self.outcome != RuntimeInvocationOutcome.ALLOWED and self.output_sha256:
            raise ValueError("a denied or failed invocation cannot claim an output digest")
        return self

    @property
    def runtime_ref(self) -> str:
        return f"runtime-invocation:{self.invocation_id}"


class EvaluationMetric(_FrozenContract):
    name: str = Field(min_length=1)
    value: float
    direction: MetricDirection = MetricDirection.HIGHER_IS_BETTER

    @field_validator("value")
    @classmethod
    def _validate_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric values must be finite")
        return value


class OfflineEvaluation(_FrozenContract):
    evaluation_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    model_ref: str = Field(min_length=1)
    dataset_ref: str = Field(min_length=1)
    dataset_sha256: str
    methodology: str = Field(min_length=1)
    metrics: tuple[EvaluationMetric, ...] = Field(min_length=1)
    cost_bps: float = Field(ge=0)
    source_artifact_ref: str = Field(min_length=1)
    source_artifact_sha256: str
    verdict: EvaluationVerdict
    limitations: tuple[str, ...] = Field(min_length=1)
    recorded_at: int = Field(ge=0)

    @field_validator("dataset_sha256", "source_artifact_sha256", mode="before")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _normalize_digest(value)

    @field_validator("cost_bps")
    @classmethod
    def _validate_cost(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("cost_bps must be finite")
        return value

    @field_validator("limitations")
    @classmethod
    def _validate_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_nonempty(value, "limitations")

    @model_validator(mode="after")
    def _validate_metric_names(self) -> "OfflineEvaluation":
        names = [metric.name for metric in self.metrics]
        if len(set(names)) != len(names):
            raise ValueError("evaluation metric names must be unique")
        return self

    @property
    def evaluation_ref(self) -> str:
        return f"evaluation:{self.evaluation_id}:{self.version}"

    @property
    def artifact_id(self) -> str:
        return self.evaluation_ref


class RedTeamCaseResult(_FrozenContract):
    case_id: str = Field(min_length=1)
    attack_class: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)
    actual_outcome: str = Field(min_length=1)
    boundary_breached: bool
    observed_exception: str = ""


class RedTeamReport(_FrozenContract):
    report_id: str = Field(min_length=1)
    model_ref: str = Field(min_length=1)
    tool_manifest_ref: str = Field(min_length=1)
    cases: tuple[RedTeamCaseResult, ...] = Field(min_length=1)
    required_case_ids: tuple[str, ...] = Field(min_length=1)
    verdict: RedTeamVerdict
    recorded_at: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_cases(self) -> "RedTeamReport":
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("red-team case IDs must be unique")
        if len(set(self.required_case_ids)) != len(self.required_case_ids):
            raise ValueError("required red-team case IDs must be unique")
        missing = sorted(set(self.required_case_ids) - set(case_ids))
        if missing:
            raise ValueError("missing required red-team cases: " + ", ".join(missing))
        failed = any(
            case.boundary_breached
            or case.actual_outcome != case.expected_outcome
            for case in self.cases
            if case.case_id in self.required_case_ids
        )
        expected = RedTeamVerdict.FAIL if failed else RedTeamVerdict.PASS
        if self.verdict != expected:
            raise ValueError("red-team verdict is inconsistent with required case outcomes")
        return self

    @property
    def artifact_id(self) -> str:
        return f"red-team-report:{self.report_id}"


class ChallengerReview(_FrozenContract):
    review_id: str = Field(min_length=1)
    incumbent_model_ref: str = Field(min_length=1)
    challenger_model_ref: str = Field(min_length=1)
    incumbent_evaluation_ref: str = Field(min_length=1)
    challenger_evaluation_ref: str = Field(min_length=1)
    model_owner_group: str = Field(min_length=1)
    reviewer_group: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    identity_assurance: ReviewIdentityAssurance = ReviewIdentityAssurance.PROCESS_ROLE_ONLY
    primary_metric: str = Field(min_length=1)
    incumbent_value: float
    challenger_value: float
    minimum_acceptable: float
    verdict: ChallengerVerdict
    limitations: tuple[str, ...] = ()
    recorded_at: int = Field(ge=0)

    @field_validator("incumbent_value", "challenger_value", "minimum_acceptable")
    @classmethod
    def _validate_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("challenger metrics must be finite")
        return value

    @field_validator("limitations")
    @classmethod
    def _validate_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_nonempty(value, "limitations")

    @model_validator(mode="after")
    def _validate_review(self) -> "ChallengerReview":
        if self.incumbent_model_ref == self.challenger_model_ref:
            raise ValueError("challenger review cannot compare a model with itself")
        if self.incumbent_evaluation_ref == self.challenger_evaluation_ref:
            raise ValueError("challenger review requires distinct evaluations")
        if self.model_owner_group == self.reviewer_group:
            raise ValueError("challenger review requires an independent reviewer group")
        expected = self.expected_verdict
        if self.verdict != expected:
            raise ValueError("challenger verdict is inconsistent with the metric policy")
        return self

    @property
    def metric_delta(self) -> float:
        return self.challenger_value - self.incumbent_value

    @property
    def expected_verdict(self) -> ChallengerVerdict:
        if self.challenger_value < self.minimum_acceptable:
            return ChallengerVerdict.REJECT_ABSOLUTE_FLOOR
        if self.challenger_value <= self.incumbent_value:
            return ChallengerVerdict.REJECT_NO_IMPROVEMENT
        return ChallengerVerdict.ACCEPT_RESEARCH_ONLY

    @property
    def artifact_id(self) -> str:
        return f"challenger-review:{self.review_id}"


ModelRiskArtifact = (
    ModelCard
    | PromptTemplateCard
    | ToolManifest
    | ExternalProviderBinding
    | OfflineEvaluation
    | RedTeamReport
    | ChallengerReview
)


__all__ = [
    "ChallengerReview",
    "ChallengerVerdict",
    "EvaluationMetric",
    "EvaluationVerdict",
    "ExternalProviderBinding",
    "ExternalProviderBindingStatus",
    "ExternalProviderResponse",
    "MetricDirection",
    "ModelCard",
    "ModelRiskArtifact",
    "ModelUseStatus",
    "OfflineEvaluation",
    "PromptLineageStatus",
    "PromptTemplateCard",
    "PromptUseStatus",
    "RedTeamCaseResult",
    "RedTeamReport",
    "RedTeamVerdict",
    "ReviewIdentityAssurance",
    "RuntimeInvocation",
    "RuntimeInvocationOutcome",
    "ToolManifest",
]
