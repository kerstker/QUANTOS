"""Replayable validation evidence shared by research and alpha domains."""

from __future__ import annotations

import math
import statistics
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .canonical import canonical_sha256
from .hashing import stable_hash


_VAR_SR_FLOOR = 1e-4


def _is_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class PboAlignment(str, Enum):
    EVENT_TIME_INTERSECTION = "EVENT_TIME_INTERSECTION"
    POSITIONAL_LEGACY = "POSITIONAL_LEGACY"


class BatchPboEvidence(BaseModel):
    """Replayable CSCV input retained with each evaluated candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[2] = 2
    algorithm: Literal["CSCV_V1"] = "CSCV_V1"
    candidate_definition_hashes: tuple[str, ...]
    performance_matrix: tuple[tuple[float, ...], ...]
    alignment: PboAlignment
    aligned_period_count: int = Field(ge=4)
    aligned_event_times: tuple[int, ...] = ()
    aligned_event_times_sha256: str = Field(min_length=64, max_length=64)
    pbo: float = Field(ge=0.0, le=1.0)
    identity_commitment: str = Field(min_length=64, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_schema(cls, value):
        if isinstance(value, dict) and value.get("schema_version", 2) != 2:
            raise ValueError("unsupported PBO evidence schema_version; expected 2")
        return value

    @model_validator(mode="after")
    def _validate_evidence_shape(self) -> "BatchPboEvidence":
        hashes = self.candidate_definition_hashes
        if len(hashes) < 2 or len(set(hashes)) != len(hashes):
            raise ValueError(
                "PBO evidence requires at least two distinct candidate definitions"
            )
        if any(not _is_sha256(value) for value in hashes):
            raise ValueError(
                "candidate definition hashes must be SHA-256 hex digests"
            )
        rows = self.performance_matrix
        if not (2 <= len(rows) <= 8):
            raise ValueError(
                "PBO performance matrix must contain between 2 and 8 blocks"
            )
        if any(len(row) != len(hashes) for row in rows):
            raise ValueError(
                "PBO performance matrix must be rectangular and candidate-bound"
            )
        if any(not math.isfinite(value) for row in rows for value in row):
            raise ValueError(
                "PBO performance matrix must contain only finite values"
            )
        if len(rows) > self.aligned_period_count:
            raise ValueError("PBO block count cannot exceed aligned period count")

        if self.alignment == PboAlignment.EVENT_TIME_INTERSECTION:
            times = self.aligned_event_times
            if (
                len(times) != self.aligned_period_count
                or any(value < 0 for value in times)
                or tuple(sorted(set(times))) != times
            ):
                raise ValueError(
                    "aligned event times must be exact, sorted, unique, and complete"
                )
            expected_calendar_hash = stable_hash(list(times))
        else:
            if self.aligned_event_times:
                raise ValueError(
                    "positional legacy evidence cannot claim event-time alignment"
                )
            expected_calendar_hash = stable_hash(
                {
                    "alignment": self.alignment.value,
                    "aligned_period_count": self.aligned_period_count,
                }
            )
        if self.aligned_event_times_sha256 != expected_calendar_hash:
            raise ValueError("aligned event-time digest mismatch")
        if self.identity_commitment != stable_hash({
            "candidate_definition_hashes": list(self.candidate_definition_hashes),
            "performance_matrix": [list(row) for row in self.performance_matrix],
            "alignment": self.alignment.value,
            "aligned_period_count": self.aligned_period_count,
            "aligned_event_times": list(self.aligned_event_times),
            "pbo": self.pbo,
        }):
            raise ValueError("PBO identity commitment mismatch")
        return self

    @property
    def evidence_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class CatalogSharpeObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    observation_id: str = Field(min_length=1)
    sharpe: float

    @model_validator(mode="after")
    def _finite_sharpe(self) -> "CatalogSharpeObservation":
        if not math.isfinite(self.sharpe):
            raise ValueError("catalog Sharpe observations must be finite")
        return self


class CatalogReturnObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    observation_id: str = Field(min_length=1)
    observation_count: int = Field(ge=2)
    returns: tuple[float, ...]
    returns_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def _valid_digest(self) -> "CatalogReturnObservation":
        if not _is_sha256(self.returns_sha256):
            raise ValueError("catalog return observations require SHA-256 digests")
        if len(self.returns) != self.observation_count:
            raise ValueError("catalog return observation count mismatch")
        if any(not math.isfinite(value) for value in self.returns):
            raise ValueError("catalog returns must contain only finite values")
        if self.returns_sha256 != canonical_sha256(list(self.returns)):
            raise ValueError("catalog return observation digest mismatch")
        return self


class CatalogCorrelationObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    left_id: str = Field(min_length=1)
    right_id: str = Field(min_length=1)
    aligned_observation_count: int = Field(ge=2)
    abs_correlation: float = Field(ge=0.0, le=1.0)
    alignment: Literal["EQUAL_LENGTH_POSITIONAL"] = "EQUAL_LENGTH_POSITIONAL"

    @model_validator(mode="after")
    def _canonical_pair(self) -> "CatalogCorrelationObservation":
        if self.left_id >= self.right_id:
            raise ValueError("catalog correlation pairs must be canonical")
        if not math.isfinite(self.abs_correlation):
            raise ValueError("catalog correlations must be finite")
        return self


class CatalogEvidence(BaseModel):
    """Exact inputs and output for catalog-wide Deflated Sharpe.

    Unknown return histories contribute zero correlation and therefore remain
    independent trials. This is deliberately conservative: missing evidence can
    never reduce the multiple-testing penalty.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    algorithm: Literal["DSR_EFFECTIVE_TRIALS_V1"] = (
        "DSR_EFFECTIVE_TRIALS_V1"
    )
    subject_id: str = Field(min_length=1)
    subject_definition_hash: str = Field(min_length=64, max_length=64)
    subject_returns_sha256: str = Field(min_length=64, max_length=64)
    n_obs: int = Field(ge=2)
    trial_definition_hashes: tuple[str, ...]
    trial_count: int = Field(ge=2)
    effective_trial_count: int = Field(ge=1)
    sharpe_observations: tuple[CatalogSharpeObservation, ...]
    return_observations: tuple[CatalogReturnObservation, ...]
    pairwise_abs_correlations: tuple[CatalogCorrelationObservation, ...] = ()
    mean_abs_correlation: float = Field(ge=0.0, le=1.0)
    var_sr: float = Field(gt=0.0)
    observed_sr: float
    catalog_trial_set_hash: str = Field(min_length=64, max_length=64)
    catalog_snapshot_hash: str = Field(min_length=64, max_length=64)
    deflated_sharpe: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_catalog_inputs(self) -> "CatalogEvidence":
        if (
            not _is_sha256(self.subject_definition_hash)
            or not _is_sha256(self.subject_returns_sha256)
            or not _is_sha256(self.catalog_trial_set_hash)
            or not _is_sha256(self.catalog_snapshot_hash)
        ):
            raise ValueError("catalog evidence digests must be SHA-256 hex")
        trials = self.trial_definition_hashes
        if (
            len(trials) != self.trial_count
            or tuple(sorted(set(trials))) != trials
            or any(not _is_sha256(value) for value in trials)
        ):
            raise ValueError(
                "catalog trial definitions must be exact, sorted, and distinct"
            )
        if self.subject_definition_hash not in trials:
            raise ValueError("catalog evidence subject is not in the trial ledger")

        sharpes = self.sharpe_observations
        sharpe_ids = tuple(item.observation_id for item in sharpes)
        if len(sharpes) < 2 or tuple(sorted(set(sharpe_ids))) != sharpe_ids:
            raise ValueError(
                "catalog Sharpe population must contain two sorted distinct rows"
            )
        sharpe_by_id = {
            item.observation_id: item.sharpe for item in sharpes
        }
        if self.subject_definition_hash not in sharpe_by_id:
            raise ValueError("catalog subject has no Sharpe observation")
        if not math.isclose(
            self.observed_sr,
            sharpe_by_id[self.subject_definition_hash],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("observed Sharpe does not match the catalog subject")
        expected_var_sr = max(
            statistics.pvariance(item.sharpe for item in sharpes),
            _VAR_SR_FLOOR,
        )
        if not math.isclose(
            self.var_sr, expected_var_sr, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError("catalog Sharpe variance mismatch")

        returns = self.return_observations
        return_ids = tuple(item.observation_id for item in returns)
        if tuple(sorted(set(return_ids))) != return_ids:
            raise ValueError(
                "catalog return observations must be sorted and distinct"
            )
        return_by_id = {item.observation_id: item for item in returns}
        subject_returns = return_by_id.get(self.subject_definition_hash)
        if (
            subject_returns is None
            or subject_returns.observation_count != self.n_obs
            or subject_returns.returns_sha256 != self.subject_returns_sha256
        ):
            raise ValueError("subject return observation mismatch")

        correlations = self.pairwise_abs_correlations
        pair_keys = tuple((item.left_id, item.right_id) for item in correlations)
        if tuple(sorted(set(pair_keys))) != pair_keys:
            raise ValueError(
                "catalog correlation observations must be sorted and distinct"
            )
        if any(
            item.left_id not in return_by_id
            or item.right_id not in return_by_id
            or return_by_id[item.left_id].observation_count
            != item.aligned_observation_count
            or return_by_id[item.right_id].observation_count
            != item.aligned_observation_count
            for item in correlations
        ):
            raise ValueError(
                "catalog correlations must bind equal-length return observations"
            )
        correlated_ids = {
            observation_id
            for item in correlations
            for observation_id in (item.left_id, item.right_id)
        }
        if not correlated_ids <= set(trials):
            raise ValueError("catalog correlations must bind catalog trials")
        correlated_count = len(correlated_ids)
        comparable_pairs = correlated_count * (correlated_count - 1) / 2.0
        expected_mean_correlation = (
            sum(item.abs_correlation for item in correlations)
            / comparable_pairs
            if comparable_pairs
            else 0.0
        )
        if not math.isclose(
            self.mean_abs_correlation,
            expected_mean_correlation,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("catalog mean absolute correlation mismatch")
        if correlated_count:
            effective_correlated = correlated_count / (
                1.0
                + (correlated_count - 1) * expected_mean_correlation
            )
            expected_effective_count = (
                self.trial_count
                - correlated_count
                + math.ceil(effective_correlated - 1e-12)
            )
        else:
            expected_effective_count = self.trial_count
        if self.effective_trial_count != expected_effective_count:
            raise ValueError("catalog effective trial count mismatch")

        if self.catalog_trial_set_hash != stable_hash(list(trials)):
            raise ValueError("catalog trial-set digest mismatch")
        expected_snapshot_hash = canonical_sha256(
            {
                "trial_definition_hashes": list(trials),
                "sharpe_observations": [
                    item.model_dump(mode="json") for item in sharpes
                ],
                "return_observations": [
                    item.model_dump(mode="json") for item in returns
                ],
                "pairwise_abs_correlations": [
                    item.model_dump(mode="json") for item in correlations
                ],
            }
        )
        if self.catalog_snapshot_hash != expected_snapshot_hash:
            raise ValueError("catalog snapshot digest mismatch")
        if not math.isfinite(self.observed_sr):
            raise ValueError("catalog observed Sharpe must be finite")
        return self

    @property
    def evidence_hash(self) -> str:
        return canonical_sha256(self)
