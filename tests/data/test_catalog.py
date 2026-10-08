"""Phase 2 / G2 — data-catalog backing computations come from the real layer."""
from services.data.catalog import (
    DATA_BACKEND, dataset_content_hash, verify_point_in_time_safe,
)


def test_content_hash_is_real_reproducible_and_dataset_specific():
    h1 = dataset_content_hash("ds-market-daily-bars", "2026.06.12.001")
    h2 = dataset_content_hash("ds-market-daily-bars", "2026.06.12.001")
    other = dataset_content_hash("ds-corporate-actions", "2026.06.12.001")
    assert h1 == h2 and len(h1) == 16   # reproducible (CT-3)
    assert h1 != other                  # derived from data, not a constant


def test_point_in_time_safe_is_operationally_verified():
    assert verify_point_in_time_safe("ds-market-daily-bars") is True


def test_data_backend_marker():
    assert DATA_BACKEND == "PIT_DATA_LAYER_v1"
