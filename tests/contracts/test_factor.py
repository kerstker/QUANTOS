import pytest
from quantos.contracts.factor import FactorRiskModel


def _model():
    return FactorRiskModel(
        factor_ids=("mkt", "size"),
        loadings={"a": (1.0, 0.2), "b": (0.8, -0.5)},
        factor_cov=((0.04, 0.0), (0.0, 0.02)),
        specific_var={"a": 0.01, "b": 0.02},
    )


def test_aligned_returns_rows_and_specific_in_order():
    B, spec = _model().aligned(["b", "a"])
    assert B == [[0.8, -0.5], [1.0, 0.2]]
    assert spec == [0.02, 0.01]


def test_missing_alpha_id_fails_closed():
    with pytest.raises(ValueError):
        _model().aligned(["a", "missing"])


def test_validates_loading_row_length_against_factor_count():
    with pytest.raises(Exception):
        FactorRiskModel(factor_ids=("mkt", "size"), loadings={"a": (1.0,)},
                        factor_cov=((0.04, 0.0), (0.0, 0.02)), specific_var={"a": 0.01})


def test_validates_factor_cov_is_square_symmetric():
    with pytest.raises(Exception):
        FactorRiskModel(factor_ids=("mkt", "size"), loadings={"a": (1.0, 0.2)},
                        factor_cov=((0.04, 0.01), (0.02, 0.02)), specific_var={"a": 0.01})


def test_rejects_negative_specific_var_and_nonfinite():
    with pytest.raises(Exception):
        FactorRiskModel(factor_ids=("mkt",), loadings={"a": (1.0,)},
                        factor_cov=((0.04,),), specific_var={"a": -0.01})
    with pytest.raises(Exception):
        FactorRiskModel(factor_ids=("mkt",), loadings={"a": (float("nan"),)},
                        factor_cov=((0.04,),), specific_var={"a": 0.01})
