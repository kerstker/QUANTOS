"""Deterministic structural alpha-family identity and candidate deduplication."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from services.research.alpha_identity import AlphaDefinition, alpha_id


_TUNABLE_OPS = {"signed_power"}
# alpha_identity's grammar is the authority; these are the operators whose final
# numeric argument is a tunable window rather than economic structure.
_TUNABLE_WINDOW_OPS = {
    "ts_delay", "ts_delta", "ts_mean", "ts_sum", "ts_std", "ts_min", "ts_max",
    "ts_rank", "decay_linear", "ts_argmax", "ts_argmin", "ts_corr", "ts_cov",
    "ts_beta", "ts_skew", "ts_kurt", "ts_zscore", "cs_rank", "cs_zscore",
    "cs_neutralize", "cs_winsorize", "ew_mean", "ew_std", "ew_zscore",
}


def _structure(expr: Any) -> Any:
    """Canonical expression with only tunable numeric magnitudes erased."""
    op = expr[0]
    if op == "field":
        return ("field", expr[1])
    if op == "const":
        return ("const",)  # constant magnitude is tunable; const-vs-field stays distinct
    if op == "signed_power":
        exponent = expr[2]
        # Preserve direction (sign), while ignoring tunable magnitude.
        sign = -1 if exponent < 0 else 1 if exponent > 0 else 0
        return (op, _structure(expr[1]), ("exponent-sign", sign))
    if op in _TUNABLE_WINDOW_OPS:
        return (op, *(_structure(child) for child in expr[1:-1]), "window")
    return (op, *(_structure(child) if isinstance(child, tuple) else child for child in expr[1:]))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _jsonable(value[k]) for k in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def family_id(definition: AlphaDefinition) -> str:
    """Return the full lowercase SHA-256 ID for an economically structural family."""
    if not isinstance(definition, AlphaDefinition):
        raise TypeError("expected AlphaDefinition")
    payload = {
        "definition": _structure(definition.definition),
        "feature_contract": definition.feature_contract,
        "data_schema": definition.data_schema,
        "universe_policy": definition.universe_policy,
        "horizon": definition.horizon,
        "cost_model": definition.cost_model,
        "validation_policy": definition.validation_policy,
        "model": definition.model,
        "model_version": definition.model_version,
    }
    encoded = json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AlphaCandidate:
    definition: AlphaDefinition
    signal_fingerprint: tuple[Any, ...] | None = None
    return_fingerprint: tuple[Any, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.definition, AlphaDefinition):
            raise TypeError("definition must be AlphaDefinition")
        for name in ("signal_fingerprint", "return_fingerprint"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, tuple(value))

    @property
    def alpha_id(self) -> str:
        return alpha_id(self.definition)

    @property
    def family_id(self) -> str:
        return family_id(self.definition)

    @property
    def fingerprint(self) -> tuple[Any, ...] | None:
        """Preferred deterministic behavioral fingerprint (returns, then signal)."""
        return self.return_fingerprint if self.return_fingerprint is not None else self.signal_fingerprint


# Short compatibility name for callers that prefer the domain term.
Candidate = AlphaCandidate


@dataclass(frozen=True, slots=True)
class DuplicateRelationship:
    duplicate_alpha_id: str
    representative_alpha_id: str
    kind: str  # exact or behavioral
    family_id_a: str
    family_id_b: str
    correlation: float | None = None


@dataclass(frozen=True, slots=True)
class DedupResult:
    representatives: tuple[AlphaCandidate, ...]
    exact_duplicates: tuple[DuplicateRelationship, ...]
    behavioral_duplicates: tuple[DuplicateRelationship, ...]

    @property
    def relationships(self) -> tuple[DuplicateRelationship, ...]:
        return self.exact_duplicates + self.behavioral_duplicates


def _correlation(left: tuple[Any, ...] | None, right: tuple[Any, ...] | None) -> float | None:
    if left is None or right is None or len(left) != len(right) or len(left) < 2:
        return None
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in left + right):
        return None
    ml, mr = sum(left) / len(left), sum(right) / len(right)
    dl = [x - ml for x in left]
    dr = [x - mr for x in right]
    sl = sum(x * x for x in dl)
    sr = sum(x * x for x in dr)
    if sl == 0 or sr == 0:
        return None
    return sum(x * y for x, y in zip(dl, dr)) / math.sqrt(sl * sr)


def deduplicate_candidates(candidates: Iterable[AlphaCandidate], *, correlation_threshold: float) -> DedupResult:
    if type(correlation_threshold) not in (int, float) or not math.isfinite(correlation_threshold):
        raise ValueError("correlation_threshold must be finite")
    if not 0 <= correlation_threshold < 1:
        raise ValueError("correlation_threshold must be in [0, 1)")
    items = tuple(candidates)
    if any(not isinstance(c, AlphaCandidate) for c in items):
        raise TypeError("candidates must contain AlphaCandidate values")
    by_id: dict[str, AlphaCandidate] = {}
    exact: list[DuplicateRelationship] = []
    for candidate in items:
        aid = candidate.alpha_id
        prior = by_id.get(aid)
        if prior is None:
            by_id[aid] = candidate
        else:
            rep = min(prior, candidate, key=lambda c: c.alpha_id)
            by_id[aid] = rep
            exact.append(DuplicateRelationship(aid, rep.alpha_id, "exact", candidate.family_id, rep.family_id))
    reps = tuple(sorted(by_id.values(), key=lambda c: c.alpha_id))
    behavioral: list[DuplicateRelationship] = []
    for index, left in enumerate(reps):
        for right in reps[index + 1:]:
            corr = _correlation(left.fingerprint, right.fingerprint)
            if corr is None or abs(corr) < correlation_threshold:
                continue
            behavioral.append(DuplicateRelationship(
                right.alpha_id, left.alpha_id, "behavioral", left.family_id, right.family_id, corr))
    return DedupResult(reps, tuple(sorted(exact, key=lambda r: (r.duplicate_alpha_id, r.representative_alpha_id))),
                       tuple(behavioral))
