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
from typing import Callable, Mapping, Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState

from services.lifecycle import (
    LOSSY_DEPLOYMENT,
    LOSSY_HEALTH,
    LOSSY_HEALTH_REASON,
    DeploymentState,
    HealthState,
    HealthVerdict,
    LifecycleTriple,
    ResearchState,
    project,
)

_VAR_SR_FLOOR = 1e-4


@dataclass(frozen=True)
class AlphaStateRow:
    alpha_id: str
    definition: AlphaDefinition
    state: AlphaState
    returns: tuple[float, ...]
    pvalue: Optional[float]
    # 727 C2: the three orthogonal machines alongside the legacy mixed state.
    # None means "written before C2" — restore() falls back to project(state)
    # rather than inventing dimensions that were never recorded.
    triple: Optional[LifecycleTriple] = None


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
        # 727 C2: parallel per-machine views. In C2 they are ALWAYS the
        # projection of _state (dual-write); C3's advance_research/deployment/
        # health entries are what will let them diverge from the projection.
        self._research: dict[str, ResearchState] = {}
        self._deployment: dict[str, DeploymentState] = {}
        self._health: dict[str, HealthVerdict] = {}
        self._lossy: dict[str, tuple[str, ...]] = {}
        self._returns: dict[str, tuple[float, ...]] = {}
        self._sharpes: dict[str, float] = {}
        self._pvalues: dict[str, float] = {}
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
        self.register_trial(definition.definition_hash)
        self._defs[aid] = definition
        self._state[aid] = AlphaState.DRAFT
        self._write_triple(aid, project(AlphaState.DRAFT))

    def register_trial(self, config_hash: str) -> None:
        """Count a never-seen config exactly once; monotonic, never decremented."""
        if self._trial_recorder is not None:
            self._trial_recorder(config_hash)
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
        vals = list({**self._historical_sharpes, **self._sharpes}.values())
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

    def _write_triple(self, alpha_id: str, triple: LifecycleTriple) -> None:
        self._research[alpha_id] = triple.research
        self._deployment[alpha_id] = triple.deployment
        self._health[alpha_id] = triple.health
        self._lossy[alpha_id] = triple.lossy

    def _set_state(self, alpha_id: str, state: AlphaState) -> None:
        """INTERNAL — performs NO validation. The AlphaLifecycleManager (the
        validated, audited chokepoint) is the ONLY component that should call this
        during operation; test/seed setup may use it to construct fixtures. A
        production state change MUST go through AlphaLifecycleManager.advance() —
        calling this directly bypasses the state machine, guards, and audit trail
        (see the threat-model note in CLAUDE.md: in-process trusted code is governed
        by code review + the governance scanner, not by this runtime check)."""
        self._state[alpha_id] = state
        # Dual-write (727 C2): the orthogonal machines shadow the legacy write
        # with its projection. Zero behavior change — the legacy read path stays
        # authoritative until C3 switches the orchestrator.
        self._write_triple(alpha_id, project(state))

    def _set_health(self, alpha_id: str, verdict: HealthVerdict) -> None:
        """INTERNAL (727 C3) — health-machine write, same chokepoint discipline as
        _set_state: only AlphaLifecycleManager.advance_health may call this in
        production. Records a fully-known verdict, so the row's loss markers
        concerning health are cleared (deployment loss, if any, is kept)."""
        self._health[alpha_id] = verdict
        self._lossy[alpha_id] = tuple(
            marker
            for marker in self._lossy.get(alpha_id, ())
            if marker not in (LOSSY_HEALTH, LOSSY_HEALTH_REASON)
        )

    def _set_deployment(self, alpha_id: str, state: DeploymentState) -> None:
        """INTERNAL (727 C3) — deployment-machine write; only the manager's
        advance_deployment / advance_health de-risk coupling may call it."""
        self._deployment[alpha_id] = state
        if state is not DeploymentState.UNKNOWN_LEGACY:
            self._lossy[alpha_id] = tuple(
                marker
                for marker in self._lossy.get(alpha_id, ())
                if marker != LOSSY_DEPLOYMENT
            )

    def _set_research(self, alpha_id: str, state: ResearchState) -> None:
        """INTERNAL (727 C3) — research-machine write; reserved for the manager's
        advance_research entry (no production caller yet)."""
        self._research[alpha_id] = state

    # --- orthogonal machine reads (727 C2; additive, legacy reads untouched) --

    def triple(self, alpha_id: str) -> LifecycleTriple:
        """The three-machine view. Rows registered/restored after C2 carry it
        directly; anything older is projected on the fly with its loss markers."""
        if alpha_id not in self._research:
            return project(self._state[alpha_id])
        return LifecycleTriple(
            research=self._research[alpha_id],
            deployment=self._deployment[alpha_id],
            health=self._health[alpha_id],
            lossy=self._lossy.get(alpha_id, ()),
        )

    def research_state(self, alpha_id: str) -> ResearchState:
        return self.triple(alpha_id).research

    def deployment_state(self, alpha_id: str) -> DeploymentState:
        return self.triple(alpha_id).deployment

    def health(self, alpha_id: str) -> HealthVerdict:
        return self.triple(alpha_id).health

    def by_deployment(self, state: DeploymentState) -> list[str]:
        return sorted(
            aid for aid in self._defs if self.triple(aid).deployment is state
        )

    def by_health(self, state: HealthState) -> list[str]:
        return sorted(aid for aid in self._defs if self.triple(aid).health.state is state)

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
                triple=self.triple(aid),
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
        self._research = {}
        self._deployment = {}
        self._health = {}
        self._lossy = {}
        self._returns = {}
        self._sharpes = {}
        self._pvalues = {}
        self._historical_sharpes = {}
        self._historical_pvalues = {}
        self._seen_configs = set(snap.trials)
        self._trial_count = len(self._seen_configs)
        for row in snap.alphas:
            self._defs[row.alpha_id] = row.definition
            self._state[row.alpha_id] = row.state
            # Rows persisted before C2 have no triple columns; project the legacy
            # state (keeping its loss markers) instead of inventing dimensions.
            self._write_triple(row.alpha_id, row.triple or project(row.state))
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
    ) -> None:
        """Restore multiple-testing state without stale lifecycle identities.

        A new dataset must re-evaluate the same canonical alpha IDs, while DSR and
        FDR still see prior trials and statistics. Historical rows remain
        statistical context only; current evaluations replace their value by ID.
        """
        self._defs = {}
        self._state = {}
        self._research = {}
        self._deployment = {}
        self._health = {}
        self._lossy = {}
        self._returns = {}
        self._sharpes = {}
        self._pvalues = {}
        self._seen_configs = set(trials)
        self._trial_count = len(self._seen_configs)
        self._historical_sharpes = {}
        for alpha_id, values in historical_returns.items():
            returns = tuple(float(value) for value in values)
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
