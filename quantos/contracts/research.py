"""Research domain entities (blueprint §6.3).

Factors, alphas, strategies, experiments, backtest request/result, and the
validation result that gates promotion. Experiment/ValidationResult carry a
trial_count so multiple-testing correction (Deflated Sharpe / PBO) can account
for the total number of trials (CT-2 / plan P1-2).
"""

from __future__ import annotations

import unicodedata
from enum import Enum
from math import isclose, sqrt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import (
    canonical_json_bytes,
    canonical_sha256,
    float64_hex,
    load_canonical_json_bytes,
    parse_float64_hex,
)
from .validation import BatchPboEvidence, CatalogEvidence


def _canonical_hex(value: object) -> str:
    if isinstance(value, bool):
        raise ValueError("authority floats must be float64 hex strings")
    if isinstance(value, float):
        return float64_hex(value)
    if isinstance(value, str):
        return float64_hex(parse_float64_hex(value))
    raise ValueError("authority floats must be float64 hex strings")


class _FrozenAuthorityContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _validate_prefix_schedule(
    prefix_hashes: tuple["BootstrapPrefixHash", ...],
    *,
    block_length: int,
    terminal_replication_count: int,
) -> None:
    if not prefix_hashes:
        raise ValueError("prefix_hashes must not be empty")
    counts = tuple(prefix.replication_count for prefix in prefix_hashes)
    if any(prefix.block_length != block_length for prefix in prefix_hashes):
        raise ValueError("prefix hash must match block_length")
    if tuple(sorted(counts)) != counts or len(set(counts)) != len(counts):
        raise ValueError("prefix replication counts must be strictly increasing")
    if counts[-1] != terminal_replication_count:
        raise ValueError("terminal prefix replication count must match tail replication_count")


class PeriodKey(_FrozenAuthorityContract):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_time: int = Field(gt=0)
    frequency_id: str = Field(min_length=1)
    label_horizon: str = Field(min_length=1)

    @field_validator("frequency_id", "label_horizon", mode="before")
    @classmethod
    def _validate_canonical_period_text(cls, value: object) -> object:
        if isinstance(value, str) and unicodedata.normalize("NFC", value) != value:
            raise ValueError("period text must be NFC normalized")
        return value


class MonteCarloTailResult(_FrozenAuthorityContract):
    exceedance_count: int = Field(ge=0)
    replication_count: int = Field(gt=0)
    p_numerator: int = Field(gt=0)
    p_denominator: int = Field(gt=0)
    p_value: str
    conditional_variance_formula_id: Literal["BINOMIAL_CONDITIONAL_V1"]
    conservative_radius: str
    ambiguous: bool

    _normalize_p_value = field_validator("p_value", "conservative_radius", mode="before")(_canonical_hex)

    @model_validator(mode="after")
    def _validate_tail(self) -> "MonteCarloTailResult":
        if self.exceedance_count > self.replication_count:
            raise ValueError("exceedance_count must not exceed replication_count")
        if self.p_numerator != self.exceedance_count + 1:
            raise ValueError("p_numerator must equal 1 + exceedance_count")
        if self.p_denominator != self.replication_count + 1:
            raise ValueError("p_denominator must equal 1 + replication_count")
        if parse_float64_hex(self.p_value) != self.p_numerator / self.p_denominator:
            raise ValueError("p_value must equal p_numerator / p_denominator")
        return self


