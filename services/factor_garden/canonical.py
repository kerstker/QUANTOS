"""Canonical factor convergence (727 §6.3 / Factor Garden F2, 2026-07-27).

The PRODUCTION factor expressions previously lived as module-level constants scattered
across ``tools/`` — some duplicated verbatim (``crypto_oi_research.CARRY`` vs
``crypto_carry_strategy.COMPOSITE``; ``CROWD_CONTRA``/``TOP_CONTRA`` re-typed in ~10
drivers; the trend composite duplicated in ``dry_run_trend_multi``; the ``_legs``
book-builder copied verbatim into both carry files). F2 moves the single source of
truth HERE (services side — tools import services, never the reverse) and the tools
files re-export it under their historical names, so every importer and every pinned
test sees bit-identical values (in fact the SAME objects).

Moved verbatim (bit-identity is load-bearing — this is a MOVE, not a tune):
- ``CARRY_COMPOSITE_EXPRESSION`` — the de-redundant funding-carry composite
  (mean cs_zscore of neg decay_linear{3,7,14} + neg ts_mean{7} of funding).
- ``CROWD_CONTRA_EXPRESSION`` / ``TOP_CONTRA_EXPRESSION`` — the OI positioning factors.
- ``TREND_COMPOSITE_EXPRESSION`` — the daily trend composite in its PRODUCTION form:
  a tuple of ``(SIGNAL_OPS op, window)`` pairs consumed by
  ``services.research.ic_panel.signal_composite`` (NOT the nested-tuple DSL).
- ``legs(sig_row, q)`` — the shared quantile long/short book builder; the two tools
  copies were verified byte-identical (inspect.getsource equality) before the move.
- ``FUNDING_FIELD`` — the ``("field", "funding")`` node the carry expression is
  built from (re-exported as ``FUND`` by ``tools/crypto_carry_strategy.py``).

Honest boundaries (design doc §5, 2026-07-27-factor-garden-design.md):
- **QUANTILE divergence NOT unified** (registered as a human decision, design doc F2):
  the OFFLINE carry book trades at ``CARRY_QUANTILE_OFFLINE = 0.2``
  (``tools/crypto_carry_strategy.py::QUANTILE``, 115-coin funding universe) while the
  LIVE track uses ``CARRY_QUANTILE_LIVE = 0.3`` (``tools/crypto_oi_research.py::
  QUANTILE`` via ``tools/live_track_advance.py``, ~24-coin OI universe — the thinner
  universe legitimately wants a wider band). Unifying them is a behavior change with
  different forward-evidence histories behind each number; F2 only records both.
- **The trend composite is NOT in ``CANONICAL_FACTORS``.** Its production form is the
  ``SIGNAL_OPS`` pair-tuple above, whose semantics (gap-skipping history accumulation,
  all-or-nothing legs, >=3-valid cross-sectional z-score in ``signal_composite``) are
  NOT bit-identical to any nested-tuple-DSL re-expression (the DSL's ``ts_*`` windows
  operate on matrix rows, not gap-skipped histories). ``build_factor_definition``
  mandates the DSL, so forcing trend in would be a re-derivation, not a move. It is
  exported here as the single source of truth for the pair-tuple form; garden
  registration of a DSL-native trend factor is a separate, explicitly-reviewed slice.
- No IO at import time: ``CANONICAL_FACTORS`` are pure constants (created_at_ms=0);
  ``register_canonical_factors`` is called by drivers, never at import.
- Registering a factor drives NO promotion and NO orders (diagnostic, not a gate).
"""
from __future__ import annotations

from quantos.contracts.feature import FactorFamily, FeatureDefinition

from .definitions import FactorDefinitionError, build_factor_definition
from .registry import FactorGardenRegistry

# ---------------------------------------------------------------------------
# Canonical expressions (the single source of truth; tools re-export these).
# ---------------------------------------------------------------------------

FUNDING_FIELD = ("field", "funding")


def _zneg(op, w):
    return ("cs_zscore", ("neg", (op, FUNDING_FIELD, w)))


# De-redundant carry composite: average of robust funding smoothings (the search's
# winners: neg decay_linear{3,7,14} + neg ts_mean{7}, equal-weighted via /4).
CARRY_COMPOSITE_EXPRESSION = (
    "div",
    ("add",
     ("add", _zneg("decay_linear", 3), _zneg("decay_linear", 7)),
     ("add", _zneg("decay_linear", 14), _zneg("ts_mean", 7))),
    ("const", 4.0),
)

# Fade crowded retail positioning (global long/short account ratio).
CROWD_CONTRA_EXPRESSION = ("cs_rank", ("neg", ("field", "global_ls")))

# Fade top-trader positioning extremes (top-trader long/short ratio).
TOP_CONTRA_EXPRESSION = ("cs_zscore", ("neg", ("field", "top_ls")))

# Daily trend composite in its PRODUCTION pair-tuple form (SIGNAL_OPS ops + windows),
# consumed by services.research.ic_panel.signal_composite — deliberately NOT the
# nested-tuple DSL (see the module docstring's honest boundaries).
TREND_COMPOSITE_EXPRESSION = (
    ("dist_from_high", 7),
    ("dist_from_high", 14),
    ("dist_from_high", 30),
    ("momentum", 7),
)

