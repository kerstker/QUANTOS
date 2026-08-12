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


def _evidence_trial_id(value: str) -> str:
    """Return a SHA-256 identity for current and legacy trial tokens.

    Early production registries retained 16-hex trial tokens. They are still
    real, distinct trials and must remain in the multiple-testing denominator,
    but the replayable evidence contract now requires full SHA-256 identities.
    Namespace hashing is one-to-one for the retained tokens and leaves the raw
    persistence ledger untouched.
    """
    token = str(value)
    lowered = token.lower()
    if len(lowered) == 64 and all(ch in "0123456789abcdef" for ch in lowered):
        return lowered
    if len(lowered) == 16 and all(ch in "0123456789abcdef" for ch in lowered):
        return stable_hash({"legacy_catalog_trial_token": lowered})
    return token


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
        self._historical_returns: dict[str, tuple[float, ...]] = {}
        self._historical_definition_hashes: dict[str, str] = {}
        self._historical_sharpes: dict[str, float] = {}
        self._historical_pvalues: dict[str, float] = {}
        self._seen_configs: set[str] = set()
        self._evidence_trials: set[str] = set()
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
        self._write_triple(aid, project(AlphaState.DRAFT))

    def register_trial(self, config_hash: str) -> None:
        """Count a never-seen config exactly once; monotonic, never decremented."""
        if self._trial_recorder is not None:
            self._trial_recorder(config_hash)
        if config_hash not in self._seen_configs:
            self._seen_configs.add(config_hash)
            self._evidence_trials.add(_evidence_trial_id(config_hash))
            self._trial_count += 1

    def global_trial_count(self) -> int:
        return self._trial_count

    def trial_set_hash(self) -> str:
        """Digest the complete monotonic distinct-config population.

        Count-only guards catch catalog growth but not a same-size substituted set.
        The digest lets evaluation and lifecycle evidence bind the exact population.
        """

        return stable_hash(sorted(self._evidence_trials))

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

    def evidence(self, alpha_id: str, *, scope_trial_hashes: Optional[frozenset] = None):
        """Mint replayable catalog-wide DSR evidence for one current alpha.

        The catalog owns both the monotonic trial ledger and all exact OOS return
        observations.  Callers can consume this frozen value but cannot supply a
        local trial-count/variance override.  Historical observations are keyed
        by their definition hash; a current re-evaluation replaces the matching
        historical observation rather than double-counting the same hypothesis.

        ``scope_trial_hashes``: when provided, only these trials count toward DSR
        denominator (filters out tuning grids / perturbations). The subject's own
        trial is always included even if absent from the scope.
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
                current_definition = self._defs.get(historical_id)
                if (
                    historical_id in self._returns
                    and current_definition is not None
                    and current_definition.definition_hash == definition_hash
                ):
                    # A current evaluation of the exact same Alpha identity and
                    # definition supersedes its persisted observation.  Keeping
                    # both would make every expanding-PIT re-evaluation conflict
                    # with its own previous return window.  Different identities
                    # that claim the same definition remain conflict-checked in
                    # the current-series loop below.
                    continue
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

        # Apply scope filter if provided
        if scope_trial_hashes is not None:
            scoped = frozenset(scope_trial_hashes) & frozenset(self._evidence_trials)
            # Always include the subject's own trial even if absent from scope
            subject_trial_id = f"research_session#{alpha_id}#trial"
            if subject_trial_id not in scoped and subject_trial_id in self._evidence_trials:
                scoped = scoped | {subject_trial_id}
            effective_trials = tuple(sorted(scoped))
        else:
            effective_trials = tuple(sorted(self._evidence_trials))

        return create_catalog_evidence(
            subject_id=alpha_id,
            subject_definition_hash=definition_hash,
            trial_definition_hashes=effective_trials,
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

    def family_fdr_result(
        self,
        q: float = 0.10,
        *,
        rho_cut: float = 0.70,
        min_overlap: int = 60,
    ):
        """Run the 727 §7.3 family/within-family FDR lens.

        Family identity is fail-closed and deterministic:

        * an explicit ``economic_family``/``family_id`` candidate parameter wins;
        * otherwise candidates with the same factor dependency set share one
          declared input family (the rank/zscore/window variants in §7.3);
        * definitions without usable dependency metadata fall back to return
          correlation;
        * historical p-values restored without their definitions/returns are
          charged as unique singleton families, never guessed into a discount.

        This result is an ADDITIONAL promotion lens.  The existing catalog-wide
        BHY test and flat global trial count remain mandatory, so family
        accounting cannot weaken the current gate or un-deflate DSR.
        """
        from .family_testing import assign_families, hierarchical_fdr

        pvalues = {**self._historical_pvalues, **self._pvalues}
        return_series: dict[str, tuple[float, ...]] = {}
        declared: dict[str, str] = {}
        for alpha_id in sorted(pvalues):
            return_series[alpha_id] = self._returns.get(alpha_id, ())
            definition = self._defs.get(alpha_id)
            if definition is None:
                # The evaluation-only restore path intentionally does not
                # rehydrate stale definitions/returns.  Treat every such row as
                # a singleton instead of inventing an economic relationship.
                declared[alpha_id] = f"legacy-singleton:{alpha_id}"
                continue
            explicit = (
                definition.params.get("economic_family")
                or definition.params.get("family_id")
                or ""
            )
            if isinstance(explicit, str) and explicit.strip():
                declared[alpha_id] = f"economic:{explicit.strip()}"
            elif definition.factor_ids:
                dependencies = ",".join(sorted(set(definition.factor_ids)))
                declared[alpha_id] = f"inputs:{dependencies}"

        families = assign_families(
            return_series=return_series,
            declared_families=declared,
            rho_cut=rho_cut,
            min_overlap=min_overlap,
        )
        return hierarchical_fdr(pvalues=pvalues, families=families, q=q)

    def family_fdr_survivors(
        self,
        q: float = 0.10,
        *,
        rho_cut: float = 0.70,
        min_overlap: int = 60,
    ) -> set[str]:
        return set(
            self.family_fdr_result(
                q,
                rho_cut=rho_cut,
                min_overlap=min_overlap,
            ).survivors
        )

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
        self._historical_returns = {}
        self._historical_definition_hashes = {}
        self._historical_sharpes = {}
        self._historical_pvalues = {}
        self._seen_configs = set(snap.trials)
        self._evidence_trials = {
            _evidence_trial_id(value) for value in self._seen_configs
        }
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
        historical_definition_hashes: Optional[Mapping[str, str]] = None,
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
        self._evidence_trials = {
            _evidence_trial_id(value) for value in self._seen_configs
        }
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