class BootstrapPrefixHash(_FrozenAuthorityContract):
    algorithm_version: Literal["QUANTOS_STATIONARY_BOOTSTRAP_V1"]
    block_length: int = Field(gt=0)
    replication_count: int = Field(gt=0)
    index_stream_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RealityCheckResult(_FrozenAuthorityContract):
    algorithm_version: str = Field(min_length=1)
    objective_version: Literal["MEAN_NET_RETURN_DIFFERENTIAL"]
    period_count: int = Field(ge=20)
    candidate_count: int = Field(ge=2)
    block_length: int = Field(gt=0)
    candidate_order: tuple[str, ...]
    column_means: tuple[str, ...]
    observed_statistic: str
    tail_result: MonteCarloTailResult
    prefix_hashes: tuple[BootstrapPrefixHash, ...]
    decision: Literal["AMBIGUOUS", "REJECT", "NON_REJECT", "INVALID"]
    run_status: Literal["PUBLISHABLE", "NON_PUBLISHABLE_TEST_RUN", "INVALID"]
    reasons: tuple[str, ...] = ()

    @field_validator("column_means", mode="before")
    @classmethod
    def _normalize_column_means(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("column_means must be a sequence")
        return tuple(_canonical_hex(item) for item in value)

    @field_validator("observed_statistic", mode="before")
    @classmethod
    def _normalize_observed_statistic(cls, value: object) -> str:
        return _canonical_hex(value)

    @model_validator(mode="after")
    def _validate_result(self) -> "RealityCheckResult":
        if self.block_length > self.period_count // 2:
            raise ValueError("block_length must not exceed half the period count")
        if len(self.candidate_order) != self.candidate_count or len(self.column_means) != self.candidate_count:
            raise ValueError("candidate order and column means must match candidate_count")
        if (
            tuple(sorted(self.candidate_order)) != self.candidate_order
            or len(set(self.candidate_order)) != self.candidate_count
        ):
            raise ValueError("candidate_order must be sorted and unique")
        _validate_prefix_schedule(
            self.prefix_hashes,
            block_length=self.block_length,
            terminal_replication_count=self.tail_result.replication_count,
        )
        return self


class SpaVariantResult(_FrozenAuthorityContract):
    variant: Literal["LOWER", "CONSISTENT", "UPPER"]
    centers: tuple[str, ...]
    tail_result: MonteCarloTailResult
    decision: Literal["AMBIGUOUS", "REJECT", "NON_REJECT", "INVALID"]

    @field_validator("centers", mode="before")
    @classmethod
    def _normalize_centers(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("centers must be a sequence")
        return tuple(_canonical_hex(item) for item in value)


class SpaResult(_FrozenAuthorityContract):
    algorithm_version: str = Field(min_length=1)
    objective_version: Literal["MEAN_NET_RETURN_DIFFERENTIAL"]
    period_count: int = Field(ge=20)
    candidate_count: int = Field(ge=2)
    block_length: int = Field(gt=0)
    candidate_order: tuple[str, ...]
    observed_statistic: str
    gamma_zero: tuple[str, ...]
    degeneracy_floors: tuple[str, ...]
    long_run_variances: tuple[str, ...]
    thresholds: tuple[str, ...]
    variants: tuple[SpaVariantResult, ...]
    primary_variant: Literal["CONSISTENT"]
    prefix_hashes: tuple[BootstrapPrefixHash, ...]
    run_status: Literal["PUBLISHABLE", "NON_PUBLISHABLE_TEST_RUN", "INVALID"]
    reasons: tuple[str, ...] = ()

    @field_validator("observed_statistic", mode="before")
    @classmethod
    def _normalize_observed_statistic(cls, value: object) -> str:
        return _canonical_hex(value)

    @field_validator("gamma_zero", "degeneracy_floors", "long_run_variances", "thresholds", mode="before")
    @classmethod
    def _normalize_vectors(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("statistical vectors must be sequences")
        return tuple(_canonical_hex(item) for item in value)

    @model_validator(mode="after")
    def _validate_spa(self) -> "SpaResult":
        if self.block_length > self.period_count // 2:
            raise ValueError("block_length must not exceed half the period count")
        if (
            tuple(sorted(self.candidate_order)) != self.candidate_order
            or len(set(self.candidate_order)) != self.candidate_count
        ):
            raise ValueError("candidate_order must be sorted and unique")
        vectors = (self.gamma_zero, self.degeneracy_floors, self.long_run_variances, self.thresholds)
        if len(self.candidate_order) != self.candidate_count or any(
            len(vector) != self.candidate_count for vector in vectors
        ):
            raise ValueError("candidate vectors must match candidate_count")
        if tuple(variant.variant for variant in self.variants) != ("LOWER", "CONSISTENT", "UPPER"):
            raise ValueError("SPA variants must be LOWER, CONSISTENT, UPPER in order")
        if any(len(variant.centers) != self.candidate_count for variant in self.variants):
            raise ValueError("SPA variant centers must match candidate_count")
        tail_counts = {variant.tail_result.replication_count for variant in self.variants}
        if len(tail_counts) != 1:
            raise ValueError("SPA variants must share one tail replication_count")
        _validate_prefix_schedule(
            self.prefix_hashes,
            block_length=self.block_length,
            terminal_replication_count=tail_counts.pop(),
        )
        return self


class AlphaState(str, Enum):
    DRAFT = "DRAFT"
    TESTING = "TESTING"
    VALIDATED = "VALIDATED"
    PAPER_TRADING = "PAPER_TRADING"
    SHADOW = "SHADOW"  # dry-run deeper observation (post-paper, pre-approval); NO live authority
    APPROVED = "APPROVED"  # the only state that authorizes live — HUMAN-ONLY to reach
    DEGRADED = "DEGRADED"  # performance decayed, under watch (reversible)
    PAUSED = "PAUSED"  # reversible halt
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"
    # NOTE: small_live / production / live are deliberately ABSENT — promoting capital into live is the
    # L3 boundary (human-only, multi-step), not modelled in this L2_DRY_RUN_ONLY state machine.


class ValidationVerdict(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"


class Factor(BaseModel):
    model_config = ConfigDict(frozen=True)
    factor_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    definition: str = ""
    category: str = ""


class Alpha(BaseModel):
    model_config = ConfigDict(frozen=True)
    alpha_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    factor_ids: tuple[str, ...] = ()
    state: AlphaState = AlphaState.DRAFT
    reproducibility_hash: str = ""


class Strategy(BaseModel):
    model_config = ConfigDict(frozen=True)
    strategy_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    alpha_ids: tuple[str, ...] = ()
    approved: bool = False


class Experiment(BaseModel):
    model_config = ConfigDict(frozen=True)
    experiment_id: str = Field(min_length=1)
    strategy_id: str = ""
    config_hash: str = ""
    created_at: int = Field(default=0, ge=0)
    trial_count: int = Field(default=1, ge=1)


class BacktestRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    start_date: str
    end_date: str
    dataset_version: str = ""
    cost_model: str = "default"


class BacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: str = Field(min_length=1)
    sharpe: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = Field(default=0.0, ge=0)
    turnover: float = Field(default=0.0, ge=0)
    net_of_cost: bool = True
    reproducibility_hash: str = ""


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    subject_id: str = Field(min_length=1)
    verdict: ValidationVerdict
    deflated_sharpe: float | None = None
    catalog_evidence: CatalogEvidence | None = None
    catalog_evidence_hash: str = ""
    pbo: float | None = Field(default=None, ge=0, le=1)
    pbo_evidence: BatchPboEvidence | None = None
    pbo_evidence_hash: str = ""
    trial_count: int = Field(default=1, ge=1)
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_bound_validation_evidence(self) -> "ValidationResult":
        if self.catalog_evidence is None:
            if self.catalog_evidence_hash:
                raise ValueError("catalog evidence hash is unbound")
        else:
            evidence = self.catalog_evidence
            if self.catalog_evidence_hash != evidence.evidence_hash:
                raise ValueError("catalog evidence hash mismatch")
            if evidence.subject_id != self.subject_id:
                raise ValueError("catalog evidence subject mismatch")
            if self.deflated_sharpe is None or not isclose(
                self.deflated_sharpe,
                evidence.deflated_sharpe,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError("Deflated Sharpe does not match catalog evidence")
            if self.trial_count != evidence.trial_count:
                raise ValueError("trial count does not match catalog evidence")

        if self.pbo_evidence is None:
            if self.pbo_evidence_hash:
                raise ValueError("PBO evidence hash is unbound")
        else:
            evidence = self.pbo_evidence
            if self.pbo_evidence_hash != evidence.evidence_hash:
                raise ValueError("PBO evidence hash mismatch")
            if self.pbo is None or not isclose(
                self.pbo, evidence.pbo, rel_tol=0.0, abs_tol=1e-12
            ):
                raise ValueError("PBO does not match retained evidence")
            if (
                self.catalog_evidence is not None
                and self.catalog_evidence.subject_definition_hash
                not in evidence.candidate_definition_hashes
            ):
                raise ValueError("PBO evidence does not contain the subject")
        return self

    @property
    def promotable(self) -> bool:
        # Only an unqualified PASS is auto-promotable. WARN (a soft failure
        # exists) and REQUIRES_HUMAN_REVIEW need explicit human action.
        return self.verdict == ValidationVerdict.PASS


class ResearchReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    report_id: str = Field(min_length=1)
    subject_id: str = ""
    summary: str = ""
    created_at: int = Field(default=0, ge=0)


# Exact-v3 Pass C shared canonical contract primitives.  These are immutable
# public values; persistence-chain fields remain private to schema authorities.
_NonEmptyText = Annotated[str, Field(min_length=1)]
_Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_GitObjectId = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
_PositiveEpochUs = Annotated[int, Field(gt=0)]
_PositiveOrdinal = Annotated[int, Field(gt=0)]
_NonNegativeCount = Annotated[int, Field(ge=0)]
_GovernedSqliteSeed = Annotated[int, Field(ge=0, le=(1 << 63) - 1)]
_CanonicalMediaType = Annotated[
    str,
    Field(
        min_length=3,
        max_length=127,
        pattern=(
            r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/"
            r"[a-z0-9][a-z0-9!#$&^_.+-]{0,62}\z"
        ),
    ),
]
_PublicAuthorityToken = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9_.-]{0,63}\z"),
]
_OpaqueQuarantineRecordId = Annotated[
    str,
    Field(min_length=36, max_length=36, pattern=r"^qtn_[0-9a-f]{32}\z"),
]
_ProposalLengthBucket = Literal[
    "LE_1_KIB",
    "GT_1_LE_4_KIB",
    "GT_4_LE_16_KIB",
    "GT_16_LE_64_KIB",
    "GT_64_LE_256_KIB",
    "GT_256_LE_1024_KIB",
    "GT_1_MIB",
]


def _proposal_length_bucket(raw_byte_length: int) -> str:
    for upper_bound, bucket in (
        (1024, "LE_1_KIB"),
        (4096, "GT_1_LE_4_KIB"),
        (16384, "GT_4_LE_16_KIB"),
        (65536, "GT_16_LE_64_KIB"),
        (262144, "GT_64_LE_256_KIB"),
        (1048576, "GT_256_LE_1024_KIB"),
    ):
        if raw_byte_length <= upper_bound:
            return bucket
    return "GT_1_MIB"


def _require_nfc(value: Any) -> Any:
    """Fail closed on non-canonical text, including nested input payloads."""

    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError("authority strings must be NFC normalized")
        return value
    if isinstance(value, (tuple, list)):
        for item in value:
            _require_nfc(item)
        return value
    if isinstance(value, dict):
        for key, item in value.items():
            _require_nfc(key)
            _require_nfc(item)
    return value


class _FrozenCanonicalResearchContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def _validate_canonical_text(cls, value: Any) -> Any:
        return _require_nfc(value)


# Exact-v3 Pass C C4 domain contracts.  Constructing these values validates
# their content only; authority services remain the sole issuers of identities.
class StructuredResearchQuestion(_FrozenCanonicalResearchContract):
    question_schema_version: Literal["STRUCTURED_RESEARCH_QUESTION_V1"]
    market_domain: _NonEmptyText
    universe_domain: _NonEmptyText
    signal_class: _NonEmptyText
    label_definition: _NonEmptyText
    evaluation_horizon: _NonEmptyText


class ResearchQuestionRef(_FrozenCanonicalResearchContract):
    research_question_id: _NonEmptyText
    question_hash: _Sha256Digest
    question: StructuredResearchQuestion

    @model_validator(mode="after")
    def _validate_question_hash(self) -> "ResearchQuestionRef":
        if self.question_hash != canonical_sha256(self.question):
            raise ValueError("question_hash does not match the structured question")
        return self


def _canonical_alias_hash(alias: str) -> str:
    return canonical_sha256({"alias": alias})


class ResearchQuestionAlias(_FrozenCanonicalResearchContract):
    alias: _NonEmptyText
    alias_hash: _Sha256Digest
    research_question_id: _NonEmptyText

    @model_validator(mode="after")
    def _validate_alias_hash(self) -> "ResearchQuestionAlias":
        if self.alias_hash != _canonical_alias_hash(self.alias):
            raise ValueError("alias_hash does not match the canonical alias")
        return self


class SourceManifest(_FrozenCanonicalResearchContract):
    source_manifest_schema_version: Literal["SOURCE_MANIFEST_V1"]
    generator_kind: _NonEmptyText
    strategy_class: _NonEmptyText
    generator_entrypoint: _NonEmptyText
    source_tree_hash: _Sha256Digest


class StrategyLineageRootRef(_FrozenCanonicalResearchContract):
    strategy_lineage_root_id: _NonEmptyText
    source_manifest_hash: _Sha256Digest
    source_manifest: SourceManifest

    @model_validator(mode="after")
    def _validate_source_manifest_hash(self) -> "StrategyLineageRootRef":
        if self.source_manifest_hash != canonical_sha256(self.source_manifest):
            raise ValueError("source_manifest_hash does not match the source manifest")
        return self


class StrategyLineageNodeRef(_FrozenCanonicalResearchContract):
    strategy_lineage_node_id: _NonEmptyText
    strategy_lineage_root_id: _NonEmptyText
    parent_strategy_lineage_node_id: _NonEmptyText | None = None
    node_manifest_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_parent_identity(self) -> "StrategyLineageNodeRef":
        if self.parent_strategy_lineage_node_id == self.strategy_lineage_node_id:
            raise ValueError("a lineage node cannot be its own parent")
        return self


class DeploymentMandate(_FrozenCanonicalResearchContract):
    mandate_schema_version: Literal["DEPLOYMENT_MANDATE_V1"]
    portfolio_sleeve: _NonEmptyText
    capital_authority: _NonEmptyText
    risk_budget_authority: _NonEmptyText
    market_domain: _NonEmptyText
    universe_domain: _NonEmptyText
    label_definition: _NonEmptyText
    evaluation_horizon: _NonEmptyText
    benchmark_id: _NonEmptyText
    cost_policy_class: _NonEmptyText


class SelectionScopeRef(_FrozenCanonicalResearchContract):
    selection_scope_id: _NonEmptyText
    deployment_mandate_hash: _Sha256Digest
    deployment_mandate: DeploymentMandate

    @model_validator(mode="after")
    def _validate_deployment_mandate_hash(self) -> "SelectionScopeRef":
        if self.deployment_mandate_hash != canonical_sha256(self.deployment_mandate):
            raise ValueError("deployment_mandate_hash does not match the deployment mandate")
        return self


class SelectionScopeAlias(_FrozenCanonicalResearchContract):
    alias: _NonEmptyText
    alias_hash: _Sha256Digest
    selection_scope_id: _NonEmptyText

    @model_validator(mode="after")
    def _validate_alias_hash(self) -> "SelectionScopeAlias":
        if self.alias_hash != _canonical_alias_hash(self.alias):
            raise ValueError("alias_hash does not match the canonical alias")
        return self


class SelectionScopeOutcome(_FrozenCanonicalResearchContract):
    selection_scope_id: _NonEmptyText
    outcome: Literal["WINNER_PINNED", "CLOSED_WITHOUT_PROMOTION"]
    selection_decision_id: _NonEmptyText | None = None
    family_id: _NonEmptyText | None = None
    winner_alpha_id: _NonEmptyText | None = None
    evidence_ref_hash: _Sha256Digest | None = None

    @model_validator(mode="after")
    def _validate_terminal_branch(self) -> "SelectionScopeOutcome":
        winner_binding = (
            self.selection_decision_id,
            self.family_id,
            self.winner_alpha_id,
            self.evidence_ref_hash,
        )
        if self.outcome == "WINNER_PINNED":
            if any(value is None for value in winner_binding):
                raise ValueError("WINNER_PINNED requires the exact decision/family/alpha/evidence binding")
        elif any(value is not None for value in winner_binding):
            raise ValueError("CLOSED_WITHOUT_PROMOTION forbids every winner binding")
        return self


class ResearchProgramRef(_FrozenCanonicalResearchContract):
    research_program_id: _NonEmptyText
    selection_scope_id: _NonEmptyText
    research_question_id: _NonEmptyText
    question_hash: _Sha256Digest
    strategy_lineage_root_id: _NonEmptyText
    source_manifest_hash: _Sha256Digest


class SelectionDecisionRef(_FrozenCanonicalResearchContract):
    selection_decision_id: _NonEmptyText
    selection_scope_id: _NonEmptyText
    decision_ordinal: _PositiveOrdinal
    family_id: _NonEmptyText
    decision_class: Literal["PUBLISHABLE", "DIAGNOSTIC_ONLY"]


# Exact-v3 Pass C C5/C6 contracts follow.  C4's OOS exposure and consumption
# values are shared with those slices and therefore have one definition below.


class FamilySearchRecord(_FrozenCanonicalResearchContract):
    family_id: _NonEmptyText
    search_id: _NonEmptyText
    research_program_id: _NonEmptyText
    search_kind: _NonEmptyText
    generator_seed: _GovernedSqliteSeed
    proposal_budget: int = Field(gt=0)
    stopping_rule_hash: _Sha256Digest
    external_ingestion_deadline_us: _PositiveEpochUs
    search_authority_id: _NonEmptyText
    search_authority_head: _Sha256Digest
    linked_at_us: _PositiveEpochUs
    link_ordinal: _PositiveOrdinal

    @model_validator(mode="after")
    def _validate_search_window(self) -> "FamilySearchRecord":
        if self.linked_at_us > self.external_ingestion_deadline_us:
            raise ValueError("search must be linked no later than its ingestion deadline")
        return self


class CandidateIdentity(_FrozenCanonicalResearchContract):
    family_id: _NonEmptyText
    search_id: _NonEmptyText
    emission_ordinal: _PositiveOrdinal
    proposal_id: _Sha256Digest
    proposal_branch: Literal["CLEAN", "SECRET_QUARANTINED"]
    raw_envelope_hash: _Sha256Digest | None = None

    @model_validator(mode="after")
    def _validate_proposal_identity(self) -> "CandidateIdentity":
        proposal_key: dict[str, object] = {
            "family_id": self.family_id,
            "search_id": self.search_id,
            "emission_ordinal": self.emission_ordinal,
        }
        if self.proposal_branch == "SECRET_QUARANTINED":
            if self.raw_envelope_hash is not None:
                raise ValueError("quarantined identity cannot bind secret content")
            proposal_key["status"] = "SECRET_QUARANTINED"
        else:
            if self.raw_envelope_hash is None:
                raise ValueError("clean identity requires raw_envelope_hash")
            proposal_key["raw_envelope_hash"] = self.raw_envelope_hash
        if self.proposal_id != canonical_sha256(proposal_key):
            raise ValueError("proposal_id does not match the canonical public identity")
        return self


class CandidateProposalRegistrationRecord(_FrozenCanonicalResearchContract):
    identity: CandidateIdentity
    media_type: _CanonicalMediaType
    raw_byte_length: Annotated[int, Field(gt=0)] | None = None
    length_bucket: _ProposalLengthBucket
    dlp_policy_id: _PublicAuthorityToken
    dlp_policy_version: _PublicAuthorityToken
    scanner_implementation: _PublicAuthorityToken
    scanner_version: _PublicAuthorityToken
    dlp_result_code: Literal["CLEAN", "SECRET_QUARANTINED"]
    restricted_clean_blob_receipt_hash: _Sha256Digest | None = None
    quarantine_record_id: _OpaqueQuarantineRecordId | None = None
    registered_at_us: _PositiveEpochUs

    @model_validator(mode="after")
    def _validate_proposal_branch(self) -> "CandidateProposalRegistrationRecord":
        if self.identity.proposal_branch == "SECRET_QUARANTINED":
            if self.dlp_result_code != "SECRET_QUARANTINED":
                raise ValueError("quarantined identity requires quarantined DLP result")
            if self.raw_byte_length is not None:
                raise ValueError("quarantine public metadata cannot expose exact byte length")
            if self.restricted_clean_blob_receipt_hash is not None:
                raise ValueError("quarantine cannot reference a clean blob receipt")
            if self.quarantine_record_id is None:
                raise ValueError("quarantine requires an opaque quarantine record id")
        else:
            if self.dlp_result_code != "CLEAN":
                raise ValueError("clean identity requires a clean DLP result")
            if self.raw_byte_length is None:
                raise ValueError("clean proposal requires its exact byte length")
            if self.restricted_clean_blob_receipt_hash is None:
                raise ValueError("clean proposal requires a restricted blob receipt")
            if self.quarantine_record_id is not None:
                raise ValueError("clean proposal cannot reference quarantine")
            if self.length_bucket != _proposal_length_bucket(self.raw_byte_length):
                raise ValueError("clean proposal length_bucket must match its exact raw byte length")
        return self


class CandidateHypothesisRecord(_FrozenCanonicalResearchContract):
    family_id: _NonEmptyText
    first_search_id: _NonEmptyText
    config_hash: _Sha256Digest
    alpha_id: _NonEmptyText
    canonical_hypothesis_hash: _Sha256Digest
    first_proposal_id: _Sha256Digest


class CandidateEmissionSeal(_FrozenCanonicalResearchContract):
    family_id: _NonEmptyText
    search_id: _NonEmptyText
    declared_proposal_budget: int = Field(gt=0)
    actual_raw_proposal_count: _NonNegativeCount
    ordered_proposal_ids_hash: _Sha256Digest
    ordered_non_quarantined_raw_hashes_hash: _Sha256Digest
    ordered_canonical_proposal_hashes_hash: _Sha256Digest
    ordered_quarantine_record_ids_hash: _Sha256Digest
    accepted_config_set_hash: _Sha256Digest
    candidate_count: _NonNegativeCount
    candidate_chain_head: _Sha256Digest
    terminal_reason: _PublicAuthorityToken
    sealed_at_us: _PositiveEpochUs

    @model_validator(mode="after")
    def _validate_proposal_count(self) -> "CandidateEmissionSeal":
        if self.candidate_count > self.actual_raw_proposal_count:
            raise ValueError("candidate count cannot exceed the raw proposal count")
        if self.actual_raw_proposal_count > self.declared_proposal_budget:
            raise ValueError("actual proposal count cannot exceed the declared budget")
        return self


class CandidateDispositionReason(str, Enum):
    EVALUATION_EXCEPTION = "EVALUATION_EXCEPTION"
    EVALUATION_TIMEOUT = "EVALUATION_TIMEOUT"
    ATOMIC_COMMIT_FAILED = "ATOMIC_COMMIT_FAILED"
    ARTIFACT_LEASE_INVALID = "ARTIFACT_LEASE_INVALID"
    EXECUTION_ATTESTATION_MISMATCH = "EXECUTION_ATTESTATION_MISMATCH"
    RECOVERY_INCOMPLETE = "RECOVERY_INCOMPLETE"
    INVALID_UTF8 = "INVALID_UTF8"
    MALFORMED_JSON = "MALFORMED_JSON"
    DUPLICATE_JSON_KEY = "DUPLICATE_JSON_KEY"
    NON_NFC_TEXT = "NON_NFC_TEXT"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    TYPE_INVALID = "TYPE_INVALID"
    GUARDRAIL_REJECTED = "GUARDRAIL_REJECTED"
    DLP_SECRET_DETECTED = "DLP_SECRET_DETECTED"
    DLP_SCANNER_UNAVAILABLE = "DLP_SCANNER_UNAVAILABLE"
    DLP_SCANNER_ERROR = "DLP_SCANNER_ERROR"


_CandidateDispositionReasonCode = Literal[
    "EVALUATION_EXCEPTION",
    "EVALUATION_TIMEOUT",
    "ATOMIC_COMMIT_FAILED",
    "ARTIFACT_LEASE_INVALID",
    "EXECUTION_ATTESTATION_MISMATCH",
    "RECOVERY_INCOMPLETE",
    "INVALID_UTF8",
    "MALFORMED_JSON",
    "DUPLICATE_JSON_KEY",
    "NON_NFC_TEXT",
    "SCHEMA_INVALID",
    "TYPE_INVALID",
    "GUARDRAIL_REJECTED",
    "DLP_SECRET_DETECTED",
    "DLP_SCANNER_UNAVAILABLE",
    "DLP_SCANNER_ERROR",
]

_INCOMPLETE_DISPOSITION_REASONS = frozenset(
    {
        CandidateDispositionReason.EVALUATION_EXCEPTION.value,
        CandidateDispositionReason.EVALUATION_TIMEOUT.value,
        CandidateDispositionReason.ATOMIC_COMMIT_FAILED.value,
        CandidateDispositionReason.ARTIFACT_LEASE_INVALID.value,
        CandidateDispositionReason.EXECUTION_ATTESTATION_MISMATCH.value,
        CandidateDispositionReason.RECOVERY_INCOMPLETE.value,
    }
)
_PRE_EVALUATION_DISPOSITION_REASONS = frozenset(
    {
        CandidateDispositionReason.INVALID_UTF8.value,
        CandidateDispositionReason.MALFORMED_JSON.value,
        CandidateDispositionReason.DUPLICATE_JSON_KEY.value,
        CandidateDispositionReason.NON_NFC_TEXT.value,
        CandidateDispositionReason.SCHEMA_INVALID.value,
        CandidateDispositionReason.TYPE_INVALID.value,
        CandidateDispositionReason.GUARDRAIL_REJECTED.value,
    }
)
_QUARANTINE_DISPOSITION_REASONS = frozenset(
    {
        CandidateDispositionReason.DLP_SECRET_DETECTED.value,
        CandidateDispositionReason.DLP_SCANNER_UNAVAILABLE.value,
        CandidateDispositionReason.DLP_SCANNER_ERROR.value,
    }
)


class CandidateDisposition(_FrozenCanonicalResearchContract):
    family_id: _NonEmptyText
    proposal_id: _Sha256Digest
    disposition: Literal[
        "PERFORMANCE_EVALUATED",
        "PERFORMANCE_ATTEMPTED_INCOMPLETE",
        "PRE_EVALUATION_INVALID",
        "SECRET_QUARANTINED",
    ]
    config_hash: _Sha256Digest | None = None
    evaluation_receipt_hash: _Sha256Digest | None = None
    quarantine_record_id: _OpaqueQuarantineRecordId | None = None
    reason_code: _CandidateDispositionReasonCode | None = None
    performance_accessed: bool

    @model_validator(mode="after")
    def _validate_disposition_branch(self) -> "CandidateDisposition":
        if self.disposition == "PERFORMANCE_EVALUATED":
            if self.config_hash is None or self.evaluation_receipt_hash is None:
                raise ValueError("evaluated disposition requires config and receipt hashes")
            if self.quarantine_record_id is not None or not self.performance_accessed:
                raise ValueError("evaluated disposition must record performance access only")
            if self.reason_code is not None:
                raise ValueError("evaluated disposition cannot claim a failure reason")
        elif self.disposition == "PERFORMANCE_ATTEMPTED_INCOMPLETE":
            if self.config_hash is None or self.reason_code not in _INCOMPLETE_DISPOSITION_REASONS:
                raise ValueError("incomplete disposition requires config hash and reason")
            if self.evaluation_receipt_hash is not None or self.quarantine_record_id is not None:
                raise ValueError("incomplete disposition cannot claim a receipt or quarantine")
            if not self.performance_accessed:
                raise ValueError("incomplete disposition must record attempted performance access")
        elif self.disposition == "PRE_EVALUATION_INVALID":
            if self.reason_code not in _PRE_EVALUATION_DISPOSITION_REASONS:
                raise ValueError("pre-evaluation invalid disposition requires a reason")
            if (
                any(
                    value is not None
                    for value in (
                        self.config_hash,
                        self.evaluation_receipt_hash,
                        self.quarantine_record_id,
                    )
                )
                or self.performance_accessed
            ):
                raise ValueError("pre-evaluation invalid disposition cannot bind evaluation state")
        else:
            if self.quarantine_record_id is None or self.reason_code not in _QUARANTINE_DISPOSITION_REASONS:
                raise ValueError("quarantine disposition requires opaque id and reason")
            if self.config_hash is not None or self.evaluation_receipt_hash is not None:
                raise ValueError("quarantine disposition cannot bind parsed or evaluated content")
            if self.performance_accessed:
                raise ValueError("quarantine disposition cannot access performance data")
        return self


class RegisteredEnvelopeHandle(_FrozenCanonicalResearchContract):
    proposal_id: _Sha256Digest
    family_id: _NonEmptyText
    search_id: _NonEmptyText
    emission_ordinal: _PositiveOrdinal
    restricted_clean_blob_receipt_hash: _Sha256Digest
    registration_record_hash: _Sha256Digest
    registration_authority_head: _Sha256Digest


class CodeManifestPolicyRef(_FrozenCanonicalResearchContract):
    policy_id: _NonEmptyText
    policy_version: _NonEmptyText
    policy_hash: _Sha256Digest


class CodeManifestEntry(_FrozenCanonicalResearchContract):
    repo_path: _NonEmptyText
    git_object_format: Literal["SHA1", "SHA256"]
    git_blob_oid: _GitObjectId
    blob_sha256: _Sha256Digest

    @field_validator("repo_path")
    @classmethod
    def _validate_repo_path(cls, value: str) -> str:
        parts = value.split("/")
        if (
            value.startswith("/")
            or value.endswith("/")
            or "\\" in value
            or any(part in {"", ".", ".."} for part in parts)
            or parts[0].endswith(":")
        ):
            raise ValueError("repo_path must be a normalized relative POSIX path")
        return value

    @model_validator(mode="after")
    def _validate_git_oid(self) -> "CodeManifestEntry":
        expected_length = 40 if self.git_object_format == "SHA1" else 64
        if len(self.git_blob_oid) != expected_length:
            raise ValueError("git blob OID length does not match git_object_format")
        return self


class CodeManifestEvidence(_FrozenCanonicalResearchContract):
    source_commit: _GitObjectId
    worktree_state: Literal["CLEAN", "DIRTY"]
    dirty_tree_fingerprint: _Sha256Digest | None = None
    policy_ref: CodeManifestPolicyRef
    ordered_entries: Annotated[tuple[CodeManifestEntry, ...], Field(min_length=1)]
    path_set_hash: _Sha256Digest
    manifest_hash: _Sha256Digest
    runtime_identity: _NonEmptyText
    dependency_lock_hash: _Sha256Digest
    authority_id: _NonEmptyText
    authority_head: _Sha256Digest
    evidence_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_manifest(self) -> "CodeManifestEvidence":
        if (self.worktree_state == "DIRTY") != (self.dirty_tree_fingerprint is not None):
            raise ValueError("DIRTY requires and CLEAN forbids dirty_tree_fingerprint")
        paths = tuple(entry.repo_path for entry in self.ordered_entries)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("manifest entries must be path-sorted and unique")
        formats = {entry.git_object_format for entry in self.ordered_entries}
        if len(formats) != 1:
            raise ValueError("one manifest cannot mix Git object formats")
        expected_commit_length = 40 if formats == {"SHA1"} else 64
        if len(self.source_commit) != expected_commit_length:
            raise ValueError("source commit length does not match manifest object format")
        if self.path_set_hash != canonical_sha256(paths):
            raise ValueError("path_set_hash must bind the exact ordered repository paths")
        manifest_payload = self.model_dump(
            mode="python",
            exclude={
                "manifest_hash",
                "authority_id",
                "authority_head",
                "evidence_hash",
            },
        )
        if self.manifest_hash != canonical_sha256(manifest_payload):
            raise ValueError("manifest_hash must bind the exact code manifest")
        if self.evidence_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"evidence_hash"})
        ):
            raise ValueError("evidence_hash must bind the exact authority-issued manifest")
        return self


