import ast
from pathlib import Path

import pytest

from tools.dry_run_combo_riskparity_multi import _combine_sleeves


ROOT = Path(__file__).resolve().parents[2]


def test_combined_target_weights_remain_unscaled_without_calibrated_evidence() -> None:
    combined = _combine_sleeves(
        {0: 0.5, 1: -0.5},
        {2: 0.5, 3: -0.5},
        carry_weight=0.60,
        crowd_weight=0.40,
    )

    assert combined == pytest.approx(
        {0: 0.30, 1: -0.30, 2: 0.20, 3: -0.20}
    )
    assert sum(abs(value) for value in combined.values()) == pytest.approx(1.00)


def test_driver_records_null_result_without_wiring_recommendation_into_targets() -> None:
    path = ROOT / "tools" / "dry_run_combo_riskparity_multi.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    combine_calls = [
        node
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_combine_sleeves"
        )
    ]
    applies_multiplier = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_combine_sleeves"
        and any(
            keyword.arg == "gross_multiplier"
            for keyword in node.keywords
        )
        for node in ast.walk(tree)
    )

    assert combine_calls
    assert not applies_multiplier
    source = path.read_text(encoding="utf-8")
    assert '"activation": "DISABLED_BY_EXECUTABLE_CONTROL"' in source
    assert '"correlation_scaled_periods": 0' in source
