"""Candidate deduplicator (727 §8.3 layer 3) — canonicalize / deduplicate.

Two reject strengths, both deterministic:
- EXACT_DUPLICATE: the candidate's expression_hash was already seen. This is
  the §8.3 dedup layer's hard case and the judgement source a caller records
  as TrialStatus.ELIMINATED_DUPLICATE in the TrialRecord.
- NEAR_DUPLICATE: structural similarity (services/alpha/similarity.py's
  shared-subtree Jaccard) against a seen expression meets `near_threshold`.
  The 0.85 default is UNCALIBRATED — an engineering default, not a tuned
  research finding; it is recorded here so audits can see the knob.

`filter_batch` keeps FIRST-SEEN candidates and rejects later duplicates — the
same first-occurrence semantics as run_alpha_factory._pending, so a family
routed through either path dedups identically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from services.alpha.similarity import expression_similarity
from services.research.alpha_expression import alpha_id, canonicalize

from .candidate_compiler import CompiledCandidate


class DeduplicatorError(ValueError):
    """The deduplicator was configured or called with invalid inputs."""


@dataclass(frozen=True)
class DedupDecision:
    """One dedup judgement. `matched_expression_hash` is "" for KEEP."""

    verdict: str  # "KEEP" | "EXACT_DUPLICATE" | "NEAR_DUPLICATE"
    matched_expression_hash: str
    reason: str


class CandidateDeduplicator:
    """Stateful-rule, stateless-store deduplicator: the caller owns the seen set."""

    def __init__(self, *, near_threshold: float = 0.85) -> None:
        if isinstance(near_threshold, bool) or not isinstance(
            near_threshold, (int, float)
        ):
            raise DeduplicatorError("near_threshold must be a real number")
        threshold = float(near_threshold)
        # UNCALIBRATED engineering default (see module docstring).
        if not math.isfinite(threshold) or threshold <= 0.0 or threshold > 1.0:
            raise DeduplicatorError("near_threshold must be in (0, 1]")
        self._near_threshold = threshold

    @property
    def near_threshold(self) -> float:
        return self._near_threshold

    def check(
        self,
        candidate: CompiledCandidate,
        *,
        seen_hashes: set[str],
        seen_expressions: Sequence[tuple] = (),
    ) -> DedupDecision:
        """Judge one candidate against the seen population. Deterministic:
        near-dup takes the HIGHEST similarity, ties broken by earliest seen."""
        if not isinstance(candidate, CompiledCandidate):
            raise DeduplicatorError("candidate must be a CompiledCandidate")
        if not isinstance(seen_hashes, set):
            raise DeduplicatorError("seen_hashes must be a set")
        if candidate.expression_hash in seen_hashes:
            return DedupDecision(
                verdict="EXACT_DUPLICATE",
                matched_expression_hash=candidate.expression_hash,
                reason="expression_hash already seen",
            )
        best_similarity = -1.0
        best_hash = ""
        for seen in seen_expressions:
            canonical_seen = canonicalize(seen)
            similarity = expression_similarity(candidate.expression, canonical_seen)
            if similarity > best_similarity:
                best_similarity = similarity
                best_hash = alpha_id(canonical_seen)
        if best_similarity >= self._near_threshold:
            return DedupDecision(
                verdict="NEAR_DUPLICATE",
                matched_expression_hash=best_hash,
                reason=(
                    f"expression_similarity {best_similarity:.6f} "
                    f">= near_threshold {self._near_threshold:.6f}"
                ),
            )
        return DedupDecision(verdict="KEEP", matched_expression_hash="", reason="")

    def filter_batch(
        self, candidates: Sequence[CompiledCandidate]
    ) -> tuple[list[CompiledCandidate], list[tuple[CompiledCandidate, DedupDecision]]]:
        """First-seen KEEP, later duplicates judged against the accumulated
        survivors. Returns (kept, rejected[(candidate, decision)]) in input order."""
        kept: list[CompiledCandidate] = []
        rejected: list[tuple[CompiledCandidate, DedupDecision]] = []
        seen_hashes: set[str] = set()
        seen_expressions: list[tuple] = []
        for candidate in candidates:
            decision = self.check(
                candidate,
                seen_hashes=seen_hashes,
                seen_expressions=seen_expressions,
            )
            if decision.verdict == "KEEP":
                kept.append(candidate)
                seen_hashes.add(candidate.expression_hash)
                seen_expressions.append(candidate.expression)
            else:
                rejected.append((candidate, decision))
        return kept, rejected


__all__ = [
    "CandidateDeduplicator",
    "DedupDecision",
    "DeduplicatorError",
]