def _canonical_alpha_proposal_identities(
    canonical_proposal_json: str,
) -> tuple[str, str, str]:
    payload_bytes = canonical_proposal_json.encode("utf-8")
    proposal = load_canonical_json_bytes(payload_bytes)
    if canonical_json_bytes(proposal) != payload_bytes or type(proposal) is not dict:
        raise ValueError("canonical proposal must be one exact canonical JSON object")
    if set(proposal) != {"alpha_id", "factor_ids", "name", "params", "universe"}:
        raise ValueError("canonical proposal must match the closed AlphaDefinition shape")

    alpha_id = proposal["alpha_id"]
    name = proposal["name"]
    factor_ids = proposal["factor_ids"]
    universe = proposal["universe"]
    params = proposal["params"]
    if type(alpha_id) is not str or not alpha_id:
        raise ValueError("canonical proposal alpha_id must be non-empty text")
    if type(name) is not str or not name:
        raise ValueError("canonical proposal name must be non-empty text")
    if type(factor_ids) is not list or any(
        type(item) is not str or not item for item in factor_ids
    ):
        raise ValueError("canonical proposal factor_ids must be exact text values")
    if type(universe) is not str:
        raise ValueError("canonical proposal universe must be exact text")
    if type(params) is not dict or any(
        type(key) is not str or not key or type(value) is not str
        for key, value in params.items()
    ):
        raise ValueError("canonical proposal params must be an exact text mapping")

    config = {
        "factor_ids": factor_ids,
        "universe": universe,
        "params": params,
    }
    return canonical_sha256(proposal), canonical_sha256(config), alpha_id


