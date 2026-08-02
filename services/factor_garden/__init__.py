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

from .canonical import (
    CANONICAL_FACTOR_VERSION,
    CANONICAL_FACTORS,
    CARRY_COMPOSITE_EXPRESSION,
    CARRY_QUANTILE_LIVE,
    CARRY_QUANTILE_OFFLINE,
    CROWD_CONTRA_EXPRESSION,
    FUNDING_FIELD,
    TOP_CONTRA_EXPRESSION,
    TREND_COMPOSITE_EXPRESSION,
    legs,
    register_canonical_factors,
)
from .definitions import (
    FactorDefinitionError,
    build_factor_definition,
    factor_content_hash,
)
from .dependency_graph import DependencyGraph
from .family_cluster import (
    FactorClusterAssignment,
    FactorClusterError,
    assign_factor_clusters,
    cluster_summary,
    family_of,
)
from .health import (
    DatasetHealth,
    FactorHealthVerdict,
    evaluate_factor_health,
    propagate,
)
from .materializer import MATERIALIZER_VERSION, materialize_factor, materialize_many
from .offline_online_parity import (
    PARITY_INSUFFICIENT_OVERLAP,
    PARITY_MISMATCH,
    PARITY_OK,
    ParityReport,
    compare_panels,
    compare_series,
)
from .registry import FactorGardenRegistry

__all__ = [
    "CANONICAL_FACTOR_VERSION",
    "CANONICAL_FACTORS",
    "CARRY_COMPOSITE_EXPRESSION",
    "CARRY_QUANTILE_LIVE",
    "CARRY_QUANTILE_OFFLINE",
    "CROWD_CONTRA_EXPRESSION",
    "DatasetHealth",
    "DependencyGraph",
    "FUNDING_FIELD",
    "FactorClusterAssignment",
    "FactorClusterError",
    "FactorDefinitionError",
    "FactorFamily",
    "FactorGardenRegistry",
    "FactorHealthVerdict",
    "MATERIALIZER_VERSION",
    "PARITY_INSUFFICIENT_OVERLAP",
    "PARITY_MISMATCH",
    "PARITY_OK",
    "ParityReport",
    "TOP_CONTRA_EXPRESSION",
    "TREND_COMPOSITE_EXPRESSION",
    "assign_factor_clusters",
    "build_factor_definition",
    "cluster_summary",
    "compare_panels",
    "compare_series",
    "evaluate_factor_health",
    "factor_content_hash",
    "family_of",
    "legs",
    "materialize_factor",
    "materialize_many",
    "propagate",
    "register_canonical_factors",
]
