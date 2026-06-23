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
from typing import Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState

_VAR_SR_FLOOR = 1e-4


class AlphaCatalog:
    def __init__(self) -> None:
        self._defs: dict[str, AlphaDefinition] = {}
        self._state: dict[str, AlphaState] = {}
        self._returns: dict[str, tuple[float, ...]] = {}
        self._sharpes: dict[str, float] = {}
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

    # --- state (low-level; the lifecycle manager is the validated chokepoint) -

    def get(self, alpha_id: str) -> AlphaDefinition:
        return self._defs[alpha_id]

    def state(self, alpha_id: str) -> AlphaState:
        return self._state[alpha_id]

    def _set_state(self, alpha_id: str, state: AlphaState) -> None:
        self._state[alpha_id] = state

    def all_ids(self) -> list[str]:
        return sorted(self._defs)

    def by_state(self, state: AlphaState) -> list[str]:
        return sorted(aid for aid, s in self._state.items() if s == state)

    def approved_set(self) -> dict[str, tuple[float, ...]]:
        """Approved alphas' return series (the orthogonalization basis)."""
        return {aid: self._returns[aid] for aid in self.by_state(AlphaState.APPROVED) if aid in self._returns}