class CandidateProposalRecord(_FrozenCanonicalResearchContract):
    """Exactly one terminal parse result for one committed registration."""

    proposal_id: _Sha256Digest
    family_id: _NonEmptyText
    search_id: _NonEmptyText
    emission_ordinal: _PositiveOrdinal
    registration_record_hash: _Sha256Digest
    parse_status: Literal["ACCEPTED", "INVALID", "SECRET_QUARANTINED"]
    canonical_proposal_json: str | None = None
    canonical_proposal_hash: _Sha256Digest | None = None
    config_hash: _Sha256Digest | None = None
    alpha_id: _NonEmptyText | None = None
    parsed_at_us: _PositiveEpochUs

    @model_validator(mode="after")
    def _validate_terminal_parse_result(self) -> "CandidateProposalRecord":
        parsed_fields = (
            self.canonical_proposal_json,
            self.canonical_proposal_hash,
            self.config_hash,
            self.alpha_id,
        )
        if self.parse_status != "ACCEPTED":
            if any(value is not None for value in parsed_fields):
                raise ValueError(
                    "invalid or quarantined terminal results forbid parsed fields"
                )
            return self
        if any(value is None for value in parsed_fields):
            raise ValueError("accepted terminal result requires every parsed field")
        assert self.canonical_proposal_json is not None
        expected_proposal_hash, expected_config_hash, expected_alpha_id = (
            _canonical_alpha_proposal_identities(self.canonical_proposal_json)
        )
        if self.canonical_proposal_hash != expected_proposal_hash:
            raise ValueError("canonical_proposal_hash does not bind the canonical bytes")
        if self.config_hash != expected_config_hash:
            raise ValueError("config_hash does not bind the canonical configuration")
        if self.alpha_id != expected_alpha_id:
            raise ValueError("alpha_id does not match the canonical proposal")
        return self


class ExecutionCodeAttestation(_FrozenCanonicalResearchContract):
    code_manifest_hash: _Sha256Digest
    source_commit: _GitObjectId
    runtime_identity: _NonEmptyText
    dependency_lock_hash: _Sha256Digest
    immutable_artifact_id: _NonEmptyText
    immutable_artifact_digest: _Sha256Digest
    entrypoint: _NonEmptyText
    process_image_hash: _Sha256Digest
    lease_id: _NonEmptyText
    authority_id: _NonEmptyText
    authority_head: _Sha256Digest
    issued_at_us: _PositiveEpochUs
    attestation_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_attestation_hash(self) -> "ExecutionCodeAttestation":
        if self.attestation_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"attestation_hash"})
        ):
            raise ValueError("attestation_hash must bind the exact execution evidence")
        return self


class ImmutableArtifactLease(_FrozenCanonicalResearchContract):
    lease_id: _NonEmptyText
    artifact_id: _NonEmptyText
    artifact_digest: _Sha256Digest
    code_manifest_hash: _Sha256Digest
    process_image_hash: _Sha256Digest
    authority_id: _NonEmptyText
    authority_head: _Sha256Digest
    acquired_at_us: _PositiveEpochUs
    expires_at_us: _PositiveEpochUs
    lease_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_lease_window(self) -> "ImmutableArtifactLease":
        if self.expires_at_us <= self.acquired_at_us:
            raise ValueError("lease must expire after acquisition")
        if self.lease_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"lease_hash"})
        ):
            raise ValueError("lease_hash must bind the exact immutable artifact lease")
        return self


class ObservedPitDatasetHandle(_FrozenCanonicalResearchContract):
    authority_id: _NonEmptyText
    handle_id: _NonEmptyText
    provenance_class: Literal["OBSERVED_PIT"]
    dataset_id: _NonEmptyText
    dataset_version: _NonEmptyText
    raw_archive_digest: _Sha256Digest
    raw_archive_byte_length: int = Field(gt=0)
    normalized_pit_store_digest: _Sha256Digest
    normalized_pit_store_version: _NonEmptyText
    normalized_store_byte_length: int = Field(gt=0)
    quality_admission_id: _NonEmptyText
    quality_authority_head: _Sha256Digest
    universe_id: _NonEmptyText
    universe_hash: _Sha256Digest
    ordered_period_calendar_hash: _Sha256Digest
    knowledge_time_policy_id: _NonEmptyText
    knowledge_time_policy_version: _NonEmptyText
    label_horizon: _NonEmptyText
    purge_periods: _NonNegativeCount
    embargo_periods: _NonNegativeCount
    cost_contract_hash: _Sha256Digest
    benchmark_contract_hash: _Sha256Digest
    content_hash: _Sha256Digest
    authority_head: _Sha256Digest


class OosExposureRef(_FrozenCanonicalResearchContract):
    oos_exposure_id: _NonEmptyText
    selection_scope_id: _NonEmptyText
    selection_decision_id: _NonEmptyText
    family_id: _NonEmptyText
    exposure_ordinal: _PositiveOrdinal
    observed_pit_authority_id: _NonEmptyText
    observed_pit_handle_id: _NonEmptyText
    observed_pit_content_hash: _Sha256Digest
    dataset_id: _NonEmptyText
    dataset_version: _NonEmptyText
    universe_id: _NonEmptyText
    universe_hash: _Sha256Digest
    ordered_period_calendar_hash: _Sha256Digest
    knowledge_time_policy_id: _NonEmptyText
    knowledge_time_policy_version: _NonEmptyText
    label_horizon: _NonEmptyText
    purge_periods: _NonNegativeCount
    embargo_periods: _NonNegativeCount
    cost_contract_hash: _Sha256Digest
    benchmark_contract_hash: _Sha256Digest
    period_start_us: _PositiveEpochUs
    period_end_us: _PositiveEpochUs
    exposure_fingerprint: _Sha256Digest
    exposure_version: _PositiveOrdinal
    observed_pit_authority_head: _Sha256Digest

    @model_validator(mode="after")
    def _validate_exposure(self) -> "OosExposureRef":
        if self.period_end_us <= self.period_start_us:
            raise ValueError("OOS exposure period must be strictly increasing")
        binding = {
            "observed_pit_content_hash": self.observed_pit_content_hash,
            "dataset_version": self.dataset_version,
            "universe_id": self.universe_id,
            "universe_hash": self.universe_hash,
            "ordered_period_calendar_hash": self.ordered_period_calendar_hash,
            "label_horizon": self.label_horizon,
            "purge_periods": self.purge_periods,
            "embargo_periods": self.embargo_periods,
        }
        if self.exposure_fingerprint != canonical_sha256(binding):
            raise ValueError("exposure_fingerprint does not match immutable data binding")
        return self


class OosExposureConsumption(_FrozenCanonicalResearchContract):
    oos_exposure_id: _NonEmptyText
    selection_decision_id: _NonEmptyText
    family_id: _NonEmptyText
    access_attempt_id: _NonEmptyText
    expected_exposure_version: _PositiveOrdinal
    consumed_at_us: _PositiveEpochUs
    consumption_authority_id: _NonEmptyText
    consumption_record_hash: _Sha256Digest
    consumption_authority_head: _Sha256Digest


# Exact-v3 Pass C C7 public evidence contracts.  These immutable values bind
# canonical one-shot OOS evidence only; they neither read OOS data nor activate
# a persistence schema or evaluation service.
_Float64HexVector = Annotated[tuple[str, ...], Field(min_length=1)]