# The QUANTILE divergence, recorded as two explicitly-named parameters — NOT unified
# (a human decision per the design doc). Each tools driver keeps its historical value.
CARRY_QUANTILE_OFFLINE = 0.2   # tools/crypto_carry_strategy.py::QUANTILE (offline book)
CARRY_QUANTILE_LIVE = 0.3      # tools/crypto_oi_research.py::QUANTILE (live track, OI universe)


def legs(sig_row, q):
    """Quantile long/short dollar-neutral book from one cross-sectional signal row.

    Verbatim move of the tools ``_legs`` helper (the two copies in
    crypto_oi_research.py / crypto_carry_strategy.py were byte-identical):
    needs >= 4 non-None signals; longs/shorts are the top/bottom
    ``max(1, round(n*q))`` names, each leg 0.5 gross, index-keyed weights.
    """
    idx = [i for i, v in enumerate(sig_row) if v is not None]
    if len(idx) < 4:
        return {}
    idx.sort(key=lambda i: sig_row[i])
    k = max(1, int(round(len(idx) * q)))
    longs, shorts = idx[-k:], idx[:k]
    w = {}
    for i in longs:
        w[i] = 0.5 / len(longs)
    for i in shorts:
        w[i] = w.get(i, 0.0) - 0.5 / len(shorts)
    return w


# ---------------------------------------------------------------------------
# CANONICAL_FACTORS — the DSL-native production factors as garden definitions.
# ---------------------------------------------------------------------------

CANONICAL_FACTOR_VERSION = "1.0.0"

CANONICAL_FACTORS: tuple[FeatureDefinition, ...] = (
    build_factor_definition(
        name="carry_composite",
        family=FactorFamily.FUNDING_CARRY,
        expression=CARRY_COMPOSITE_EXPRESSION,
        economic_hypothesis=(
            "Perpetual-futures funding is the term-structure/cash-and-carry premium: "
            "crowded longs pay shorts on positive-funding names and vice versa. A "
            "dollar-neutral book shorting smoothed-high-funding perps and longing "
            "smoothed-low/negative-funding perps collects the funding cash flow on "
            "both legs (the edge is the carry cash flow, not price direction)."
        ),
        required_datasets=("crypto_funding", "crypto_funding_rates"),
        frequency="1d",
        universe="crypto_funding",
        minimum_lag=1,
        normalization="mean of per-leg cs_zscore (4 equal legs)",
        creator_actor="quantos-research",
        created_at_ms=0,
    ),
    build_factor_definition(
        name="crowd_contra",
        family=FactorFamily.POSITIONING_CROWDING,
        expression=CROWD_CONTRA_EXPRESSION,
        economic_hypothesis=(
            "Retail positioning is a contrarian indicator: when the global "
            "long/short account ratio is crowded long, subsequent returns are "
            "systematically weaker (and symmetrically for crowded shorts), so fading "
            "the crowded side earns the positioning-unwind premium — empirically "
            "near-orthogonal to the funding-carry edge."
        ),
        required_datasets=("crypto_funding", "crypto_oi_metrics"),
        frequency="1d",
        universe="crypto_oi_majors",
        minimum_lag=1,
        normalization="cs_rank",
        creator_actor="quantos-research",
        created_at_ms=0,
    ),
    build_factor_definition(
        name="top_contra",
        family=FactorFamily.POSITIONING_CROWDING,
        expression=TOP_CONTRA_EXPRESSION,
        economic_hypothesis=(
            "Fading top-trader positioning extremes: extreme top-trader long/short "
            "ratios mark positioning crowding whose unwind is adverse for the "
            "crowded side (the smart-money cohort's extreme positioning mean-reverts "
            "rather than persists)."
        ),
        required_datasets=("crypto_funding", "crypto_oi_metrics"),
        frequency="1d",
        universe="crypto_oi_majors",
        minimum_lag=1,
        normalization="cs_zscore",
        creator_actor="quantos-research",
        created_at_ms=0,
    ),
)


def register_canonical_factors(
    registry: FactorGardenRegistry, *, recorded_at_ms: int
) -> dict:
    """Register every CANONICAL_FACTORS entry; idempotent. Called by DRIVERS only.

    - already registered with the identical definition -> skipped ("already_registered");
    - same (factor_id, version) with a DIFFERENT definition -> raise (conflict — the
      registry is the authority and silent divergence is exactly what F2 exists to kill).
    Returns ``{factor_name: "registered" | "already_registered"}``.
    """
    if not isinstance(registry, FactorGardenRegistry):
        raise TypeError("registry must be a FactorGardenRegistry")
    if isinstance(recorded_at_ms, bool) or not isinstance(recorded_at_ms, int) \
            or recorded_at_ms < 0:
        raise FactorDefinitionError(
            "recorded_at_ms must be a caller-supplied non-negative int ms"
        )
    results = {}
    for defn in CANONICAL_FACTORS:
        if CANONICAL_FACTOR_VERSION in registry.versions(defn.feature_id):
            existing = registry.get(defn.feature_id, CANONICAL_FACTOR_VERSION).definition
            if existing != defn:
                raise FactorDefinitionError(
                    f"canonical factor {defn.name!r} v{CANONICAL_FACTOR_VERSION} "
                    "conflicts with the registered definition"
                )
            results[defn.name] = "already_registered"
        else:
            registry.register(
                defn,
                factor_version=CANONICAL_FACTOR_VERSION,
                recorded_at_ms=recorded_at_ms,
            )
            results[defn.name] = "registered"
    return results
