import ast
from pathlib import Path

import pytest

from services.data.binance_realtime_store import BinanceRealtimeStore
from services.features.value_store import SqliteFeatureValueStore
from services.features.binance_realtime_projection import CORE_FEATURE_IDS, FEATURE_IDS
from tools import binance_realtime_features as realtime


def _snapshot(instrument_id: str, index: int) -> dict:
    observed = 10_000 + index * 10
    price = 100.0 + index * 0.01
    return {
        "schema_version": 1,
        "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
        "venue": "binance_usdm",
        "instrument_id": instrument_id,
        "event_time_ms": observed - 1,
        "knowledge_time_ms": observed,
        "observed_at_ms": observed,
        "quality": {
            "complete_core": True,
            "missing_channels": [],
            "private_access": False,
            "broker_connectivity": False,
            "order_submission": False,
        },
        "book": {"mid": price, "spread_bps": 1.0 + index * 0.001},
        "depth20": {
            "bids": [
                [str(price - 0.01 - level * 0.01), str(100 + level + index)]
                for level in range(5)
            ],
            "asks": [
                [str(price + 0.01 + level * 0.01), str(110 + level + index)]
                for level in range(5)
            ],
        },
        "mark": {
            "mark_price": price + 0.01,
            "index_price": price,
            "funding_rate": 0.0001 + index * 0.0000001,
        },
        "trade_window": {
            "buy_taker_notional": 100 + index,
            "sell_taker_notional": 90 + index,
        },
        "liquidation_window": {
            "long_liquidation_notional": index % 3,
            "short_liquidation_notional": index % 2,
        },
    }


def test_execute_materializes_complete_drift_ready_feature_cache(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA", "BBB"))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        for index in range(300):
            for ticker in ("AAA", "BBB"):
                source.append_snapshot(_snapshot(f"{ticker}USDT", index))
        incomplete = _snapshot("AAAUSDT", 301)
        incomplete["quality"]["complete_core"] = False
        incomplete["quality"]["missing_channels"] = ["depth20"]
        source.append_snapshot(incomplete)
    finally:
        source.close()

    feature_path = tmp_path / "features.db"
    result = realtime.execute(
        now_ms=20_000,
        source_store_path=str(source_path),
        feature_store_path=str(feature_path),
        batch_store_path=str(tmp_path / "batches.db"),
        status_path=str(tmp_path / "status.json"),
        source_row_limit=601,
    )
    assert result["status"] == "PASS"
    assert result["features"]["instrument_count"] == 2
    assert result["features"]["new_value_count"] == 2 * 300 * len(FEATURE_IDS)
    assert result["drift"]["series_count"] == 2 * len(CORE_FEATURE_IDS)
    assert result["drift"]["structurally_ready"] is True
    assert result["drift"]["calibration"]["status"] == "READY"
    assert result["drift"]["calibration"]["calibration_id"]
    assert result["drift"]["policy"]["calibration"] == (
        "EMPIRICALLY_CALIBRATED_PIT_V1"
    )
    assert result["drift"]["policy"]["warn_ks"] < (
        result["drift"]["policy"]["block_ks"]
    )
    assert result["batch"]["chain_valid"] is True
    assert result["agent_input"]["automatic_action"] is False

    store = SqliteFeatureValueStore(str(feature_path))
    try:
        rows = store.as_of(
            "rt_mid_price",
            realtime.FEATURE_VERSION,
            "AAAUSDT",
            20_000,
        )
        assert len(rows) == 300
    finally:
        store.close()


def test_source_snapshot_selection_keeps_incomplete_restart_rows_as_evidence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(realtime, "FORWARD_MAJOR_TICKERS", ("AAA", "BBB"))
    source_path = tmp_path / "source.db"
    source = BinanceRealtimeStore(str(source_path), create=True)
    try:
        complete = _snapshot("AAAUSDT", 1)
        source.append_snapshot(complete)
        incomplete = _snapshot("BBBUSDT", 1)
        incomplete["quality"]["complete_core"] = False
        incomplete["quality"]["missing_channels"] = ["mark"]
        source.append_snapshot(incomplete)

        with pytest.raises(
            realtime.BinanceRealtimeFeatureError,
            match="missing instruments",
        ):
            realtime._source_snapshots(source, limit=2)

        assert source.row_count() == 2
        assert source.chain_valid() is True
    finally:
        source.close()


def test_realtime_feature_worker_has_no_network_or_execution_surface():
    source = Path(realtime.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        str(node.module).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imports.isdisjoint({"urllib", "requests", "websocket", "ccxt", "binance"})
    for token in ("api_key", "api_secret", "submit_order", "place_order"):
        assert token not in source