class EvaluationSeriesReceipt(_FrozenCanonicalResearchContract):
    ordered_period_keys: Annotated[tuple[PeriodKey, ...], Field(min_length=1)]
    gross_returns: _Float64HexVector
    costs: _Float64HexVector
    net_returns: _Float64HexVector
    turnovers: _Float64HexVector
    ordered_period_keys_hash: _Sha256Digest
    gross_returns_hash: _Sha256Digest
    costs_hash: _Sha256Digest
    net_returns_hash: _Sha256Digest
    turnovers_hash: _Sha256Digest
    cost_contract_hash: _Sha256Digest
    cost_model_input_hash: _Sha256Digest
    series_hash: _Sha256Digest

    @field_validator("gross_returns", "costs", "net_returns", "turnovers")
    @classmethod
    def _validate_float64_hex_vector(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            parse_float64_hex(item)
        return value

    @model_validator(mode="after")
    def _validate_series(self) -> "EvaluationSeriesReceipt":
        cardinalities = {
            len(self.ordered_period_keys),
            len(self.gross_returns),
            len(self.costs),
            len(self.net_returns),
            len(self.turnovers),
        }
        if len(cardinalities) != 1:
            raise ValueError("all evaluation-series vectors must have equal cardinality")

        event_times = tuple(period.event_time for period in self.ordered_period_keys)
        if any(left >= right for left, right in zip(event_times, event_times[1:])):
            raise ValueError("period keys must be strictly increasing and unique")
        if len({period.frequency_id for period in self.ordered_period_keys}) != 1:
            raise ValueError("all period keys must share one frequency")
        if len({period.label_horizon for period in self.ordered_period_keys}) != 1:
            raise ValueError("all period keys must share one label horizon")

        gross = tuple(parse_float64_hex(item) for item in self.gross_returns)
        costs = tuple(parse_float64_hex(item) for item in self.costs)
        turnovers = tuple(parse_float64_hex(item) for item in self.turnovers)
        if any(value < 0.0 for value in (*costs, *turnovers)):
            raise ValueError("costs and turnovers must be nonnegative")
        if any(
            float64_hex(gross_item - cost_item) != net_item
            for gross_item, cost_item, net_item in zip(gross, costs, self.net_returns)
        ):
            raise ValueError("net returns must exactly reconcile gross returns less costs")

        components: tuple[tuple[object, str], ...] = (
            (self.ordered_period_keys, self.ordered_period_keys_hash),
            (self.gross_returns, self.gross_returns_hash),
            (self.costs, self.costs_hash),
            (self.net_returns, self.net_returns_hash),
            (self.turnovers, self.turnovers_hash),
        )
        if any(canonical_sha256(value) != digest for value, digest in components):
            raise ValueError("evaluation-series component hash mismatch")
        if self.series_hash != canonical_sha256(self.model_dump(mode="python", exclude={"series_hash"})):
            raise ValueError("series_hash does not cover the canonical series receipt")
        return self


class CandidateReturnSeries(_FrozenCanonicalResearchContract):
    family_id: _NonEmptyText
    evaluation_batch_id: _NonEmptyText
    config_hash: _Sha256Digest
    alpha_id: _NonEmptyText
    evaluation_run_id: _NonEmptyText
    evaluation_content_hash: _Sha256Digest
    evaluation_audit_intent_hash: _Sha256Digest
    source_search_id: _NonEmptyText
    source_proposal_id: _Sha256Digest
    series: EvaluationSeriesReceipt
    evaluation_receipt_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_receipt_hash(self) -> "CandidateReturnSeries":
        if self.evaluation_receipt_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"evaluation_receipt_hash"})
        ):
            raise ValueError("evaluation_receipt_hash does not cover the candidate receipt")
        return self


class BenchmarkReturnSeries(_FrozenCanonicalResearchContract):
    family_id: _NonEmptyText
    evaluation_batch_id: _NonEmptyText
    benchmark_id: _NonEmptyText
    benchmark_contract_hash: _Sha256Digest
    series: EvaluationSeriesReceipt
    benchmark_receipt_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_receipt_hash(self) -> "BenchmarkReturnSeries":
        if self.benchmark_receipt_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"benchmark_receipt_hash"})
        ):
            raise ValueError("benchmark_receipt_hash does not cover the benchmark receipt")
        return self


