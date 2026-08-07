"""Deterministic structural alpha-family identity and candidate deduplication."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from services.research.alpha_identity import AlphaDefinition, alpha_id
from services.research.alpha_expression import _TS_PAIR, _TS_WINDOW


# Only these grammar operators have a final numeric window argument.
_TUNABLE_WINDOW_OPS = frozenset(_TS_WINDOW) | frozenset(_TS_PAIR)


def _structure(expr: Any) -> Any:
    """Canonical expression with only tunable numeric magnitudes erased."""
    op = expr[0]
    if op == "field":
        return ("field", expr[1])
    if op == "const":
        value = expr[1]
        sign = -1 if value < 0 else 1 if value > 0 else 0
        return ("const", ("sign", sign))  # magnitude is tunable; direction is structural
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
            if value is None:
                continue
            if type(value) not in (list, tuple):
                raise TypeError(f"{name} must be a flat numeric sequence or None")
            frozen = []
            for item in value:
                if type(item) not in (int, float) or not math.isfinite(item):
                    raise ValueError(f"{name} must contain only finite numeric non-bools")
                frozen.append(item)
            object.__setattr__(self, name, tuple(frozen))

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


def _correlation(left: tuple[float, ...] | None, right: tuple[float, ...] | None) -> float | None:
    if left is None or right is None or len(left) != len(right) or len(left) < 2:
        return None
    ml, mr = sum(left) / len(left), sum(right) / len(right)
    dl = [x - ml for x in left]
    dr = [x - mr for x in right]
    sl = sum(x * x for x in dl)
    sr = sum(x * x for x in dr)
    if sl == 0 or sr == 0:
        return None
    return sum(x * y for x, y in zip(dl, dr)) / math.sqrt(sl * sr)


def _same_domain_correlation(left: AlphaCandidate, right: AlphaCandidate) -> float | None:
    """Compare returns only when both have returns; otherwise signals only when both do."""
    if left.return_fingerprint is not None and right.return_fingerprint is not None:
        return _correlation(left.return_fingerprint, right.return_fingerprint)
    if left.signal_fingerprint is not None and right.signal_fingerprint is not None:
        return _correlation(left.signal_fingerprint, right.signal_fingerprint)
    return None


def _fingerprint_key(value: tuple[float, ...] | None) -> tuple:
    return (0,) if value is None else (1, tuple(value))


def _candidate_key(candidate: AlphaCandidate) -> tuple:
    return (candidate.alpha_id, _fingerprint_key(candidate.return_fingerprint),
            _fingerprint_key(candidate.signal_fingerprint))


def deduplicate_candidates(candidates: Iterable[AlphaCandidate], *, correlation_threshold: float) -> DedupResult:
    if type(correlation_threshold) not in (int, float):
        raise TypeError("correlation_threshold must be a finite int or float")
    if not math.isfinite(correlation_threshold):
        raise ValueError("correlation_threshold must be finite")
    if not 0 <= correlation_threshold <= 1:
        raise ValueError("correlation_threshold must be in [0, 1]")
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
            rep = min(prior, candidate, key=_candidate_key)
            by_id[aid] = rep
            exact.append(DuplicateRelationship(aid, rep.alpha_id, "exact", rep.family_id, rep.family_id))
    reps = tuple(sorted(by_id.values(), key=_candidate_key))
    behavioral: list[DuplicateRelationship] = []
    for index, left in enumerate(reps):
        for right in reps[index + 1:]:
            corr = _same_domain_correlation(left, right)
            if corr is None or corr < correlation_threshold:
                continue
            behavioral.append(DuplicateRelationship(
                right.alpha_id, left.alpha_id, "behavioral", left.family_id, right.family_id, corr))
    return DedupResult(reps, tuple(sorted(exact, key=lambda r: (r.duplicate_alpha_id, r.representative_alpha_id,
                                                                  r.family_id_a, r.family_id_b, r.correlation or 0))),
                       tuple(behavioral))
