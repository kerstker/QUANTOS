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

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition
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
    def __init__(self) -> None:
        self._defs: dict[str, AlphaDefinition] = {}
        self._state: dict[str, AlphaState] = {}
        self._returns: dict[str, tuple[float, ...]] = {}
        self._sharpes: dict[str, float] = {}
        self._pvalues: dict[str, float] = {}
        self._seen_configs: set[str] = set()
        self._trial_count: int = 0

    # --- registration & multiple-testing accounting --------------------------

    def register(self, definition: AlphaDefinition) -> None:
        # Always count by the definition's own hash — no caller-supplied override,
        # which could collide distinct specs to one hash and undercount trials
        # (review P0). definition_hash is derived from factor_ids/universe/params.
        aid = definition.alpha_id
        if aid in self._defs:
            raise ValueError(f"alpha already registered: {aid}")
        self._defs[aid] = definition
        self._state[aid] = AlphaState.DRAFT
        self.register_trial(definition.definition_hash)

    def register_trial(self, config_hash: str) -> None:
        """Count a never-seen config exactly once; monotonic, never decremented."""
        if config_hash not in self._seen_configs:
            self._seen_configs.add(config_hash)
            self._trial_count += 1

    def global_trial_count(self) -> int:
        return self._trial_count

    def catalog_var_sr(self) -> Optional[float]:
        """Population variance of per-observation Sharpe across the catalog.

        Returns None when fewer than 2 alphas have returns: there is NO honest
        cross-trial variance estimate yet, and silently flooring it to 1e-4 would
        UN-deflate Deflated Sharpe and let a noise alpha pass (review P0). The
        caller must route to human review instead of fabricating a tiny var_sr.
        """
        vals = list(self._sharpes.values())
        if len(vals) < 2:
            return None
        return max(statistics.pstdev(vals) ** 2, _VAR_SR_FLOOR)

    # --- per-alpha data ------------------------------------------------------

    def set_returns(self, alpha_id: str, returns: Sequence[float]) -> None:
        rs = tuple(returns)
        self._returns[alpha_id] = rs
        if len(rs) > 1:
            sd = statistics.pstdev(rs)
            if sd > 0:
                self._sharpes[alpha_id] = statistics.fmean(rs) / sd

    def returns(self, alpha_id: str) -> tuple[float, ...]:
        return self._returns.get(alpha_id, ())

    def set_pvalue(self, alpha_id: str, pvalue: Optional[float]) -> None:
        if pvalue is not None:
            self._pvalues[alpha_id] = float(pvalue)

    def bh_fdr_survivors(self, q: float = 0.10) -> set[str]:
        """Alpha ids whose IC p-value survives catalog-wide Benjamini-Hochberg FDR
        at level q. The second, independent edge lens (alongside DSR)."""
        from .multiple_testing import benjamini_hochberg
        ids = sorted(self._pvalues)
        survivors = benjamini_hochberg([self._pvalues[a] for a in ids], q=q)
        return {ids[i] for i in survivors}

    def bhy_fdr_survivors(self, q: float = 0.10) -> set[str]:
        """Alpha ids whose IC p-value survives catalog-wide Benjamini-Hochberg-YEKUTIELI FDR at
        level q — the arbitrary-DEPENDENCE FDR control (stricter than plain BH), the correct lens
        when the catalog's edges are correlated (which they are). Wired into the VALIDATED guard
        (Rank #12); BHY was implemented but unused before."""
        from .multiple_testing import benjamini_hochberg_yekutieli
        ids = sorted(self._pvalues)
        survivors = benjamini_hochberg_yekutieli([self._pvalues[a] for a in ids], q=q)
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
        self._seen_configs = set(snap.trials)
        self._trial_count = len(self._seen_configs)
        for row in snap.alphas:
            self._defs[row.alpha_id] = row.definition
            self._state[row.alpha_id] = row.state
            if row.returns:
                self.set_returns(row.alpha_id, row.returns)   # recomputes _sharpes
            if row.pvalue is not None:
                self._pvalues[row.alpha_id] = float(row.pvalue)