class FamilyEvaluationBatchReceipt(_FrozenCanonicalResearchContract):
    record_type: Literal["DATA_SNOOPING_EVALUATION_BATCH_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    family_id: _NonEmptyText
    attempt_id: _NonEmptyText
    evaluation_batch_id: _NonEmptyText
    frozen_config_set_hash: _Sha256Digest
    candidate_count: int = Field(gt=0)
    candidate_order: Annotated[tuple[_Sha256Digest, ...], Field(min_length=1)]
    candidate_receipt_hashes: Annotated[tuple[_Sha256Digest, ...], Field(min_length=1)]
    benchmark_receipt_hash: _Sha256Digest
    ordered_period_keys_hash: _Sha256Digest
    cost_contract_hash: _Sha256Digest
    start_execution_attestation_hash: _Sha256Digest
    terminal_execution_attestation_hash: _Sha256Digest
    immutable_artifact_lease_hash: _Sha256Digest
    committed_at_us: _PositiveEpochUs
    batch_receipt_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_batch_receipt(self) -> "FamilyEvaluationBatchReceipt":
        if self.candidate_order != tuple(sorted(self.candidate_order)):
            raise ValueError("candidate_order must be sorted")
        if len(set(self.candidate_order)) != len(self.candidate_order):
            raise ValueError("candidate_order must be unique")
        if len(set(self.candidate_receipt_hashes)) != len(self.candidate_receipt_hashes):
            raise ValueError("candidate receipt hashes must be unique")
        if self.candidate_count != len(self.candidate_order) or self.candidate_count != len(
            self.candidate_receipt_hashes
        ):
            raise ValueError("candidate count, order, and receipt hashes must agree")
        if self.frozen_config_set_hash != canonical_sha256(self.candidate_order):
            raise ValueError("frozen_config_set_hash must bind the exact candidate order")
        authority_row = {
            "record_type": self.record_type,
            "authority_instance_id": self.authority_instance_id,
            "genesis_record_hash": self.genesis_record_hash,
            "evaluation_batch_id": self.evaluation_batch_id,
            "family_id": self.family_id,
            "attempt_id": self.attempt_id,
            "frozen_config_set_hash": self.frozen_config_set_hash,
            "candidate_count": self.candidate_count,
            "ordered_candidate_config_hashes_hash": canonical_sha256(self.candidate_order),
            "ordered_candidate_receipt_hashes_hash": canonical_sha256(self.candidate_receipt_hashes),
            "benchmark_receipt_hash": self.benchmark_receipt_hash,
            "ordered_period_keys_hash": self.ordered_period_keys_hash,
            "cost_contract_hash": self.cost_contract_hash,
            "start_execution_attestation_hash": self.start_execution_attestation_hash,
            "terminal_execution_attestation_hash": self.terminal_execution_attestation_hash,
            "immutable_artifact_lease_hash": self.immutable_artifact_lease_hash,
            "committed_at_us": self.committed_at_us,
        }
        if self.batch_receipt_hash != canonical_sha256(authority_row):
            raise ValueError("batch_receipt_hash does not match the exact authority row")
        return self


class OosAccessAttempt(_FrozenCanonicalResearchContract):
    record_type: Literal["DATA_SNOOPING_OOS_ACCESS_ATTEMPT_EVENT_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    attempt_event_id: _NonEmptyText
    attempt_id: _NonEmptyText
    family_id: _NonEmptyText
    selection_decision_id: _NonEmptyText
    oos_exposure_id: _NonEmptyText
    evaluation_batch_id: _NonEmptyText
    sequence_no: Literal[0, 1]
    status: Literal["STARTED", "COMPLETED", "INCOMPLETE"]
    frozen_config_set_hash: _Sha256Digest
    benchmark_contract_hash: _Sha256Digest
    ordered_period_calendar_hash: _Sha256Digest
    cost_contract_hash: _Sha256Digest
    start_execution_attestation_hash: _Sha256Digest
    terminal_execution_attestation_hash: _Sha256Digest | None = None
    immutable_artifact_lease_hash: _Sha256Digest
    evaluation_batch_receipt_hash: _Sha256Digest | None = None
    reason_code: _NonEmptyText | None = None
    event_at_us: _PositiveEpochUs
    previous_event_hash: _Sha256Digest
    event_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_attempt_event(self) -> "OosAccessAttempt":
        if self.status == "STARTED":
            if self.sequence_no != 0 or self.previous_event_hash != self.genesis_record_hash:
                raise ValueError("STARTED must be sequence zero bound to genesis")
            if any(
                value is not None
                for value in (
                    self.terminal_execution_attestation_hash,
                    self.evaluation_batch_receipt_hash,
                    self.reason_code,
                )
            ):
                raise ValueError("STARTED forbids terminal evidence")
        elif self.status == "COMPLETED":
            if self.sequence_no != 1 or self.previous_event_hash == self.genesis_record_hash:
                raise ValueError("COMPLETED must advance the started event")
            if self.terminal_execution_attestation_hash is None or self.evaluation_batch_receipt_hash is None:
                raise ValueError("COMPLETED requires terminal attestation and batch receipt")
            if self.reason_code is not None:
                raise ValueError("COMPLETED forbids a failure reason")
        else:
            if self.sequence_no != 1 or self.previous_event_hash == self.genesis_record_hash:
                raise ValueError("INCOMPLETE must advance the started event")
            if self.evaluation_batch_receipt_hash is not None or self.reason_code is None:
                raise ValueError("INCOMPLETE requires a reason and forbids a batch receipt")

        authority_row = {
            "record_type": self.record_type,
            "authority_instance_id": self.authority_instance_id,
            "genesis_record_hash": self.genesis_record_hash,
            "attempt_event_id": self.attempt_event_id,
            "attempt_id": self.attempt_id,
            "family_id": self.family_id,
            "selection_decision_id": self.selection_decision_id,
            "oos_exposure_id": self.oos_exposure_id,
            "evaluation_batch_id": self.evaluation_batch_id,
            "sequence_no": self.sequence_no,
            "status": self.status,
            "frozen_config_set_hash": self.frozen_config_set_hash,
            "benchmark_contract_hash": self.benchmark_contract_hash,
            "ordered_period_calendar_hash": self.ordered_period_calendar_hash,
            "cost_contract_hash": self.cost_contract_hash,
            "start_execution_attestation_hash": self.start_execution_attestation_hash,
            "terminal_execution_attestation_hash": self.terminal_execution_attestation_hash,
            "immutable_artifact_lease_hash": self.immutable_artifact_lease_hash,
            "evaluation_batch_receipt_hash": self.evaluation_batch_receipt_hash,
            "reason_code": self.reason_code,
            "event_at_us": self.event_at_us,
            "prev_record_hash": self.previous_event_hash,
        }
        if self.event_hash != canonical_sha256(authority_row):
            raise ValueError("event_hash does not match the exact authority row")
        return self


class OosReleaseReceipt(_FrozenCanonicalResearchContract):
    record_type: Literal["DATA_SNOOPING_OOS_RELEASE_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    oos_release_id: _NonEmptyText
    family_id: _NonEmptyText
    attempt_id: _NonEmptyText
    started_attempt_event_id: _NonEmptyText
    family_freeze_record_hash: _Sha256Digest
    exposure_consumption_record_hash: _Sha256Digest
    oos_exposure_id: _NonEmptyText
    exposure_fingerprint: _Sha256Digest
    exposure_authority_id: _NonEmptyText
    exposure_authority_head: _Sha256Digest
    start_execution_attestation_hash: _Sha256Digest
    execution_attestation_authority_id: _NonEmptyText
    execution_attestation_authority_head: _Sha256Digest
    immutable_artifact_lease_id: _NonEmptyText
    immutable_artifact_lease_hash: _Sha256Digest
    immutable_artifact_lease_authority_id: _NonEmptyText
    immutable_artifact_lease_authority_head: _Sha256Digest
    released_at_us: _PositiveEpochUs
    release_record_hash: _Sha256Digest
    authority_head: _Sha256Digest

    @model_validator(mode="after")
    def _validate_release_receipt(self) -> "OosReleaseReceipt":
        authority_row = {
            "record_type": self.record_type,
            "authority_instance_id": self.authority_instance_id,
            "genesis_record_hash": self.genesis_record_hash,
            "exposure_fingerprint": self.exposure_fingerprint,
            "exposure_authority_id": self.exposure_authority_id,
            "exposure_authority_head": self.exposure_authority_head,
            "start_execution_attestation_hash": self.start_execution_attestation_hash,
            "execution_attestation_authority_id": self.execution_attestation_authority_id,
            "execution_attestation_authority_head": self.execution_attestation_authority_head,
            "immutable_artifact_lease_id": self.immutable_artifact_lease_id,
            "immutable_artifact_lease_hash": self.immutable_artifact_lease_hash,
            "immutable_artifact_lease_authority_id": self.immutable_artifact_lease_authority_id,
            "immutable_artifact_lease_authority_head": self.immutable_artifact_lease_authority_head,
            "oos_release_id": self.oos_release_id,
            "family_id": self.family_id,
            "attempt_id": self.attempt_id,
            "started_attempt_event_id": self.started_attempt_event_id,
            "family_freeze_record_hash": self.family_freeze_record_hash,
            "exposure_consumption_record_hash": self.exposure_consumption_record_hash,
            "oos_exposure_id": self.oos_exposure_id,
            "released_at_us": self.released_at_us,
        }
        expected_hash = canonical_sha256(authority_row)
        if self.release_record_hash != expected_hash:
            raise ValueError("release_record_hash does not match the exact authority row")
        if self.authority_head != expected_hash:
            raise ValueError("release authority head must equal the immutable release record hash")
        return self


# Exact-v3 Pass C C8 public evidence contracts.  These immutable values close
# one declared research family and assess it diagnostically.  They do not
# publish artifacts and can never grant promotion or trading authority.
_AssessmentAlgorithmVersion = Literal["WHITE_RC_HANSEN_SPA_FAMILY_V1"]
_FailureReasons = tuple[_PublicAuthorityToken, ...]


def _validate_failure_reasons(reasons: _FailureReasons) -> None:
    if reasons != tuple(sorted(reasons)) or len(set(reasons)) != len(reasons):
        raise ValueError("failure reasons must be sorted and unique public tokens")


class DataSnoopingDeclaration(_FrozenCanonicalResearchContract):
    record_type: Literal["DATA_SNOOPING_DECLARATION_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    declaration_id: _NonEmptyText
    family_id: _NonEmptyText
    selection_scope_id: _NonEmptyText
    deployment_mandate_hash: _Sha256Digest
    selection_decision_id: _NonEmptyText
    selection_scope_predecessor_head: _Sha256Digest
    decision_class: Literal["PUBLISHABLE", "DIAGNOSTIC_ONLY"]
    ordered_research_program_ids: Annotated[tuple[_NonEmptyText, ...], Field(min_length=1)]
    ordered_research_program_ids_hash: _Sha256Digest
    ordered_research_question_ids: Annotated[tuple[_NonEmptyText, ...], Field(min_length=1)]
    ordered_research_question_ids_hash: _Sha256Digest
    ordered_strategy_lineage_root_ids: Annotated[tuple[_NonEmptyText, ...], Field(min_length=1)]
    ordered_strategy_lineage_root_ids_hash: _Sha256Digest
    objective_id: Literal["MEAN_NET_RETURN_DIFFERENTIAL"]
    winner_selection_rule_id: Literal["MAX_MEAN_NET_RETURN_DIFFERENTIAL_THEN_CONFIG_HASH_V1"]
    search_data_contract_hash: _Sha256Digest
    oos_exposure_id: _NonEmptyText
    oos_release_policy_id: Literal["RELEASE_AFTER_FREEZE_AND_CONSUMPTION_V1"]
    oos_access_policy_id: Literal["ONE_SHOT_NO_RETRY_V1"]
    non_overlap_proof_hash: _Sha256Digest
    universe_id: _NonEmptyText
    universe_hash: _Sha256Digest
    ordered_period_calendar_hash: _Sha256Digest
    label_horizon: _NonEmptyText
    purge_periods: _NonNegativeCount
    embargo_periods: _NonNegativeCount
    return_currency: _NonEmptyText
    return_scale: Literal["DECIMAL_RETURN_V1"]
    compounding_convention: Literal["GEOMETRIC_V1"]
    cost_model_id: _NonEmptyText
    cost_contract_hash: _Sha256Digest
    cost_parameters_hash: _Sha256Digest
    benchmark_id: _NonEmptyText
    benchmark_contract_hash: _Sha256Digest
    alpha_level: str
    canonical_json_version: Literal["QUANTOS_CANONICAL_JSON_V1"]
    assessment_algorithm_version: _AssessmentAlgorithmVersion
    reality_check_algorithm_version: Literal["WHITE_REALITY_CHECK_V1"]
    spa_algorithm_version: Literal["HANSEN_SPA_V1"]
    bootstrap_algorithm_version: Literal["QUANTOS_STATIONARY_BOOTSTRAP_V1"]
    prng_algorithm_version: Literal["SPLITMIX64_V1"]
    block_length_policy_id: Literal["QUANTOS_BLOCK_LENGTH_GRID_V1"]
    block_length_grid: Annotated[tuple[int, ...], Field(min_length=1)]
    replication_schedule: Annotated[tuple[int, ...], Field(min_length=1)]
    bootstrap_seed: _GovernedSqliteSeed
    observed_pit_authority_id: _NonEmptyText
    observed_pit_handle_id: _NonEmptyText
    observed_pit_content_hash: _Sha256Digest
    knowledge_time_policy_id: _NonEmptyText
    knowledge_time_policy_version: _NonEmptyText
    observed_pit_authority_head: _Sha256Digest
    code_manifest_policy_id: _NonEmptyText
    code_manifest_policy_version: _NonEmptyText
    code_manifest_policy_hash: _Sha256Digest
    code_manifest_evidence_hash: _Sha256Digest
    code_manifest_authority_id: _NonEmptyText
    code_manifest_authority_head: _Sha256Digest
    dlp_policy_id: _NonEmptyText
    dlp_policy_version: _NonEmptyText
    dlp_policy_hash: _Sha256Digest
    scanner_implementation: _NonEmptyText
    scanner_version: _NonEmptyText
    restricted_envelope_authority_id: _NonEmptyText
    quarantine_policy_id: _NonEmptyText
    public_artifact_scan_policy_id: _NonEmptyText
    allowed_search_kinds: Annotated[tuple[_NonEmptyText, ...], Field(min_length=1)]
    generator_seeds: Annotated[tuple[_GovernedSqliteSeed, ...], Field(min_length=1)]
    search_proposal_budgets: Annotated[tuple[int, ...], Field(min_length=1)]
    required_search_count: int = Field(gt=0)
    maximum_search_count: int = Field(gt=0)
    stopping_rule_hash: _Sha256Digest
    external_ingestion_deadline_us: _PositiveEpochUs
    search_attachment_rule_id: _NonEmptyText
    trial_ledger_authority_id: _NonEmptyText
    raw_proposal_count_definition: Literal["EVERY_REGISTERED_OPAQUE_ENVELOPE_V1"]
    unique_performance_hypothesis_count_definition: Literal["DISTINCT_ACCEPTED_CONFIG_HASH_V1"]
    global_unique_config_count_definition: Literal["TRIAL_LEDGER_DISTINCT_CONFIG_AT_HISTORY_HEAD_V1"]
    performance_access_attempt_count_definition: Literal["DISTINCT_COMMITTED_STARTED_ATTEMPT_V1"]
    declared_at_us: _PositiveEpochUs
    declaration_record_hash: _Sha256Digest

    @field_validator("alpha_level")
    @classmethod
    def _validate_alpha_level(cls, value: str) -> str:
        parsed = parse_float64_hex(value)
        if not 0.0 < parsed < 1.0:
            raise ValueError("alpha_level must be strictly between zero and one")
        if float64_hex(parsed) != value:
            raise ValueError("alpha_level must use canonical float64 hex")
        return value

    @model_validator(mode="after")
    def _validate_declaration(self) -> "DataSnoopingDeclaration":
        ordered_components = (
            (self.ordered_research_program_ids, self.ordered_research_program_ids_hash),
            (self.ordered_research_question_ids, self.ordered_research_question_ids_hash),
            (self.ordered_strategy_lineage_root_ids, self.ordered_strategy_lineage_root_ids_hash),
        )
        for values, digest in ordered_components:
            if values != tuple(sorted(values)) or len(set(values)) != len(values):
                raise ValueError("declared scope identities must be sorted and unique")
            if canonical_sha256(values) != digest:
                raise ValueError("declared scope identity hash mismatch")

        for values, label in (
            (self.block_length_grid, "block-length grid"),
            (self.replication_schedule, "replication schedule"),
        ):
            if any(value <= 0 for value in values):
                raise ValueError(f"{label} values must be positive")
            if values != tuple(sorted(values)) or len(set(values)) != len(values):
                raise ValueError(f"{label} must be strictly increasing")

        if self.allowed_search_kinds != tuple(sorted(self.allowed_search_kinds)) or len(
            set(self.allowed_search_kinds)
        ) != len(self.allowed_search_kinds):
            raise ValueError("allowed_search_kinds must be sorted and unique")
        if any(budget <= 0 for budget in self.search_proposal_budgets):
            raise ValueError("search proposal budgets must be positive")
        cardinality = len(self.allowed_search_kinds)
        if not (
            cardinality
            == len(self.generator_seeds)
            == len(self.search_proposal_budgets)
            == self.required_search_count
            == self.maximum_search_count
        ):
            raise ValueError("the closed search plan must have one aligned entry per required search")
        if self.external_ingestion_deadline_us <= self.declared_at_us:
            raise ValueError("external ingestion deadline must be strictly later than declaration")
        if self.declaration_record_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"declaration_record_hash"})
        ):
            raise ValueError("declaration_record_hash does not cover the complete declaration")
        return self


class FamilyFreezeReceipt(_FrozenCanonicalResearchContract):
    record_type: Literal["DATA_SNOOPING_FAMILY_FREEZE_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    freeze_id: _NonEmptyText
    family_id: _NonEmptyText
    declaration_id: _NonEmptyText
    declaration_record_hash: _Sha256Digest
    search_count: int = Field(gt=0)
    ordered_search_ids: Annotated[tuple[_NonEmptyText, ...], Field(min_length=1)]
    ordered_search_ids_hash: _Sha256Digest
    ordered_search_terminal_hashes: Annotated[tuple[_Sha256Digest, ...], Field(min_length=1)]
    ordered_search_terminal_hashes_hash: _Sha256Digest
    ordered_emission_seal_hashes: Annotated[tuple[_Sha256Digest, ...], Field(min_length=1)]
    ordered_emission_seal_hashes_hash: _Sha256Digest
    ordered_candidate_chain_heads: Annotated[tuple[_Sha256Digest, ...], Field(min_length=1)]
    ordered_candidate_chain_heads_hash: _Sha256Digest
    raw_proposal_count: _NonNegativeCount
    unique_performance_hypothesis_count: _NonNegativeCount
    frozen_config_set_hash: _Sha256Digest
    observed_pit_content_hash: _Sha256Digest
    code_manifest_evidence_hash: _Sha256Digest
    dlp_policy_hash: _Sha256Digest
    stopping_rule_hash: _Sha256Digest
    frozen_at_us: _PositiveEpochUs
    freeze_record_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_freeze(self) -> "FamilyFreezeReceipt":
        if self.ordered_search_ids != tuple(sorted(self.ordered_search_ids)) or len(
            set(self.ordered_search_ids)
        ) != len(self.ordered_search_ids):
            raise ValueError("ordered_search_ids must be sorted and unique")
        vectors: tuple[tuple[object, ...], ...] = (
            self.ordered_search_ids,
            self.ordered_search_terminal_hashes,
            self.ordered_emission_seal_hashes,
            self.ordered_candidate_chain_heads,
        )
        if any(len(values) != self.search_count for values in vectors):
            raise ValueError("freeze receipt must bind every declared terminal search")
        if any(len(set(values)) != len(values) for values in vectors[1:]):
            raise ValueError("terminal search evidence hashes must be unique")
        component_hashes = (
            (self.ordered_search_ids, self.ordered_search_ids_hash),
            (self.ordered_search_terminal_hashes, self.ordered_search_terminal_hashes_hash),
            (self.ordered_emission_seal_hashes, self.ordered_emission_seal_hashes_hash),
            (self.ordered_candidate_chain_heads, self.ordered_candidate_chain_heads_hash),
        )
        if any(canonical_sha256(values) != digest for values, digest in component_hashes):
            raise ValueError("freeze component hash mismatch")
        if self.unique_performance_hypothesis_count > self.raw_proposal_count:
            raise ValueError("unique performance hypotheses cannot exceed raw proposals")
        if self.freeze_record_hash != canonical_sha256(self.model_dump(mode="python", exclude={"freeze_record_hash"})):
            raise ValueError("freeze_record_hash does not cover the complete freeze receipt")
        return self


class TrialLedgerProof(_FrozenCanonicalResearchContract):
    trial_ledger_authority_id: _NonEmptyText
    history_artifact_id: _NonEmptyText
    history_artifact_sha256: _Sha256Digest
    history_head: _Sha256Digest
    snapshot_artifact_id: _NonEmptyText
    snapshot_artifact_sha256: _Sha256Digest
    seal_artifact_id: _NonEmptyText
    seal_artifact_sha256: _Sha256Digest
    global_unique_config_count: _NonNegativeCount
    captured_at_us: _PositiveEpochUs
    proof_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_proof(self) -> "TrialLedgerProof":
        artifact_pairs = (
            (self.history_artifact_id, self.history_artifact_sha256),
            (self.snapshot_artifact_id, self.snapshot_artifact_sha256),
            (self.seal_artifact_id, self.seal_artifact_sha256),
        )
        if any(artifact_id != f"sha256:{digest}" for artifact_id, digest in artifact_pairs):
            raise ValueError("trial-ledger artifacts must be content addressed by their exact digest")
        if len({artifact_id for artifact_id, _ in artifact_pairs}) != len(artifact_pairs):
            raise ValueError("trial-ledger evidence artifacts must be distinct")
        if self.proof_hash != canonical_sha256(self.model_dump(mode="python", exclude={"proof_hash"})):
            raise ValueError("proof_hash does not cover the trial-ledger proof")
        return self


class DataSnoopingFamilyManifest(_FrozenCanonicalResearchContract):
    record_type: Literal["DATA_SNOOPING_FAMILY_CLOSURE_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    closure_id: _NonEmptyText
    family_id: _NonEmptyText
    declaration_id: _NonEmptyText
    declaration_record_hash: _Sha256Digest
    freeze_id: _NonEmptyText
    freeze_record_hash: _Sha256Digest
    closure_status: Literal["COMPLETE", "NOT_ASSESSABLE"]
    selection_scope_id: _NonEmptyText
    selection_decision_id: _NonEmptyText
    selection_scope_history_head: _Sha256Digest
    selection_scope_outcome_record_hash: _Sha256Digest | None
    ordered_search_ids_hash: _Sha256Digest
    ordered_search_terminal_hashes_hash: _Sha256Digest
    ordered_emission_seal_hashes_hash: _Sha256Digest
    ordered_proposal_ids_hash: _Sha256Digest
    ordered_disposition_record_hashes_hash: _Sha256Digest
    ordered_config_hashes: tuple[_Sha256Digest, ...]
    ordered_evaluation_receipt_hashes: tuple[_Sha256Digest, ...]
    attempt_id: _NonEmptyText | None
    terminal_attempt_event_id: _NonEmptyText | None
    evaluation_batch_id: _NonEmptyText | None
    evaluation_batch_receipt_hash: _Sha256Digest | None
    oos_release_id: _NonEmptyText | None
    oos_access_attempt_terminal_hash: _Sha256Digest | None
    oos_release_record_hash: _Sha256Digest | None
    raw_proposal_count: _NonNegativeCount
    unique_performance_hypothesis_count: _NonNegativeCount
    global_unique_config_count: _NonNegativeCount
    performance_access_attempt_count: _NonNegativeCount
    ordered_period_keys_hash: _Sha256Digest | None
    candidate_series_set_hash: _Sha256Digest | None
    benchmark_receipt_hash: _Sha256Digest | None
    cost_model_input_set_hash: _Sha256Digest | None
    winner_config_hash: _Sha256Digest | None
    winner_evaluation_receipt_hash: _Sha256Digest | None
    trial_ledger_proof: TrialLedgerProof
    trial_ledger_proof_hash: _Sha256Digest
    failure_reasons: _FailureReasons
    observed_pit_authority_id: _NonEmptyText
    observed_pit_handle_id: _NonEmptyText
    observed_pit_content_hash: _Sha256Digest
    knowledge_time_policy_id: _NonEmptyText
    knowledge_time_policy_version: _NonEmptyText
    observed_pit_authority_head: _Sha256Digest
    oos_exposure_id: _NonEmptyText | None
    exposure_fingerprint: _Sha256Digest | None
    exposure_authority_id: _NonEmptyText | None
    exposure_authority_head: _Sha256Digest | None
    code_manifest_policy_id: _NonEmptyText
    code_manifest_policy_version: _NonEmptyText
    code_manifest_policy_hash: _Sha256Digest
    code_manifest_evidence_hash: _Sha256Digest
    code_manifest_authority_id: _NonEmptyText
    code_manifest_authority_head: _Sha256Digest
    start_execution_attestation_hash: _Sha256Digest | None
    terminal_execution_attestation_hash: _Sha256Digest | None
    execution_attestation_authority_id: _NonEmptyText | None
    start_execution_attestation_authority_head: _Sha256Digest | None
    terminal_execution_attestation_authority_head: _Sha256Digest | None
    immutable_artifact_lease_id: _NonEmptyText | None
    immutable_artifact_lease_hash: _Sha256Digest | None
    immutable_artifact_lease_authority_id: _NonEmptyText | None
    immutable_artifact_lease_authority_head: _Sha256Digest | None
    closed_at_us: _PositiveEpochUs
    closure_manifest_hash: _Sha256Digest
    authority_head: _Sha256Digest

    @model_validator(mode="after")
    def _validate_manifest(self) -> "DataSnoopingFamilyManifest":
        _validate_failure_reasons(self.failure_reasons)
        if self.raw_proposal_count < self.unique_performance_hypothesis_count:
            raise ValueError("raw proposal count cannot be smaller than unique hypothesis count")
        if self.global_unique_config_count < self.unique_performance_hypothesis_count:
            raise ValueError(
                "global unique config count cannot be smaller than the family hypothesis subset"
            )
        if self.trial_ledger_proof_hash != self.trial_ledger_proof.proof_hash:
            raise ValueError("trial_ledger_proof_hash must bind the embedded proof")
        if self.global_unique_config_count != self.trial_ledger_proof.global_unique_config_count:
            raise ValueError("global unique config count must match the trial-ledger proof")

        complete_evaluation_evidence = (
            self.attempt_id,
            self.terminal_attempt_event_id,
            self.evaluation_batch_id,
            self.evaluation_batch_receipt_hash,
            self.oos_release_id,
            self.oos_access_attempt_terminal_hash,
            self.oos_release_record_hash,
            self.ordered_period_keys_hash,
            self.candidate_series_set_hash,
            self.benchmark_receipt_hash,
            self.cost_model_input_set_hash,
        )
        oos_execution_evidence = (
            self.oos_exposure_id,
            self.exposure_fingerprint,
            self.exposure_authority_id,
            self.exposure_authority_head,
            self.start_execution_attestation_hash,
            self.execution_attestation_authority_id,
            self.start_execution_attestation_authority_head,
            self.immutable_artifact_lease_id,
            self.immutable_artifact_lease_hash,
            self.immutable_artifact_lease_authority_id,
            self.immutable_artifact_lease_authority_head,
        )

        if self.closure_status == "COMPLETE":
            if self.failure_reasons:
                raise ValueError("a complete family forbids failure reasons")
            if any(value is None for value in complete_evaluation_evidence):
                raise ValueError("a complete family requires exact release and evaluation evidence")
            if any(
                value is None
                for value in (
                    *oos_execution_evidence,
                    self.terminal_execution_attestation_hash,
                    self.terminal_execution_attestation_authority_head,
                )
            ):
                raise ValueError("a complete family requires exact external execution evidence")
            if self.performance_access_attempt_count != 1:
                raise ValueError("a complete one-shot family must have exactly one performance access attempt")
            if not self.ordered_config_hashes or len(self.ordered_config_hashes) != len(
                self.ordered_evaluation_receipt_hashes
            ):
                raise ValueError("a complete family requires one receipt per candidate config")
            if self.ordered_config_hashes != tuple(sorted(self.ordered_config_hashes)) or len(
                set(self.ordered_config_hashes)
            ) != len(self.ordered_config_hashes):
                raise ValueError("candidate configs must be sorted and unique")
            if len(set(self.ordered_evaluation_receipt_hashes)) != len(self.ordered_evaluation_receipt_hashes):
                raise ValueError("candidate evaluation receipts must be unique")
            if self.unique_performance_hypothesis_count != len(self.ordered_config_hashes):
                raise ValueError("candidate config count must equal the frozen hypothesis count")
            if self.winner_config_hash not in self.ordered_config_hashes:
                raise ValueError("winner config must belong to the evaluated candidate set")
            winner_index = self.ordered_config_hashes.index(self.winner_config_hash)
            if self.winner_evaluation_receipt_hash != self.ordered_evaluation_receipt_hashes[winner_index]:
                raise ValueError("winner receipt must correspond to the winner config")
        else:
            if not self.failure_reasons:
                raise ValueError("a non-assessable family requires failure reasons")
            if self.ordered_config_hashes or self.ordered_evaluation_receipt_hashes:
                raise ValueError("a non-assessable family forbids evaluated candidate evidence")
            if self.winner_config_hash is not None or self.winner_evaluation_receipt_hash is not None:
                raise ValueError("a non-assessable family forbids a winner")
            if any(
                value is not None
                for value in (
                    self.evaluation_batch_id,
                    self.evaluation_batch_receipt_hash,
                    self.oos_release_id,
                    self.oos_release_record_hash,
                    self.ordered_period_keys_hash,
                    self.candidate_series_set_hash,
                    self.benchmark_receipt_hash,
                    self.cost_model_input_set_hash,
                )
            ):
                raise ValueError("a non-assessable family forbids completed evaluation evidence")
            pre_oos = self.performance_access_attempt_count == 0
            incomplete_oos = self.performance_access_attempt_count == 1
            if not (pre_oos or incomplete_oos):
                raise ValueError("a non-assessable family permits zero or one access attempt")
            if pre_oos:
                if any(
                    value is not None
                    for value in (
                        self.attempt_id,
                        self.terminal_attempt_event_id,
                        self.oos_access_attempt_terminal_hash,
                        *oos_execution_evidence,
                        self.terminal_execution_attestation_hash,
                        self.terminal_execution_attestation_authority_head,
                    )
                ):
                    raise ValueError("a pre-OOS terminal block forbids OOS execution evidence")
            else:
                required_incomplete = (
                    self.attempt_id,
                    self.terminal_attempt_event_id,
                    self.oos_access_attempt_terminal_hash,
                    *oos_execution_evidence,
                )
                if any(value is None for value in required_incomplete):
                    raise ValueError("an incomplete OOS attempt requires its exact start evidence")
                if (
                    self.terminal_execution_attestation_hash is not None
                    or self.terminal_execution_attestation_authority_head is not None
                ):
                    raise ValueError("an incomplete OOS attempt forbids terminal execution evidence")

        expected_hash = canonical_sha256(
            self.model_dump(mode="python", exclude={"closure_manifest_hash", "authority_head"})
        )
        if self.closure_manifest_hash != expected_hash:
            raise ValueError("closure_manifest_hash does not cover the complete family manifest")
        if self.authority_head != expected_hash:
            raise ValueError("closure authority head must equal the immutable manifest hash")
        return self


class DataSnoopingGridAssessment(_FrozenCanonicalResearchContract):
    record_type: Literal["DATA_SNOOPING_GRID_ASSESSMENT_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    grid_assessment_id: _NonEmptyText
    family_id: _NonEmptyText
    closure_manifest_hash: _Sha256Digest
    algorithm_version: _AssessmentAlgorithmVersion
    period_count: int = Field(ge=20)
    block_length: int = Field(gt=0)
    replication_count: int = Field(gt=0)
    schedule_ordinal: _PositiveOrdinal
    prefix_index_stream_hash: _Sha256Digest
    previous_grid_assessment_hash: _Sha256Digest
    reality_check_result: RealityCheckResult | None
    reality_check_result_hash: _Sha256Digest | None
    spa_result: SpaResult | None
    spa_result_hash: _Sha256Digest | None
    grid_point_status: Literal["RESOLVED", "AMBIGUOUS", "INVALID"]
    grid_point_verdict: Literal[
        "SUPPORTS_SUPERIORITY",
        "DOES_NOT_SUPPORT_SUPERIORITY",
        "METHOD_DISAGREEMENT",
        "PENDING_MORE_REPLICATIONS",
        "MONTE_CARLO_UNRESOLVED",
        "NOT_APPLICABLE",
    ]
    promotion_effect: Literal["NONE"]
    failure_reasons: _FailureReasons
    assessed_at_us: _PositiveEpochUs
    grid_assessment_record_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_grid_assessment(self) -> "DataSnoopingGridAssessment":
        _validate_failure_reasons(self.failure_reasons)
        if self.block_length > self.period_count // 2:
            raise ValueError("grid block_length must not exceed half the period count")
        result_evidence = (
            self.reality_check_result,
            self.reality_check_result_hash,
            self.spa_result,
            self.spa_result_hash,
        )
        if self.grid_point_status == "INVALID":
            if self.grid_point_verdict != "NOT_APPLICABLE" or not self.failure_reasons:
                raise ValueError("an invalid grid assessment requires not-applicable and stable failure reasons")
            if any(value is not None for value in result_evidence):
                raise ValueError("an invalid grid assessment forbids partial or complete kernel evidence")
            if self.grid_assessment_record_hash != canonical_sha256(
                self.model_dump(mode="python", exclude={"grid_assessment_record_hash"})
            ):
                raise ValueError("grid_assessment_record_hash does not cover the complete grid assessment")
            return self
        if self.grid_point_verdict == "NOT_APPLICABLE" or self.failure_reasons:
            raise ValueError("a valid grid assessment requires a local verdict and no failure reasons")
        if any(value is None for value in result_evidence):
            raise ValueError("a valid grid assessment requires both exact statistical kernel results")
        reality = self.reality_check_result
        spa = self.spa_result
        assert reality is not None and spa is not None
        if reality.algorithm_version != "WHITE_REALITY_CHECK_V1" or spa.algorithm_version != "HANSEN_SPA_V1":
            raise ValueError("kernel algorithm versions must match the declared family algorithm")
        if reality.period_count != self.period_count or spa.period_count != self.period_count:
            raise ValueError("both kernel period counts must match the explicit grid assessment period count")
        if reality.block_length != self.block_length or spa.block_length != self.block_length:
            raise ValueError("kernel block length must match the assessed grid point")
        reality_replications = reality.tail_result.replication_count
        spa_replications = {variant.tail_result.replication_count for variant in spa.variants}
        if reality_replications != self.replication_count or spa_replications != {self.replication_count}:
            raise ValueError("kernel replication count must match the assessed schedule point")
        expected_radius = float64_hex(sqrt(self.replication_count) / (self.replication_count + 1))
        tails = (reality.tail_result, *(variant.tail_result for variant in spa.variants))
        if any(tail.conservative_radius != expected_radius for tail in tails):
            raise ValueError("kernel conservative radius must match the published Monte Carlo formula")
        if reality.run_status != spa.run_status:
            raise ValueError("both kernels must share one publication status")
        if reality.run_status == "INVALID":
            raise ValueError("a grid assessment forbids invalid kernel results")
        if reality.run_status == "PUBLISHABLE" and self.replication_count < 10_000:
            raise ValueError("a publishable grid assessment requires at least 10000 replications")
        if reality.run_status == "NON_PUBLISHABLE_TEST_RUN" and self.grid_point_verdict != "PENDING_MORE_REPLICATIONS":
            raise ValueError("a non-publishable checkpoint must remain pending")
        if reality.candidate_order != spa.candidate_order:
            raise ValueError("both kernels must assess the identical ordered candidate set")
        if reality.prefix_hashes != spa.prefix_hashes:
            raise ValueError("both kernels must bind the identical complete prefix-proof sequence")
        for prefix_hashes, label in (
            (reality.prefix_hashes, "reality-check"),
            (spa.prefix_hashes, "SPA"),
        ):
            checkpoint = tuple(prefix for prefix in prefix_hashes if prefix.replication_count == self.replication_count)
            if len(checkpoint) != 1 or checkpoint[0].index_stream_sha256 != self.prefix_index_stream_hash:
                raise ValueError(f"{label} current checkpoint proof must match the grid assessment")

        # The family decision is defined by White RC and the declared primary
        # SPA variant.  LOWER/UPPER remain audited and internally consistent,
        # but their Monte Carlo radius cannot advance the family schedule.
        tails_ambiguous = (
            reality.tail_result.ambiguous
            or spa.variants[1].tail_result.ambiguous
        )
        kernel_decisions = (reality.decision, spa.variants[1].decision)
        if "INVALID" in kernel_decisions:
            raise ValueError("a grid assessment forbids invalid kernel decisions")
        if (reality.decision == "AMBIGUOUS") != reality.tail_result.ambiguous:
            raise ValueError("reality-check decision must match its Monte Carlo ambiguity")
        if any((variant.decision == "AMBIGUOUS") != variant.tail_result.ambiguous for variant in spa.variants):
            raise ValueError("SPA decisions must match their Monte Carlo ambiguity")

        if self.grid_point_status == "AMBIGUOUS":
            if not tails_ambiguous or self.grid_point_verdict not in {
                "PENDING_MORE_REPLICATIONS",
                "MONTE_CARLO_UNRESOLVED",
            }:
                raise ValueError("an ambiguous grid point requires a pending or unresolved verdict")
        elif tails_ambiguous:
            raise ValueError("a resolved grid point forbids ambiguous kernel tails")
        elif reality.run_status == "PUBLISHABLE" and self.grid_point_verdict not in {
            "SUPPORTS_SUPERIORITY",
            "DOES_NOT_SUPPORT_SUPERIORITY",
            "METHOD_DISAGREEMENT",
        }:
            raise ValueError("a resolved publishable grid point requires its exact local kernel verdict")

        if self.grid_point_verdict == "SUPPORTS_SUPERIORITY" and kernel_decisions != (
            "REJECT",
            "REJECT",
        ):
            raise ValueError("superiority requires both RC and consistent SPA to reject")
        if self.grid_point_verdict == "DOES_NOT_SUPPORT_SUPERIORITY" and kernel_decisions != (
            "NON_REJECT",
            "NON_REJECT",
        ):
            raise ValueError("non-superiority requires both RC and consistent SPA not to reject")
        if self.grid_point_verdict == "METHOD_DISAGREEMENT" and set(kernel_decisions) != {
            "REJECT",
            "NON_REJECT",
        }:
            raise ValueError("method disagreement requires exactly one rejecting primary kernel")
        if self.grid_point_verdict == "MONTE_CARLO_UNRESOLVED" and "AMBIGUOUS" not in kernel_decisions:
            raise ValueError("an unresolved verdict requires an ambiguous primary kernel")
        if self.reality_check_result_hash != canonical_sha256(reality):
            raise ValueError("reality_check_result_hash does not match the embedded result")
        if self.spa_result_hash != canonical_sha256(spa):
            raise ValueError("spa_result_hash does not match the embedded result")
        if self.grid_assessment_record_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"grid_assessment_record_hash"})
        ):
            raise ValueError("grid_assessment_record_hash does not cover the complete grid assessment")
        return self


class DataSnoopingAssessment(_FrozenCanonicalResearchContract):
    record_type: Literal["DATA_SNOOPING_ASSESSMENT_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    assessment_id: _NonEmptyText
    family_id: _NonEmptyText
    closure_manifest_hash: _Sha256Digest
    algorithm_version: _AssessmentAlgorithmVersion
    ordered_latest_grid_assessment_hashes: tuple[_Sha256Digest, ...]
    ordered_latest_grid_assessment_hashes_hash: _Sha256Digest
    candidate_order_hash: _Sha256Digest | None
    period_count: Annotated[int, Field(ge=20)] | None
    minimum_replication_count: Annotated[int, Field(gt=0)] | None
    assessment_status: Literal["ASSESSED", "NOT_ASSESSABLE"]
    diagnostic_verdict: Literal[
        "SUPPORTS_SUPERIORITY",
        "DOES_NOT_SUPPORT_SUPERIORITY",
        "METHOD_DISAGREEMENT",
        "MONTE_CARLO_UNRESOLVED",
        "NOT_APPLICABLE",
    ]
    promotion_effect: Literal["NONE"]
    failure_reasons: _FailureReasons
    concluded_at_us: _PositiveEpochUs
    assessment_record_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_assessment(self) -> "DataSnoopingAssessment":
        _validate_failure_reasons(self.failure_reasons)
        if len(set(self.ordered_latest_grid_assessment_hashes)) != len(self.ordered_latest_grid_assessment_hashes):
            raise ValueError("latest grid assessment hashes must be unique")
        if self.ordered_latest_grid_assessment_hashes_hash != canonical_sha256(
            self.ordered_latest_grid_assessment_hashes
        ):
            raise ValueError("ordered_latest_grid_assessment_hashes_hash does not match the canonical sequence")
        if self.assessment_status == "ASSESSED":
            if not self.ordered_latest_grid_assessment_hashes:
                raise ValueError("an assessed family conclusion requires grid assessment evidence")
            if self.diagnostic_verdict == "NOT_APPLICABLE" or self.failure_reasons:
                raise ValueError("an assessed family conclusion requires a diagnostic verdict and no failures")
            if any(
                value is None
                for value in (
                    self.candidate_order_hash,
                    self.period_count,
                    self.minimum_replication_count,
                )
            ):
                raise ValueError("an assessed family conclusion requires all verifier-derived dimensions")
            assert self.minimum_replication_count is not None
            if self.minimum_replication_count < 10_000:
                raise ValueError("an assessed family conclusion requires at least 10000 replications per grid point")
        else:
            if self.diagnostic_verdict != "NOT_APPLICABLE" or not self.failure_reasons:
                raise ValueError(
                    "a non-assessable family conclusion requires not-applicable and stable failure reasons"
                )
            if any(
                value is not None
                for value in (
                    self.candidate_order_hash,
                    self.period_count,
                    self.minimum_replication_count,
                )
            ):
                raise ValueError("a non-assessable family conclusion forbids derived assessed dimensions")

        if self.assessment_record_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"assessment_record_hash"})
        ):
            raise ValueError("assessment_record_hash does not cover the complete family conclusion")
        return self


# C9 is kept in a focused module so the exact-byte publication graph remains
# reviewable.  Re-export it here because ``quantos.contracts.research`` is the
# canonical research-contract import surface.
from .data_snooping_c9 import (  # noqa: E402
    ArtifactCore as ArtifactCore,
    ArtifactCoreScanReceipt as ArtifactCoreScanReceipt,
    ArtifactPublicationReceipt as ArtifactPublicationReceipt,
    ArtifactPublicationReceiptProjection as ArtifactPublicationReceiptProjection,
    ArtifactPublicationProjectionRef as ArtifactPublicationProjectionRef,
    DataSnoopingEvidenceRef as DataSnoopingEvidenceRef,
    PublishedArtifactEnvelope as PublishedArtifactEnvelope,
)
