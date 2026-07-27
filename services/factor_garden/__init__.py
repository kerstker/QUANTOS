"""Factor Garden (727 §6 / Phase 4) — canonical factor definitions + versioned registry.

F1 slice (2026-07-27): the widened :class:`quantos.contracts.feature.FeatureDefinition`
contract, the fail-closed definition builder, and a thin garden-invariant boundary over
the battle-tested :class:`services.features.registry_store.SqliteFeatureRegistryStore`.

Honest boundaries:
- This is a RESEARCH-side registry/materialization layer. It drives NO promotion and
  NO orders; registering a factor changes no evaluation gate, mandate, or live path.
- Deterministic core: no wall clock (all timestamps are caller-supplied int ms), no
  uuid (identities are stable content-derived hashes / human names + versions).
- Single-node SQLite authority, same as the feature registry it wraps.
- The canonical expression form is the nested-tuple DSL of
  ``services.research.alpha_expression`` — the factor_engine string-AST compiler is
  deliberately NOT used (dead/narrow, see the F1 design doc).
"""
from quantos.contracts.feature import FactorFamily

from .definitions import (
    FactorDefinitionError,
    build_factor_definition,
    factor_content_hash,
)
from .registry import FactorGardenRegistry

__all__ = [
    "FactorDefinitionError",
    "FactorFamily",
    "FactorGardenRegistry",
    "build_factor_definition",
    "factor_content_hash",
]
