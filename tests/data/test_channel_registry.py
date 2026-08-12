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
    onchain = spec_by_channel("coinmetrics_community_onchain")
    assert onchain.availability == "REAL_HISTORY"
    assert onchain.endpoint_class == "public_rest_noncommercial"
    assert set(onchain.fields) == {"active_addresses", "transaction_count", "mvrv"}
    defillama = spec_by_channel("defillama_free_chain_tvl")
    assert defillama.availability == "REAL_DIAGNOSTIC"
    assert defillama.endpoint_class == "public_rest"
    assert set(defillama.fields) == {
        "chain_tvl_usd", "chain_tvl_growth", "chain_tvl_acceleration",
    }
    stablecoins = spec_by_channel("defillama_global_stablecoin_supply")
    assert stablecoins.availability == "REAL_DIAGNOSTIC"
    assert stablecoins.endpoint_class == "public_rest"
    assert "supply_acceleration" in stablecoins.fields
    assert spec_by_channel("cross_exchange_book").availability == "REAL_SNAPSHOT"
    hyperliquid = spec_by_channel("hyperliquid_public_perp")
    assert hyperliquid.availability == "REAL_HISTORY"
    assert hyperliquid.endpoint_class == "public_rest"
    assert {
        "funding_rate", "premium", "mark_price", "oracle_price",
        "open_interest", "day_notional_volume",
    } <= set(hyperliquid.fields)
    cftc = spec_by_channel("cftc_tff_crypto_positioning")
    assert cftc.availability == "REAL_HISTORY"
    assert cftc.endpoint_class == "public_rest"
    assert {
        "leveraged_money_net_pct", "asset_manager_net_pct", "dealer_net_pct",
    } <= set(cftc.fields)
    book_depth = spec_by_channel("binance_usdm_book_depth_archive")
    assert book_depth.availability == "REAL_DIAGNOSTIC"
    assert book_depth.endpoint_class == "public_archive"
    assert "bounded_quality_quarantine" in book_depth.fields
    assert "UNASSESSED" in book_depth.notes


def test_family_lookup_does_not_promote_diagnostic_data():
    rows = channels_for_family("volatility_risk_premium")
    assert [row.channel_id for row in rows] == ["deribit_options_vol"]
    assert rows[0].availability == "REAL_DIAGNOSTIC"
    assert [row.channel_id for row in channels_for_family("institutional_positioning")] == [
        "cftc_tff_crypto_positioning"
    ]
