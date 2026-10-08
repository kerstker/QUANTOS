"""Shared immutable catalog persistence records; no catalog implementation."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState
from .projection import LifecycleTriple


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
