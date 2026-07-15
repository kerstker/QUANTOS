"""Regression locks for domain-neutral validation ownership."""


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
