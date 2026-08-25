"""Institutional research-surface capability registry and readiness audit.

The channel registry says which datasets exist.  This module asks the stricter
question needed for mechanism-first research: whether those channels jointly
provide the clocks and observables required to test who pays, and whether the
evidence is historical, forward-only, partial, or absent.

Readiness never implies profitability, Alpha promotion, or trading permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from services.data.channel_registry import DataChannelSpec, channel_specs


class ResearchSurfaceRegistryError(ValueError):
    """Raised when a surface could overstate data readiness."""


class ChannelCapability(str, Enum):
    EVENT_TIME = "EVENT_TIME"
    RECEIVE_TIME = "RECEIVE_TIME"
    PROCESS_TIME = "PROCESS_TIME"
    TRADE_PRINTS = "TRADE_PRINTS"
    L2_BOOK = "L2_BOOK"
    TAKER_FLOW = "TAKER_FLOW"
    CANCEL_RATE = "CANCEL_RATE"
    BOOK_RECOVERY = "BOOK_RECOVERY"
    FUNDING_TERM_STRUCTURE = "FUNDING_TERM_STRUCTURE"
    BASIS_TERM_STRUCTURE = "BASIS_TERM_STRUCTURE"
    OPEN_INTEREST = "OPEN_INTEREST"
    LIQUIDATIONS = "LIQUIDATIONS"
    POSITION_CONCENTRATION = "POSITION_CONCENTRATION"
    BORROW_RATE = "BORROW_RATE"
    BORROW_AVAILABILITY = "BORROW_AVAILABILITY"
    DELISTING_HISTORY = "DELISTING_HISTORY"
    HISTORICAL_CONSTITUENTS = "HISTORICAL_CONSTITUENTS"
    FEE_SCHEDULE = "FEE_SCHEDULE"
    REBATE_SCHEDULE = "REBATE_SCHEDULE"
    RATE_LIMITS = "RATE_LIMITS"
    LOT_SIZE_RULES = "LOT_SIZE_RULES"
    ONCHAIN_EXCHANGE_FLOW = "ONCHAIN_EXCHANGE_FLOW"
    STABLECOIN_LIQUIDITY = "STABLECOIN_LIQUIDITY"
    MACRO_VINTAGE = "MACRO_VINTAGE"
    ETF_FLOW = "ETF_FLOW"
    OPTIONS_VOLATILITY = "OPTIONS_VOLATILITY"
    CROSS_ASSET_REGIME = "CROSS_ASSET_REGIME"


class SurfaceReadiness(str, Enum):
    MISSING = "MISSING"
    PARTIAL = "PARTIAL"
    FORWARD_ONLY = "FORWARD_ONLY"
    HISTORICAL_READY = "HISTORICAL_READY"


@dataclass(frozen=True)
class ResearchSurfaceSpec:
    surface_id: str
    name: str
    payer_ids: tuple[str, ...]
    required_capabilities: tuple[ChannelCapability, ...]
    preferred_channel_ids: tuple[str, ...]
    min_distinct_venues: int
    mechanism_question: str


@dataclass(frozen=True)
class ResearchSurfaceAuditRow:
    surface_id: str
    readiness: SurfaceReadiness
    available_capabilities: tuple[ChannelCapability, ...]
    missing_capabilities: tuple[ChannelCapability, ...]
    contributing_channels: tuple[str, ...]
    non_historical_channels: tuple[str, ...]
    distinct_venue_count: int
    min_distinct_venues: int


# Capabilities are intentionally conservative.  For example, snapshots do not
# become L2 history, public OI is not borrow availability, and a stablecoin total
# is not an exchange-flow series.
CHANNEL_CAPABILITIES: Mapping[str, tuple[ChannelCapability, ...]] = {
    "binance_usdm_price": (ChannelCapability.EVENT_TIME,),
    "binance_usdm_funding": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.FUNDING_TERM_STRUCTURE,
    ),
    "binance_usdm_oi_metrics": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.OPEN_INTEREST,
        ChannelCapability.TAKER_FLOW,
        ChannelCapability.POSITION_CONCENTRATION,
    ),
    "bybit_linear_positioning": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.OPEN_INTEREST,
        ChannelCapability.POSITION_CONCENTRATION,
    ),
    "hyperliquid_public_perp": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.FUNDING_TERM_STRUCTURE,
        ChannelCapability.BASIS_TERM_STRUCTURE,
        ChannelCapability.OPEN_INTEREST,
    ),
    "binance_usdm_book_depth_archive": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.L2_BOOK,
    ),
    "binance_usdm_realtime_complete": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.RECEIVE_TIME,
        ChannelCapability.PROCESS_TIME,
        ChannelCapability.OPEN_INTEREST,
        ChannelCapability.POSITION_CONCENTRATION,
        ChannelCapability.LOT_SIZE_RULES,
    ),
    "binance_usdm_websocket_stream": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.RECEIVE_TIME,
        ChannelCapability.PROCESS_TIME,
        ChannelCapability.TRADE_PRINTS,
        ChannelCapability.L2_BOOK,
        ChannelCapability.TAKER_FLOW,
        ChannelCapability.LIQUIDATIONS,
    ),
    "binance_usdm_systemic_risk": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.BASIS_TERM_STRUCTURE,
    ),
    "binance_usdm_rpi_depth": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.L2_BOOK,
    ),
    "deribit_term_structure": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.BASIS_TERM_STRUCTURE,
    ),
    "deribit_options_vol": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.OPTIONS_VOLATILITY,
    ),
    "defillama_global_stablecoin_supply": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.STABLECOIN_LIQUIDITY,
    ),
    "fred_market_regime": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.CROSS_ASSET_REGIME,
    ),
    "alfred_vintage": (
        ChannelCapability.EVENT_TIME,
        ChannelCapability.MACRO_VINTAGE,
    ),
}


RESEARCH_SURFACES: tuple[ResearchSurfaceSpec, ...] = (
    ResearchSurfaceSpec(
        "multi_venue_trade_l2",
        "Multi-venue trades and historical L2",
        ("URGENT_LARGE_ORDER", "CAPITAL_CONSTRAINED_CROSS_VENUE"),
        (
            ChannelCapability.EVENT_TIME,
            ChannelCapability.RECEIVE_TIME,
            ChannelCapability.PROCESS_TIME,
            ChannelCapability.TRADE_PRINTS,
            ChannelCapability.L2_BOOK,
        ),
        ("binance_usdm_websocket_stream",),
        2,
        "Which venue leads, who demands immediacy, and is the lag executable after latency?",
    ),
    ResearchSurfaceSpec(
        "order_flow_dynamics",
        "Order-flow, cancellation, and book recovery",
        ("URGENT_LARGE_ORDER", "PASSIVE_RULE_BASED_FLOW"),
        (
            ChannelCapability.EVENT_TIME,
            ChannelCapability.RECEIVE_TIME,
            ChannelCapability.PROCESS_TIME,
            ChannelCapability.TRADE_PRINTS,
            ChannelCapability.L2_BOOK,
            ChannelCapability.TAKER_FLOW,
            ChannelCapability.CANCEL_RATE,
            ChannelCapability.BOOK_RECOVERY,
        ),
        ("binance_usdm_websocket_stream", "binance_usdm_book_depth_archive"),
        1,
        "Does urgent signed flow exhaust or replenish liquidity before the price label?",
    ),
    ResearchSurfaceSpec(
        "positioning_liquidation",
        "OI, concentration, taker flow, and liquidation events",
        ("LEVERAGED_CROWD", "FORCED_LIQUIDATOR"),
        (
            ChannelCapability.EVENT_TIME,
            ChannelCapability.OPEN_INTEREST,
            ChannelCapability.TAKER_FLOW,
            ChannelCapability.POSITION_CONCENTRATION,
            ChannelCapability.LIQUIDATIONS,
        ),
        (
            "binance_usdm_oi_metrics",
            "bybit_linear_positioning",
            "binance_usdm_websocket_stream",
        ),
        1,
        "When does leveraged crowding become forced flow rather than a reusable price formula?",
    ),
    ResearchSurfaceSpec(
        "funding_basis_curve",
        "Funding and spot-perpetual-delivery basis curves",
        ("HEDGING_DEMAND", "CAPITAL_CONSTRAINED_CROSS_VENUE"),
        (
            ChannelCapability.EVENT_TIME,
            ChannelCapability.FUNDING_TERM_STRUCTURE,
            ChannelCapability.BASIS_TERM_STRUCTURE,
            ChannelCapability.OPEN_INTEREST,
        ),
        (
            "binance_usdm_funding",
            "binance_usdm_oi_metrics",
            "hyperliquid_public_perp",
            "deribit_term_structure",
        ),
        2,
        "Are financing and hedge-demand premia persistent across venues and maturities?",
    ),
    ResearchSurfaceSpec(
        "borrow_survivorship",
        "Borrow, delistings, and historical constituents",
        ("CAPITAL_CONSTRAINED_CROSS_VENUE", "SPECULATIVE_LOTTERY_DEMAND"),
        (
            ChannelCapability.BORROW_RATE,
            ChannelCapability.BORROW_AVAILABILITY,
            ChannelCapability.DELISTING_HISTORY,
            ChannelCapability.HISTORICAL_CONSTITUENTS,
        ),
        (),
        1,
        "Can the short leg be borrowed in the actual historical universe at the claimed price?",
    ),
    ResearchSurfaceSpec(
        "execution_economics",
        "Fees, rebates, limits, and lot-size rules",
        ("URGENT_LARGE_ORDER", "PASSIVE_RULE_BASED_FLOW"),
        (
            ChannelCapability.FEE_SCHEDULE,
            ChannelCapability.REBATE_SCHEDULE,
            ChannelCapability.RATE_LIMITS,
            ChannelCapability.LOT_SIZE_RULES,
        ),
        ("binance_usdm_realtime_complete",),
        1,
        "Does the gross edge survive the venue contract actually available to the executor?",
    ),
    ResearchSurfaceSpec(
        "onchain_stablecoin_flow",
        "Exchange flows and stablecoin liquidity",
        ("SLOW_INFORMATION_REACTOR", "PASSIVE_RULE_BASED_FLOW"),
        (
            ChannelCapability.ONCHAIN_EXCHANGE_FLOW,
            ChannelCapability.STABLECOIN_LIQUIDITY,
        ),
        ("defillama_global_stablecoin_supply",),
        1,
        "Does verifiable capital movement lead market liquidity rather than restate price?",
    ),
    ResearchSurfaceSpec(
        "macro_etf_options",
        "Macro vintages, ETF flows, options volatility, and cross-asset regime",
        ("HEDGING_DEMAND", "PASSIVE_RULE_BASED_FLOW"),
        (
            ChannelCapability.MACRO_VINTAGE,
            ChannelCapability.ETF_FLOW,
            ChannelCapability.OPTIONS_VOLATILITY,
            ChannelCapability.CROSS_ASSET_REGIME,
        ),
        ("alfred_vintage", "deribit_options_vol", "fred_market_regime"),
        1,
        "Which external hedging and allocation state changes the payoff of existing mechanisms?",
    ),
)


def _is_active(availability: str | None) -> bool:
    if availability is None:
        return False
    value = str(availability)
    return not value.startswith("BLOCKED") and value != "PUBLIC_STREAM_REQUIRED"


def validate_research_surface_registry(
    channels: Sequence[DataChannelSpec],
    surfaces: Sequence[ResearchSurfaceSpec],
    capabilities: Mapping[str, Sequence[ChannelCapability]],
) -> None:
    channel_ids = {item.channel_id for item in channels}
    surface_ids = [item.surface_id for item in surfaces]
    if len(surface_ids) != len(set(surface_ids)) or any(not value.strip() for value in surface_ids):
        raise ResearchSurfaceRegistryError("surface ids must be non-empty and unique")
    if not set(capabilities).issubset(channel_ids):
        raise ResearchSurfaceRegistryError("capabilities reference an unknown channel")
    for values in capabilities.values():
        if len(values) != len(set(values)) or any(
            not isinstance(value, ChannelCapability) for value in values
        ):
            raise ResearchSurfaceRegistryError("channel capabilities must be unique enum values")
    for item in surfaces:
        if not item.name.strip() or not item.mechanism_question.strip():
            raise ResearchSurfaceRegistryError("surface narratives are required")
        if not item.payer_ids or any(not value.strip() for value in item.payer_ids):
            raise ResearchSurfaceRegistryError("surface economic payers are required")
        if not item.required_capabilities or any(
            not isinstance(value, ChannelCapability)
            for value in item.required_capabilities
        ):
            raise ResearchSurfaceRegistryError("surface capabilities are required")
        if len(item.required_capabilities) != len(set(item.required_capabilities)):
            raise ResearchSurfaceRegistryError("surface capabilities must be unique")
        if not set(item.preferred_channel_ids).issubset(channel_ids):
            raise ResearchSurfaceRegistryError("surface references an unknown channel")
        if type(item.min_distinct_venues) is not int or item.min_distinct_venues <= 0:
            raise ResearchSurfaceRegistryError("surface venue minimum must be positive")


def audit_research_surfaces(
    channel_availability: Mapping[str, str],
) -> tuple[ResearchSurfaceAuditRow, ...]:
    """Audit mechanism observability without fetching data or evaluating returns."""
    channels = channel_specs()
    validate_research_surface_registry(channels, RESEARCH_SURFACES, CHANNEL_CAPABILITIES)
    channel_by_id = {item.channel_id: item for item in channels}
    rows: list[ResearchSurfaceAuditRow] = []
    for surface in sorted(RESEARCH_SURFACES, key=lambda item: item.surface_id):
        required = set(surface.required_capabilities)
        active_channels = tuple(
            channel_id
            for channel_id in surface.preferred_channel_ids
            if _is_active(channel_availability.get(channel_id))
            and required.intersection(CHANNEL_CAPABILITIES.get(channel_id, ()))
        )
        available = {
            capability
            for channel_id in active_channels
            for capability in CHANNEL_CAPABILITIES.get(channel_id, ())
            if capability in required
        }
        historical_channels = tuple(
            channel_id
            for channel_id in active_channels
            if channel_availability.get(channel_id) == "REAL_HISTORY"
        )
        historical = {
            capability
            for channel_id in historical_channels
            for capability in CHANNEL_CAPABILITIES.get(channel_id, ())
            if capability in required
        }
        venues = {
            channel_by_id[channel_id].venue
            for channel_id in active_channels
        }
        historical_venues = {
            channel_by_id[channel_id].venue
            for channel_id in historical_channels
        }
        missing = required - available
        if not available:
            readiness = SurfaceReadiness.MISSING
        elif missing or len(venues) < surface.min_distinct_venues:
            readiness = SurfaceReadiness.PARTIAL
        elif (
            historical == required
            and len(historical_venues) >= surface.min_distinct_venues
        ):
            readiness = SurfaceReadiness.HISTORICAL_READY
        else:
            readiness = SurfaceReadiness.FORWARD_ONLY
        rows.append(
            ResearchSurfaceAuditRow(
                surface_id=surface.surface_id,
                readiness=readiness,
                available_capabilities=tuple(sorted(available, key=lambda item: item.value)),
                missing_capabilities=tuple(sorted(missing, key=lambda item: item.value)),
                contributing_channels=tuple(sorted(active_channels)),
                non_historical_channels=tuple(
                    sorted(set(active_channels) - set(historical_channels))
                ),
                distinct_venue_count=len(venues),
                min_distinct_venues=surface.min_distinct_venues,
            )
        )
    return tuple(rows)


__all__ = [
    "CHANNEL_CAPABILITIES",
    "RESEARCH_SURFACES",
    "ChannelCapability",
    "ResearchSurfaceAuditRow",
    "ResearchSurfaceRegistryError",
    "ResearchSurfaceSpec",
    "SurfaceReadiness",
    "audit_research_surfaces",
    "validate_research_surface_registry",
]
