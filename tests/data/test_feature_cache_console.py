"""The data console reads the persisted PIT feature cache as a real snapshot."""

from pathlib import Path

from quantos.contracts.feature import (
    FeatureDefinition,
    FeatureRegistryItem,
    FeatureStatus,
    FeatureValue,
)
from services.console.data_console import DefaultDataConsolePort
from services.features.registry_store import SqliteFeatureRegistryStore
from services.features.value_store import SqliteFeatureValueStore


def test_console_enumerates_persisted_feature_cache(tmp_path: Path):
    path = tmp_path / "feature-cache.db"
    registry = SqliteFeatureRegistryStore(str(path), create=True)
    values = SqliteFeatureValueStore(str(path), create=True)
    registry.register(
        FeatureRegistryItem(
            definition=FeatureDefinition(
                feature_id="fx_momentum_20",
                name="fx_momentum_20",
                formula="(ts_momentum :close 20)",
                inputs=("fx_pit_tradable_v1.close",),
            ),
            version="fx-pit-v1",
            owner="FX PIT research",
            status=FeatureStatus.VALIDATED,
        ),
        leakage_passed=True,
        code_version="feature_cache_v1",
    )
    values.add_many(
        [
            FeatureValue(
                feature_id="fx_momentum_20",
                instrument_id="EURUSD",
                event_time=100,
                knowledge_time=100,
                value=0.1,
            ),
            FeatureValue(
                feature_id="fx_momentum_20",
                instrument_id="USDJPY",
                event_time=100,
                knowledge_time=100,
                value=-0.2,
            ),
        ],
        version="fx-pit-v1",
    )
    registry.close()
    values.close()

    port = DefaultDataConsolePort(tmp_path / "missing-crypto.db", path)
    features = port.enumerate_real_features()

    assert len(features) == 1
    feature = features[0]
    assert feature["feature_id"] == "fx_momentum_20"
    assert feature["cache_provenance"] == "REAL_PIT_FEATURE_VALUE_CACHE_V1"
    assert feature["materialized_row_count"] == 2
    assert feature["materialized_instrument_count"] == 2
    assert feature["quality_score"] == 1.0
    # The second read is a cached, read-only snapshot rather than a recomputation.
    assert port.enumerate_real_features() == features

    series = port.enumerate_feature_distribution_series()
    assert series == {"fx_momentum_20": [(-0.2 + 0.1) / 2]}
    assert port.enumerate_feature_distribution_series() == series
