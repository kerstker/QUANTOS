"""Evidence-ranked Alpha methodology registry and data-readiness audit.

The registry is a research supply-chain primitive, not a signal generator.  It
keeps academic evidence, cross-market priors, and practitioner claims visibly
separate and translates each method into a falsifiable experiment contract.
Nothing in this module evaluates returns, promotes an Alpha, touches a broker,
or grants paper/live eligibility.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Mapping, Sequence


class MethodologyRegistryError(ValueError):
    """Raised when a methodology entry could create false research provenance."""


class EvidenceTier(str, Enum):
    PEER_REVIEWED = "PEER_REVIEWED"
    WORKING_PAPER = "WORKING_PAPER"
    PRACTITIONER_PRIMARY = "PRACTITIONER_PRIMARY"


class MethodReadiness(str, Enum):
    HISTORICAL_DIAGNOSTIC_READY = "HISTORICAL_DIAGNOSTIC_READY"
    FORWARD_PREREG_REQUIRED = "FORWARD_PREREG_REQUIRED"
    BLOCKED_MISSING_INPUT = "BLOCKED_MISSING_INPUT"


_PORTABILITIES = frozenset(
    {"DIRECT_CRYPTO", "CROSS_ASSET_PRIOR", "PRACTITIONER_TRANSLATION"}
)
_SYSTEM_DISPOSITIONS = frozenset(
    {
        "EXISTING_TESTED_FAMILY",
        "EXISTING_FALSIFIED_IMPLEMENTATION",
        "EXISTING_BLOCKED_IMPLEMENTATION",
        "NEW_EXPERIMENT_GAP",
        "FORWARD_EXPERIMENT_GAP",
    }
)


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    title: str
    authors: tuple[str, ...]
    year: int
    evidence_tier: EvidenceTier
    url: str
    doi: str | None
    practitioner_claim: bool
    scope_note: str


@dataclass(frozen=True)
class AlphaMethodSpec:
    method_id: str
    name: str
    source_ids: tuple[str, ...]
    portability: str
    economic_mechanism: str
    testable_claim: str
    required_channel_ids: tuple[str, ...]
    holding_horizons: tuple[str, ...]
    cost_risks: tuple[str, ...]
    falsification_tests: tuple[str, ...]
    leakage_risks: tuple[str, ...]
    system_modules: tuple[str, ...]
    system_disposition: str


@dataclass(frozen=True)
class DiscoveryControlSpec:
    control_id: str
    source_ids: tuple[str, ...]
    failure_prevented: str
    system_modules: tuple[str, ...]


@dataclass(frozen=True)
class MethodologyAuditRow:
    method_id: str
    readiness: MethodReadiness
    missing_channels: tuple[str, ...]
    non_historical_channels: tuple[str, ...]
    evidence_tiers: tuple[str, ...]
    system_disposition: str
    priority_score: int
    priority_rank: int = 0


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        "momentum_jegadeesh_titman",
        "Profitability of Momentum Strategies: An Evaluation of Alternative Explanations",
        ("Narasimhan Jegadeesh", "Sheridan Titman"),
        2001,
        EvidenceTier.PEER_REVIEWED,
        "https://www.nber.org/papers/w7159",
        "10.3386/w7159",
        False,
        "Cross-asset equity prior; the crypto implementation still needs its own evidence.",
    ),
    SourceSpec(
        "carry_koijen_moskowitz_pedersen_vrugt",
        "Carry",
        ("Ralph Koijen", "Tobias Moskowitz", "Lasse Pedersen", "Evert Vrugt"),
        2018,
        EvidenceTier.PEER_REVIEWED,
        "https://www.nber.org/papers/w19325",
        "10.3386/w19325",
        False,
        "Multi-asset carry prior; crypto funding cash flows require separate execution tests.",
    ),
    SourceSpec(
        "pairs_gatev_goetzmann_rouwenhorst",
        "Pairs Trading: Performance of a Relative-Value Arbitrage Rule",
        ("Evan Gatev", "William Goetzmann", "K. Geert Rouwenhorst"),
        2006,
        EvidenceTier.PEER_REVIEWED,
        "https://www.nber.org/papers/w7032",
        "10.3386/w7032",
        False,
        "Equity relative-value prior; pair formation and trading samples must be separated.",
    ),
    SourceSpec(
        "ofi_cont_kukanov_stoikov",
        "The Price Impact of Order Book Events",
        ("Rama Cont", "Arseniy Kukanov", "Sasha Stoikov"),
        2014,
        EvidenceTier.PEER_REVIEWED,
        "https://academic.oup.com/jfec/article-abstract/12/1/47/816163",
        "10.1093/jjfinec/nbt003",
        False,
        "NYSE microstructure evidence; crypto persistence and net tradability are not implied.",
    ),
    SourceSpec(
        "crypto_risk_return_liu_tsyvinski",
        "Risks and Returns of Cryptocurrency",
        ("Yukun Liu", "Aleh Tsyvinski"),
        2021,
        EvidenceTier.PEER_REVIEWED,
        "https://www.nber.org/papers/w24877",
        "10.3386/w24877",
        False,
        "Direct crypto evidence for momentum and attention, not a ready trading rule.",
    ),
    SourceSpec(
        "crypto_price_discovery_makarov_schoar",
        "Price Discovery in Cryptocurrency Markets",
        ("Igor Makarov", "Antoinette Schoar"),
        2019,
        EvidenceTier.PEER_REVIEWED,
        "https://www.aeaweb.org/articles?id=10.1257%2Fpandp.20191020",
        "10.1257/pandp.20191020",
        False,
        "Direct crypto venue evidence; observed spreads are not assumed executable.",
    ),
    SourceSpec(
        "disposition_effect_grinblatt_han",
        "The Disposition Effect and Momentum",
        ("Mark Grinblatt", "Bing Han"),
        2005,
        EvidenceTier.PEER_REVIEWED,
        "https://www.nber.org/papers/w8734",
        "10.3386/w8734",
        False,
        "Behavioral equity prior used only to motivate a positioning falsification.",
    ),
    SourceSpec(
        "liquidity_bekaert_harvey_lundblad",
        "Liquidity and Expected Returns: Lessons from Emerging Markets",
        ("Geert Bekaert", "Campbell Harvey", "Christian Lundblad"),
        2007,
        EvidenceTier.PEER_REVIEWED,
        "https://academic.oup.com/rfs/article-abstract/20/6/1783/1575135",
        "10.1093/rfs/hhm030",
        False,
        "Emerging-equity liquidity prior; crypto volume is not silently treated as equivalent.",
    ),
    SourceSpec(
        "dsr_bailey_lopez_de_prado",
        "The Deflated Sharpe Ratio",
        ("David Bailey", "Marcos Lopez de Prado"),
        2014,
        EvidenceTier.PEER_REVIEWED,
        "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551",
        "10.2139/ssrn.2460551",
        False,
        "Discovery control for selection bias and non-normal returns.",
    ),
    SourceSpec(
        "pbo_bailey_borwein_lopez_de_prado_zhu",
        "The Probability of Backtest Overfitting",
        ("David Bailey", "Jonathan Borwein", "Marcos Lopez de Prado", "Qiji Zhu"),
        2015,
        EvidenceTier.PEER_REVIEWED,
        "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253",
        "10.2139/ssrn.2326253",
        False,
        "Discovery control for family-level backtest overfitting.",
    ),
    SourceSpec(
        "ict_huddleston_primary",
        "The Inner Circle Trader primary education library",
        ("Michael J. Huddleston",),
        2022,
        EvidenceTier.PRACTITIONER_PRIMARY,
        "https://www.theinnercircletrader.com/",
        None,
        True,
        "Practitioner-origin claims only; no peer-reviewed profitability evidence is inferred.",
    ),
)


ALPHA_METHODS: tuple[AlphaMethodSpec, ...] = (
    AlphaMethodSpec(
        "cross_sectional_momentum",
        "Cross-sectional momentum",
        ("momentum_jegadeesh_titman", "crypto_risk_return_liu_tsyvinski"),
        "DIRECT_CRYPTO",
        "Gradual information diffusion and slow-moving positioning can create return persistence.",
        "A fixed past-return ranking predicts same-sign net cross-sectional returns on a locked horizon.",
        ("binance_usdm_price",),
        ("7d", "28d"),
        ("turnover", "momentum crashes", "universe churn"),
        ("sign reversal", "skip-window control", "downside-regime survival"),
        ("formation/holding overlap", "survivorship", "post-selection horizon choice"),
        ("services/research/alpha_expression.py", "tools/crypto_daily_research.py"),
        "EXISTING_TESTED_FAMILY",
    ),
    AlphaMethodSpec(
        "carry_cross_section",
        "Cross-sectional funding carry",
        ("carry_koijen_moskowitz_pedersen_vrugt",),
        "CROSS_ASSET_PRIOR",
        "Known financing cash flows compensate inventory and crash risk and may persist cross-sectionally.",
        "High-minus-low settled funding produces positive net cash-flow-aware returns across locked periods.",
        ("binance_usdm_funding", "binance_usdm_price"),
        ("7d", "28d"),
        ("crowding", "funding reversal", "tail loss", "execution breadth"),
        ("price-only placebo", "double-cost pressure", "crisis replay"),
        ("using announced instead of settled funding", "revised funding history"),
        ("services/factor_garden/canonical.py", "tools/dry_run_carry_authoritative_multi.py"),
        "EXISTING_TESTED_FAMILY",
    ),
    AlphaMethodSpec(
        "pairs_relative_value",
        "Distance-selected pairs relative value",
        ("pairs_gatev_goetzmann_rouwenhorst",),
        "CROSS_ASSET_PRIOR",
        "Economically related assets can temporarily diverge from a stable relative-price relation.",
        "Pairs selected only on a formation window mean-revert net of costs in a disjoint locked trade window.",
        ("binance_spot_klines", "binance_usdm_price"),
        ("1d", "3d", "7d"),
        ("short borrow proxy", "leg imbalance", "structural breaks", "microstructure bounce"),
        ("fixed-hash pair control", "continuation sign control", "formation/trade roll-forward"),
        ("pair selection on trade window", "global normalization", "delisting leakage"),
        ("services/research/pairs_relative_value.py", "tools/pairs_relative_value_research.py"),
        "EXISTING_FALSIFIED_IMPLEMENTATION",
    ),
    AlphaMethodSpec(
        "order_flow_imbalance",
        "Order-flow imbalance and absorption",
        ("ofi_cont_kukanov_stoikov",),
        "CROSS_ASSET_PRIOR",
        "Aggressive flow moves prices in proportion to imbalance and inversely to displayed depth.",
        "Pre-decision order-flow/depth imbalance predicts a fixed future mid-price move after spread and fees.",
        ("binance_usdm_websocket_stream",),
        ("5m", "15m", "1h"),
        ("latency", "spread", "impact", "queue position"),
        ("flow sign control", "depth-only placebo", "latency pressure"),
        ("including label-window trades", "using reconstructed post-gap depth"),
        ("services/research/realtime_alpha_discovery.py", "tools/realtime_alpha_discovery.py"),
        "EXISTING_FALSIFIED_IMPLEMENTATION",
    ),
    AlphaMethodSpec(
        "cross_venue_price_discovery",
        "Cross-venue price discovery",
        ("crypto_price_discovery_makarov_schoar",),
        "DIRECT_CRYPTO",
        "Venue segmentation and heterogeneous price discovery can create temporary lead-lag relations.",
        "A source-timestamped leader venue predicts a lagging venue after executable spread and latency costs.",
        ("cross_exchange_book", "hyperliquid_public_perp"),
        ("5m", "1h"),
        ("transfer friction", "stale quotes", "venue failure", "latency"),
        ("venue-label permutation", "synchronous-only sample", "double-latency pressure"),
        ("asynchronous joins", "clock skew", "observed-at/event-time confusion"),
        ("tools/cross_venue_research.py", "tools/paper_multivenue_sor.py"),
        "EXISTING_FALSIFIED_IMPLEMENTATION",
    ),
    AlphaMethodSpec(
        "positioning_reversal",
        "Crowded-position reversal",
        ("disposition_effect_grinblatt_han",),
        "CROSS_ASSET_PRIOR",
        "Slow loss realization and crowded positioning can create delayed adjustment and later reversal.",
        "Extreme public long/short positioning predicts sign-reversed net returns on fixed horizons.",
        ("binance_usdm_oi_metrics", "bybit_linear_positioning"),
        ("3d", "7d"),
        ("reporting population bias", "funding exposure", "liquidation gaps"),
        ("continuation control", "single-venue exclusion", "crisis regime"),
        ("publication delay", "revised account ratios", "cross-venue calendar mismatch"),
        ("tools/crypto_oi_research.py", "tools/cross_venue_positioning_evaluation.py"),
        "EXISTING_FALSIFIED_IMPLEMENTATION",
    ),
    AlphaMethodSpec(
        "liquidity_premium",
        "Cross-sectional liquidity premium",
        ("liquidity_bekaert_harvey_lundblad",),
        "CROSS_ASSET_PRIOR",
        "Hard-to-trade assets may offer a return premium that is inseparable from trading costs and liquidity risk.",
        "A fixed PIT liquidity measure predicts returns that remain positive after conservative participation and impact limits.",
        ("binance_usdm_price", "binance_usdm_oi_metrics"),
        ("7d", "28d"),
        ("untradeable-premium illusion", "capacity", "delisting", "impact"),
        ("large-cap-only subset", "participation cap", "double-impact pressure"),
        ("using future ADV", "zero-return liquidity proxy bias", "survivorship"),
        ("services/research/liquidity_premium.py", "tools/liquidity_premium_research.py"),
        "EXISTING_BLOCKED_IMPLEMENTATION",
    ),
    AlphaMethodSpec(
        "crypto_attention_momentum",
        "Crypto attention-conditioned momentum",
        ("crypto_risk_return_liu_tsyvinski",),
        "DIRECT_CRYPTO",
        "Attention shocks can amplify slow information diffusion in crypto markets.",
        "A timestamped attention innovation conditions a fixed momentum signal in a locked crypto sample.",
        ("binance_usdm_price", "crypto_attention"),
        ("1d", "7d"),
        ("data licensing", "bot activity", "crowding", "turnover"),
        ("price-only control", "random timestamp control", "source outage regime"),
        ("backfilled search trends", "article publication time", "revised attention series"),
        ("services/data", "services/research/alpha_expression.py"),
        "NEW_EXPERIMENT_GAP",
    ),
    AlphaMethodSpec(
        "ict_liquidity_sweep_session",
        "ICT liquidity-sweep/session translation",
        ("ict_huddleston_primary", "ofi_cont_kukanov_stoikov"),
        "PRACTITIONER_TRANSLATION",
        "The practitioner claim is translated into stop-level breach, aggressive flow, replenishment, and fixed session interactions.",
        "A pre-registered sweep/replenishment definition predicts reversal better than sweep-only and random-session controls.",
        ("binance_usdm_websocket_stream",),
        ("15m", "1h", "4h"),
        ("spread spikes", "liquidation jumps", "latency", "definition multiplicity"),
        ("random-session placebo", "sweep-only control", "continuation sign control"),
        ("swing levels using future bars", "session timezone drift", "post-hoc confluence rules"),
        ("services/research/realtime_alpha_discovery.py", "services/research/session.py"),
        "FORWARD_EXPERIMENT_GAP",
    ),
    AlphaMethodSpec(
        "ict_fvg_mean_fill",
        "ICT fair-value-gap mean-fill translation",
        ("ict_huddleston_primary",),
        "PRACTITIONER_TRANSLATION",
        "A three-bar non-overlap after displacement is treated as a measurable price gap, not institutional intent.",
        "A fixed three-bar gap definition has a higher future fill rate and net fade return than matched random gaps.",
        ("binance_usdm_websocket_stream",),
        ("15m", "1h", "4h"),
        ("spread", "gap-through loss", "high turnover", "bar aggregation"),
        ("random-gap match", "trend-continuation control", "session exclusion"),
        ("bar close not yet known", "choosing gap threshold after outcomes", "ambiguous fill rule"),
        ("services/research/realtime_alpha_discovery.py", "services/research/backtest_engine"),
        "FORWARD_EXPERIMENT_GAP",
    ),
)


DISCOVERY_CONTROLS: tuple[DiscoveryControlSpec, ...] = (
    DiscoveryControlSpec(
        "dsr_selection_bias",
        ("dsr_bailey_lopez_de_prado",),
        "Winning-only selection and non-normal return inflation.",
        ("services/validation/deflated_sharpe.py", "services/research/trial_ledger.py"),
    ),
    DiscoveryControlSpec(
        "pbo_cscv",
        ("pbo_bailey_borwein_lopez_de_prado_zhu",),
        "Family-level backtest overfitting across tried configurations.",
        ("services/validation/deflated_sharpe.py", "services/research/validation"),
    ),
    DiscoveryControlSpec(
        "locked_oos_no_peek",
        ("dsr_bailey_lopez_de_prado", "pbo_bailey_borwein_lopez_de_prado_zhu"),
        "Outcome-informed tuning and reuse of the same holdout.",
        ("services/research/oos_validation.py", "services/research/trial_provenance.py"),
    ),
)


def _nonempty(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_unique(items: Sequence[object], attribute: str) -> None:
    values = [getattr(item, attribute, None) for item in items]
    if any(not _nonempty(value) for value in values) or len(values) != len(set(values)):
        raise MethodologyRegistryError(f"{attribute} values must be non-empty and unique")


def validate_methodology_registry(
    sources: Sequence[SourceSpec],
    methods: Sequence[AlphaMethodSpec],
    controls: Sequence[DiscoveryControlSpec],
) -> None:
    """Validate provenance and experiment completeness without IO."""
    _require_unique(sources, "source_id")
    _require_unique(methods, "method_id")
    _require_unique(controls, "control_id")
    source_ids = {item.source_id for item in sources}
    for item in sources:
        if not isinstance(item.evidence_tier, EvidenceTier):
            raise MethodologyRegistryError("source evidence tier is invalid")
        if item.practitioner_claim and item.evidence_tier != EvidenceTier.PRACTITIONER_PRIMARY:
            raise MethodologyRegistryError("practitioner claim cannot be labelled academic")
        if not item.authors or any(not _nonempty(value) for value in item.authors):
            raise MethodologyRegistryError("source authors are required")
        if not 1900 <= item.year <= 2100:
            raise MethodologyRegistryError("source year is invalid")
        if not item.url.startswith("https://") or not _nonempty(item.scope_note):
            raise MethodologyRegistryError("source URL and scope are required")
        if item.doi is not None and not item.doi.startswith("10."):
            raise MethodologyRegistryError("source DOI is invalid")
    for item in methods:
        if item.portability not in _PORTABILITIES:
            raise MethodologyRegistryError("method portability is invalid")
        if item.system_disposition not in _SYSTEM_DISPOSITIONS:
            raise MethodologyRegistryError("method system disposition is invalid")
        scalar_fields = (item.name, item.economic_mechanism, item.testable_claim)
        tuple_fields = (
            item.source_ids,
            item.required_channel_ids,
            item.holding_horizons,
            item.cost_risks,
            item.falsification_tests,
            item.leakage_risks,
            item.system_modules,
        )
        if any(not _nonempty(value) for value in scalar_fields):
            raise MethodologyRegistryError("method narrative fields are required")
        if any(not values or any(not _nonempty(value) for value in values) for values in tuple_fields):
            raise MethodologyRegistryError("method experiment fields are required")
        if not set(item.source_ids).issubset(source_ids):
            raise MethodologyRegistryError("method references an unknown source")
    for item in controls:
        if not item.source_ids or not set(item.source_ids).issubset(source_ids):
            raise MethodologyRegistryError("control references an unknown source")
        if not _nonempty(item.failure_prevented) or not item.system_modules:
            raise MethodologyRegistryError("control implementation mapping is required")


def _source_tier_score(method: AlphaMethodSpec, sources: Mapping[str, SourceSpec]) -> int:
    scores = {
        EvidenceTier.PEER_REVIEWED: 40,
        EvidenceTier.WORKING_PAPER: 30,
        EvidenceTier.PRACTITIONER_PRIMARY: 10,
    }
    return max(scores[sources[source_id].evidence_tier] for source_id in method.source_ids)


def audit_methodology(
    channel_availability: Mapping[str, str],
) -> tuple[MethodologyAuditRow, ...]:
    """Rank experiment supply by evidence and data readiness, never profitability."""
    validate_methodology_registry(SOURCES, ALPHA_METHODS, DISCOVERY_CONTROLS)
    sources = {item.source_id: item for item in SOURCES}
    rows: list[MethodologyAuditRow] = []
    for item in ALPHA_METHODS:
        missing = tuple(
            sorted(
                channel_id
                for channel_id in item.required_channel_ids
                if channel_id not in channel_availability
                or str(channel_availability[channel_id]).startswith("BLOCKED")
                or channel_availability[channel_id] == "PUBLIC_STREAM_REQUIRED"
            )
        )
        non_historical = tuple(
            sorted(
                channel_id
                for channel_id in item.required_channel_ids
                if channel_id in channel_availability
                and channel_availability[channel_id] != "REAL_HISTORY"
            )
        )
        if missing:
            readiness = MethodReadiness.BLOCKED_MISSING_INPUT
            readiness_score = 0
        elif non_historical:
            readiness = MethodReadiness.FORWARD_PREREG_REQUIRED
            readiness_score = 10
        else:
            readiness = MethodReadiness.HISTORICAL_DIAGNOSTIC_READY
            readiness_score = 30
        portability_score = {
            "DIRECT_CRYPTO": 15,
            "CROSS_ASSET_PRIOR": 5,
            "PRACTITIONER_TRANSLATION": 0,
        }[item.portability]
        disposition_score = {
            "NEW_EXPERIMENT_GAP": 15,
            "FORWARD_EXPERIMENT_GAP": 10,
            "EXISTING_TESTED_FAMILY": -5,
            "EXISTING_FALSIFIED_IMPLEMENTATION": -10,
            "EXISTING_BLOCKED_IMPLEMENTATION": -8,
        }[item.system_disposition]
        tiers = tuple(
            sorted({sources[source_id].evidence_tier.value for source_id in item.source_ids})
        )
        rows.append(
            MethodologyAuditRow(
                method_id=item.method_id,
                readiness=readiness,
                missing_channels=missing,
                non_historical_channels=non_historical,
                evidence_tiers=tiers,
                system_disposition=item.system_disposition,
                priority_score=(
                    _source_tier_score(item, sources)
                    + readiness_score
                    + portability_score
                    + disposition_score
                ),
            )
        )
    ranked = sorted(rows, key=lambda row: (-row.priority_score, row.method_id))
    return tuple(replace(row, priority_rank=index) for index, row in enumerate(ranked, 1))


validate_methodology_registry(SOURCES, ALPHA_METHODS, DISCOVERY_CONTROLS)


__all__ = [
    "ALPHA_METHODS",
    "DISCOVERY_CONTROLS",
    "SOURCES",
    "AlphaMethodSpec",
    "DiscoveryControlSpec",
    "EvidenceTier",
    "MethodReadiness",
    "MethodologyAuditRow",
    "MethodologyRegistryError",
    "SourceSpec",
    "audit_methodology",
    "validate_methodology_registry",
]
