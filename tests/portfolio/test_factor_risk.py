import math
import pytest
from quantos.contracts.factor import FactorRiskModel
from services.portfolio.factor_risk import (
    total_covariance, factor_exposures, risk_decomposition,
)


def _model():
    return FactorRiskModel(
        factor_ids=("mkt", "size"),
        loadings={"a": (1.0, 0.2), "b": (0.8, -0.5), "c": (0.3, 0.9)},
        factor_cov=((0.04, 0.005), (0.005, 0.02)),
        specific_var={"a": 0.01, "b": 0.02, "c": 0.015},
    )


def _quad(w, cov):
    n = len(w)
    return sum(w[i] * cov[i][j] * w[j] for i in range(n) for j in range(n))


def test_total_covariance_is_symmetric_and_correct_dimension():
    cov = total_covariance(_model(), ["a", "b", "c"])
    assert len(cov) == 3 and all(len(r) == 3 for r in cov)
    for i in range(3):
        for j in range(3):
            assert abs(cov[i][j] - cov[j][i]) < 1e-12
    assert cov[0][0] == pytest.approx(0.04 + 0.002 + 0.0008 + 0.01, abs=1e-12)


def test_factor_exposures_equal_B_transpose_w():
    w = [0.5, 0.3, 0.2]
    e = factor_exposures(w, _model(), ["a", "b", "c"])
    assert e["mkt"] == pytest.approx(0.5 * 1.0 + 0.3 * 0.8 + 0.2 * 0.3)
    assert e["size"] == pytest.approx(0.5 * 0.2 + 0.3 * -0.5 + 0.2 * 0.9)


def test_decomposition_identity_factor_plus_specific_equals_total():
    w = [0.5, -0.3, 0.2]                       # long-short
    d = risk_decomposition(w, _model(), ["a", "b", "c"])
    assert d["factor_var"] + d["specific_var"] == pytest.approx(d["total_var"], abs=1e-12)
    cov = total_covariance(_model(), ["a", "b", "c"])
    assert d["total_var"] == pytest.approx(_quad(w, cov), abs=1e-12)
    assert sum(d["by_factor"].values()) == pytest.approx(d["factor_var"], abs=1e-12)
