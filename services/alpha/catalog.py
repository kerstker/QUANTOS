"""Alpha catalog (Phase 6) — the append-only registry that owns the catalog-wide
multiple-testing accounting.

Two numbers make the whole factory honest and are owned HERE, fed together to
Deflated Sharpe (critique P0-1/P0-2):
  * global_trial_count(): monotonic count of DISTINCT configs ever evaluated
    (re-runs of an identical config do not inflate it; RETIRED/REJECTED still
    count — honest multiple testing).
  * catalog_var_sr(): variance of the per-observation Sharpe across the WHOLE
    candidate population (not a local batch), floored.

Survivorship-free: RETIRED/REJECTED alphas remain queryable.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import AlphaState

_VAR_SR_FLOOR = 1e-4


@dataclass(frozen=True)
class AlphaStateRow:
    alpha_id: str
    definition: AlphaDefinition
    state: AlphaState
    returns: tuple[float, ...]
    pvalue: Optional[float]


@dataclass(frozen=True)
class CatalogSnapshot:
    """A plain, serializable copy of AlphaCatalog state. `trials` is the FULL
    distinct-config population (a superset of the alphas' own definition hashes),
    so the trial count round-trips exactly."""
    trials: tuple[str, ...]
    alphas: tuple[AlphaStateRow, ...]


class AlphaCatalog:
    def __init__(
        self,
        *,
        trial_recorder: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._defs: dict[str, AlphaDefinition] = {}
        self._state: dict[str, AlphaState] = {}
        self._returns: dict[str, tuple[float, ...]] = {}
        self._sharpes: dict[str, float] = {}
        self._pvalues: dict[str, float] = {}
        self._historical_returns: dict[str, tuple[float, ...]] = {}
        self._historical_definition_hashes: dict[str, str] = {}
        self._historical_sharpes: dict[str, float] = {}
        self._historical_pvalues: dict[str, float] = {}
        self._seen_configs: set[str] = set()
        self._trial_count: int = 0
        self._trial_recorder = trial_recorder

    # --- registration & multiple-testing accounting --------------------------

    def register(self, definition: AlphaDefinition) -> None:
        # Always count by the definition's own hash — no caller-supplied override,
        # which could collide distinct specs to one hash and undercount trials
        # (review P0). definition_hash is derived from factor_ids/universe/params.
        aid = definition.alpha_id
        if aid in self._defs:
            raise ValueError(f"alpha already registered: {aid}")
        if definition.definition_hash not in self._seen_configs:
            self.register_trial(definition.definition_hash)
        self._defs[aid] = definition
        self._state[aid] = AlphaState.DRAFT

    def register_trial(self, config_hash: str) -> None:
        """Count a never-seen config exactly once; monotonic, never decremented."""
        if self._trial_recorder is not None:
            self._trial_recorder(config_hash)
        if config_hash not in self._seen_configs:
            self._seen_configs.add(config_hash)
            self._trial_count += 1

    def global_trial_count(self) -> int:
        return self._trial_count

    def trial_set_hash(self) -> str:
        """Digest the complete monotonic distinct-config population.

        Count-only guards catch catalog growth but not a same-size substituted set.
        The digest lets evaluation and lifecycle evidence bind the exact population.
        """

        return stable_hash(sorted(self._seen_configs))

    def catalog_var_sr(self) -> Optional[float]:
        """Population variance of per-observation Sharpe across the catalog.

        Returns None when fewer than 2 alphas have returns: there is NO honest
        cross-trial variance estimate yet, and silently flooring it to 1e-4 would
        UN-deflate Deflated Sharpe and let a noise alpha pass (review P0). The
        caller must route to human review instead of fabricating a tiny var_sr.
        """
        vals = list({**self._historical_sharpes, **self._sharpes}.values())
        if len(vals) < 2:
            return None
        return max(statistics.pstdev(vals) ** 2, _VAR_SR_FLOOR)

    # --- per-alpha data ------------------------------------------------------

    def set_returns(self, alpha_id: str, returns: Sequence[float]) -> None:
        rs = tuple(float(value) for value in returns)
        if any(not math.isfinite(value) for value in rs):
            raise ValueError("catalog returns must contain only finite values")
        self._returns[alpha_id] = rs
        self._sharpes.pop(alpha_id, None)
        if len(rs) > 1:
            sd = statistics.pstdev(rs)
            if sd > 0:
                self._sharpes[alpha_id] = statistics.fmean(rs) / sd

    def returns(self, alpha_id: str) -> tuple[float, ...]:
        return self._returns.get(alpha_id, ())

    def evidence(self, alpha_id: str):
        """Mint replayable catalog-wide DSR evidence for one current alpha.

        The catalog owns both the monotonic trial ledger and all exact OOS return
        observations.  Callers can consume this frozen value but cannot supply a
        local trial-count/variance override.  Historical observations are keyed
        by their definition hash; a current re-evaluation replaces the matching
        historical observation rather than double-counting the same hypothesis.
        """

        if alpha_id not in self._defs or alpha_id not in self._returns:
            return None
        if alpha_id not in self._sharpes:
            return None
        if len({**self._historical_sharpes, **self._sharpes}) < 2:
            return None

        return_series_by_definition: dict[str, tuple[float, ...]] = {}
        for historical_id, values in self._historical_returns.items():
            definition_hash = self._historical_definition_hashes.get(
                historical_id
            )
            if definition_hash is not None:
                return_series_by_definition[definition_hash] = values
        for current_id in sorted(self._returns):
            definition = self._defs.get(current_id)
            if definition is None:
                continue
            definition_hash = definition.definition_hash
            values = self._returns[current_id]
            existing = return_series_by_definition.get(definition_hash)
            if existing is not None and existing != values:
                raise ValueError(
                    "one catalog definition has conflicting OOS return series"
                )
            return_series_by_definition[definition_hash] = values

        from services.validation.catalog_evidence import create_catalog_evidence

        definition_hash = self._defs[alpha_id].definition_hash
        return create_catalog_evidence(
            subject_id=alpha_id,
            subject_definition_hash=definition_hash,
            trial_definition_hashes=tuple(sorted(self._seen_configs)),
            return_series_by_definition=return_series_by_definition,
        )

    def set_pvalue(self, alpha_id: str, pvalue: Optional[float]) -> None:
        if pvalue is not None:
            self._pvalues[alpha_id] = float(pvalue)

    def bh_fdr_survivors(self, q: float = 0.10) -> set[str]:
        """Alpha ids whose IC p-value survives catalog-wide Benjamini-Hochberg FDR
        at level q. The second, independent edge lens (alongside DSR)."""
        from .multiple_testing import benjamini_hochberg
        pvalues = {**self._historical_pvalues, **self._pvalues}
        ids = sorted(pvalues)
        survivors = benjamini_hochberg([pvalues[a] for a in ids], q=q)
        return {ids[i] for i in survivors}

    def bhy_fdr_survivors(self, q: float = 0.10) -> set[str]:
        """Alpha ids whose IC p-value survives catalog-wide Benjamini-Hochberg-YEKUTIELI FDR at
        level q — the arbitrary-DEPENDENCE FDR control (stricter than plain BH), the correct lens
        when the catalog's edges are correlated (which they are). Wired into the VALIDATED guard
        (Rank #12); BHY was implemented but unused before."""
        from .multiple_testing import benjamini_hochberg_yekutieli
        pvalues = {**self._historical_pvalues, **self._pvalues}
        ids = sorted(pvalues)
        survivors = benjamini_hochberg_yekutieli([pvalues[a] for a in ids], q=q)
        return {ids[i] for i in survivors}

    # --- state (low-level; the lifecycle manager is the validated chokepoint) -

    def get(self, alpha_id: str) -> AlphaDefinition:
        return self._defs[alpha_id]

    def state(self, alpha_id: str) -> AlphaState:
        return self._state[alpha_id]

    def _set_state(self, alpha_id: str, state: AlphaState) -> None:
        """INTERNAL — performs NO validation. The AlphaLifecycleManager (the
        validated, audited chokepoint) is the ONLY component that should call this
        during operation; test/seed setup may use it to construct fixtures. A
        production state change MUST go through AlphaLifecycleManager.advance() —
        calling this directly bypasses the state machine, guards, and audit trail
        (see the threat-model note in CLAUDE.md: in-process trusted code is governed
        by code review + the governance scanner, not by this runtime check)."""
        self._state[alpha_id] = state

    def all_ids(self) -> list[str]:
        return sorted(self._defs)

    def by_state(self, state: AlphaState) -> list[str]:
        return sorted(aid for aid, s in self._state.items() if s == state)

    def approved_set(self) -> dict[str, tuple[float, ...]]:
        """Approved alphas' return series (the orthogonalization basis)."""
        return {aid: self._returns[aid] for aid in self.by_state(AlphaState.APPROVED) if aid in self._returns}

    # --- persistence port (snapshot/restore; no I/O — see experiment_registry) -

    def snapshot(self) -> CatalogSnapshot:
        rows = tuple(
            AlphaStateRow(
                alpha_id=aid,
                definition=self._defs[aid],
                state=self._state[aid],
                returns=self._returns.get(aid, ()),
                pvalue=self._pvalues.get(aid),
            )
            for aid in self.all_ids()
        )
        return CatalogSnapshot(trials=tuple(sorted(self._seen_configs)), alphas=rows)

    def restore(self, snap: CatalogSnapshot) -> None:
        """Replace in-memory state from a snapshot. Sharpe is RECOMPUTED from
        returns via set_returns (never trusted from outside), so catalog_var_sr is
        consistent with the persisted returns."""
        self._defs = {}
        self._state = {}
        self._returns = {}
        self._sharpes = {}
        self._pvalues = {}
        self._historical_returns = {}
        self._historical_definition_hashes = {}
        self._historical_sharpes = {}
        self._historical_pvalues = {}
        self._seen_configs = set(snap.trials)
        self._trial_count = len(self._seen_configs)
        for row in snap.alphas:
            self._defs[row.alpha_id] = row.definition
            self._state[row.alpha_id] = row.state
            if row.returns:
                self.set_returns(row.alpha_id, row.returns)   # recomputes _sharpes
            if row.pvalue is not None:
                self._pvalues[row.alpha_id] = float(row.pvalue)

    def restore_evaluation_context(
        self,
        *,
        trials: Sequence[str],
        historical_returns: Mapping[str, Sequence[float]],
        historical_pvalues: Mapping[str, float],
        historical_definition_hashes: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Restore multiple-testing state without stale lifecycle identities.

        A new dataset must re-evaluate the same canonical alpha IDs, while DSR and
        FDR still see prior trials and statistics. Historical rows remain
        statistical context only; current evaluations replace their value by ID.
        """
        self._defs = {}
        self._state = {}
        self._returns = {}
        self._sharpes = {}
        self._pvalues = {}
        self._seen_configs = set(trials)
        self._trial_count = len(self._seen_configs)
        self._historical_returns = {}
        self._historical_definition_hashes = dict(
            historical_definition_hashes or {}
        )
        self._historical_sharpes = {}
        for alpha_id, values in historical_returns.items():
            returns = tuple(float(value) for value in values)
            if any(not math.isfinite(value) for value in returns):
                raise ValueError("historical catalog returns must be finite")
            self._historical_returns[alpha_id] = returns
            if len(returns) > 1:
                standard_deviation = statistics.pstdev(returns)
                if standard_deviation > 0:
                    self._historical_sharpes[alpha_id] = (
                        statistics.fmean(returns) / standard_deviation
                    )
        self._historical_pvalues = {
            alpha_id: float(value)
            for alpha_id, value in historical_pvalues.items()
        }
