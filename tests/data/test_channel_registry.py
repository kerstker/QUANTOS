from services.data.channel_registry import (DATA_CHANNELS, channels_for_family,
                                             spec_by_channel)


def test_channel_ids_are_unique_and_have_explicit_temporal_contracts():
    assert len({item.channel_id for item in DATA_CHANNELS}) == len(DATA_CHANNELS)
    assert all(item.fields and item.artifact_path for item in DATA_CHANNELS)
    assert spec_by_channel("binance_liquidations").availability == "PUBLIC_STREAM_REQUIRED"
    realtime = spec_by_channel("binance_usdm_realtime_complete")
    assert realtime.availability == "REAL_RECENT"
    assert {"global_ls", "top_ls", "taker_ls", "bid", "ask"} <= set(realtime.fields)
    stream = spec_by_channel("binance_usdm_websocket_stream")
    assert stream.endpoint_class == "public_websocket"
    assert "liquidation_orders" in stream.fields
    bybit_positioning = spec_by_channel("bybit_linear_positioning")
    assert bybit_positioning.availability == "REAL_HISTORY"
    assert bybit_positioning.endpoint_class == "public_rest"
    assert "long_short_ratio" in bybit_positioning.fields
    assert spec_by_channel("cross_exchange_book").availability == "REAL_SNAPSHOT"


def test_family_lookup_does_not_promote_diagnostic_data():
    rows = channels_for_family("volatility_risk_premium")
    assert [row.channel_id for row in rows] == ["deribit_options_vol"]
    assert rows[0].availability == "REAL_DIAGNOSTIC"
