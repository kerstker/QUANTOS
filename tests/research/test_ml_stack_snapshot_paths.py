from __future__ import annotations

import pytest

from tools.ml_stack_research import snapshot_path


def test_ml_stack_snapshot_defaults_are_asset_scoped() -> None:
    assert snapshot_path("crypto").replace("\\", "/").endswith(
        "data/cache/ml_stack_snapshot_crypto.json"
    )
    assert snapshot_path("fx").replace("\\", "/").endswith(
        "data/cache/ml_stack_snapshot_fx.json"
    )


def test_ml_stack_snapshot_explicit_output_is_opt_in() -> None:
    assert snapshot_path("fx", "data/cache/intentional.json") == "data/cache/intentional.json"


def test_ml_stack_snapshot_rejects_unknown_asset() -> None:
    with pytest.raises(ValueError, match="unsupported asset"):
        snapshot_path("equities")
