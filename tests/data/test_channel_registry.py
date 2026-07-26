from services.data.channel_registry import (DATA_CHANNELS, channels_for_family,
                                             spec_by_channel)


def test_channel_ids_are_unique_and_have_explicit_temporal_contracts():
    assert len({item.channel_id for item in DATA_CHANNELS}) == len(DATA_CHANNELS)
    assert all(item.fields and item.artifact_path for item in DATA_CHANNELS)
    assert spec_by_channel("binance_liquidations").availability == "PUBLIC_STREAM_REQUIRED"
    assert spec_by_channel("cross_exchange_book").availability == "REAL_SNAPSHOT"


def test_family_lookup_does_not_promote_diagnostic_data():
    rows = channels_for_family("volatility_risk_premium")
    assert [row.channel_id for row in rows] == ["deribit_options_vol"]
    assert rows[0].availability == "REAL_DIAGNOSTIC"
