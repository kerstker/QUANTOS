"""Explicit dependencies for quarantined diagnostic alpha research."""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AlphaResearchRuntime:
    catalog_factory: Callable
    evaluator_factory: Callable
    registry_factory: Callable
    make_child_asset: Callable
    optimize_population: Callable
