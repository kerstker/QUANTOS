"""Regression locks for domain-neutral validation ownership."""

import ast
from pathlib import Path


def test_research_validation_compatibility_exports_preserve_identity():
    from services.research.backtest_engine.metrics import sharpe as legacy_sharpe
    from services.research.regime import label_regimes_by_vol as legacy_regime
    from services.research.validation.cpcv import (
        cpcv_oos_distribution as legacy_cpcv_oos_distribution,
        cpcv_summary as legacy_cpcv_summary,
    )
    from services.research.validation.deflated_sharpe import pbo_cscv as legacy_pbo
    from services.research.validation.gate import ValidationGate as LegacyGate
    from services.research.validation.walk_forward import (
        purged_kfold as legacy_purged_kfold,
        walk_forward_splits as legacy_walk_forward_splits,
    )
    from services.validation.deflated_sharpe import pbo_cscv
    from services.validation.cpcv import cpcv_oos_distribution, cpcv_summary
    from services.validation.gate import ValidationGate
    from services.validation.performance import sharpe
    from services.validation.regime import label_regimes_by_vol
    from services.validation.walk_forward import purged_kfold, walk_forward_splits

    assert legacy_sharpe is sharpe
    assert legacy_regime is label_regimes_by_vol
    assert legacy_pbo is pbo_cscv
    assert legacy_cpcv_oos_distribution is cpcv_oos_distribution
    assert legacy_cpcv_summary is cpcv_summary
    assert LegacyGate is ValidationGate
    assert legacy_purged_kfold is purged_kfold
    assert legacy_walk_forward_splits is walk_forward_splits


def test_factor_statistics_and_factor_set_compatibility_preserve_identity():
    from services.alpha.factor_set import select_synergistic_set
    from services.research.factor_engine.domain.model import compute_ic as legacy_ic
    from services.research.factor_set import select_synergistic_set as legacy_selection
    from services.validation.factor_statistics import compute_ic

    assert legacy_ic is compute_ic
    assert legacy_selection is select_synergistic_set


def test_reality_check_compatibility_exports_preserve_identity():
    from services.research.validation.reality_check import (
        RealityCheckInputError as LegacyInputError,
        SplitMix64 as LegacySplitMix64,
        block_length_grid_v1 as legacy_block_length_grid_v1,
        compute_reality_check_and_spa as legacy_compute,
        iter_stationary_bootstrap_indices as legacy_iter_indices,
    )
    from services.validation.reality_check import (
        RealityCheckInputError,
        SplitMix64,
        block_length_grid_v1,
        compute_reality_check_and_spa,
        iter_stationary_bootstrap_indices,
    )

    assert LegacyInputError is RealityCheckInputError
    assert LegacySplitMix64 is SplitMix64
    assert legacy_block_length_grid_v1 is block_length_grid_v1
    assert legacy_compute is compute_reality_check_and_spa
    assert legacy_iter_indices is iter_stationary_bootstrap_indices


def test_reality_check_compatibility_module_contains_no_second_implementation():
    repository_root = Path(__file__).resolve().parents[2]
    compatibility_path = repository_root / "services" / "research" / "validation" / "reality_check.py"
    tree = ast.parse(compatibility_path.read_text(encoding="utf-8"))

    assert not [node for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert imports
    assert {node.module for node in imports} == {"services.validation.reality_check"}
