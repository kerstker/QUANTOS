from services.data.channel_registry import channel_specs
from services.data.research_surfaces import (
    CHANNEL_CAPABILITIES,
    RESEARCH_SURFACES,
    ChannelCapability,
    SurfaceReadiness,
    audit_research_surfaces,
    validate_research_surface_registry,
)


def _declared_availability() -> dict[str, str]:
    return {spec.channel_id: spec.availability for spec in channel_specs()}


def test_builtin_surface_registry_is_valid_and_references_real_channels() -> None:
    validate_research_surface_registry(
        channel_specs(), RESEARCH_SURFACES, CHANNEL_CAPABILITIES
    )

    assert len({row.surface_id for row in RESEARCH_SURFACES}) == len(
        RESEARCH_SURFACES
    )
    assert all(row.required_capabilities for row in RESEARCH_SURFACES)
    assert all(row.payer_ids for row in RESEARCH_SURFACES)


def test_current_data_surface_audit_keeps_orthogonal_gaps_honest() -> None:
    rows = {
        row.surface_id: row
        for row in audit_research_surfaces(_declared_availability())
    }

    micro = rows["multi_venue_trade_l2"]
    assert micro.readiness is SurfaceReadiness.FORWARD_ONLY
    assert micro.distinct_venue_count == 2
    assert ChannelCapability.RECEIVE_TIME in micro.available_capabilities
    assert "bybit_linear_websocket_events" in micro.non_historical_channels

    order_flow = rows["order_flow_dynamics"]
    assert order_flow.readiness is SurfaceReadiness.PARTIAL
    assert order_flow.missing_capabilities == (ChannelCapability.CANCEL_RATE,)
    assert ChannelCapability.BOOK_RECOVERY in order_flow.available_capabilities

    borrow = rows["borrow_survivorship"]
    assert borrow.readiness is SurfaceReadiness.MISSING
    assert set(borrow.missing_capabilities) >= {
        ChannelCapability.BORROW_RATE,
        ChannelCapability.BORROW_AVAILABILITY,
    }


def test_recent_or_snapshot_channels_never_masquerade_as_history() -> None:
    rows = audit_research_surfaces(_declared_availability())
    by_id = {row.surface_id: row for row in rows}

    assert by_id["positioning_liquidation"].readiness is (
        SurfaceReadiness.FORWARD_ONLY
    )
    assert "binance_usdm_websocket_stream" in (
        by_id["positioning_liquidation"].non_historical_channels
    )
    assert all("PROFIT" not in row.readiness.value for row in rows)


def test_audit_is_deterministic_and_fail_closed_for_missing_artifacts() -> None:
    availability = _declared_availability()
    first = audit_research_surfaces(availability)
    second = audit_research_surfaces(dict(reversed(tuple(availability.items()))))
    assert first == second

    blocked = {channel_id: "BLOCKED_MISSING" for channel_id in availability}
    assert all(
        row.readiness is SurfaceReadiness.MISSING
        for row in audit_research_surfaces(blocked)
    )
