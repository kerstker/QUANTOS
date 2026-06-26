"""Information-theoretic factor screening (tests-first).

IC measures LINEAR factor->return dependence; it is blind to non-linear relationships
(e.g. "big moves either way precede a rise"), where linear correlation ~ 0 yet the factor
is genuinely informative. Mutual information (histogram-based, quantile-binned) captures
that dependence. Pure-Python, deterministic, fail-closed.
"""
import statistics
from services.research.information import (
    mutual_information, normalized_mutual_information, entropy)

X = list(range(-20, 21))                       # symmetric around 0, 41 points
Y_QUAD = [v * v for v in X]                     # y = x^2: linear corr ~ 0 but fully dependent
Y_INDEP = [(i * 7) % 5 for i in range(41)]      # unrelated to x


def _pearson(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = statistics.pstdev(a) * len(a) ** 0.5
    db = statistics.pstdev(b) * len(b) ** 0.5
    return num / (da * db) if da > 0 and db > 0 else 0.0


def test_mutual_information_catches_what_linear_ic_misses():
    assert abs(_pearson(X, Y_QUAD)) < 0.05                 # linear IC is ~0 (symmetric quadratic)
    mi_quad = mutual_information(X, Y_QUAD, bins=5)
    mi_indep = mutual_information(X, Y_INDEP, bins=5)
    assert mi_quad is not None and mi_indep is not None
    assert mi_quad > 0.3                                    # the non-linear dependence IS detected
    assert mi_quad > mi_indep                               # and ranks above an unrelated series


def test_symmetric():
    assert mutual_information(X, Y_QUAD, bins=5) == mutual_information(Y_QUAD, X, bins=5)


def test_normalized_in_unit_range():
    nmi = normalized_mutual_information(X, Y_QUAD, bins=5)
    assert nmi is not None and 0.0 <= nmi <= 1.0
    # a variable with itself -> NMI == 1 (fully informative)
    assert normalized_mutual_information(X, X, bins=5) == 1.0


def test_independent_is_near_zero():
    # an (almost) independent pair has MI near 0 relative to the dependent case
    assert mutual_information(X, Y_INDEP, bins=5) < 0.5


def test_fail_closed_on_thin():
    assert mutual_information([1, 2, 3], [1, 2, 3], bins=5) is None     # < bins*2 -> None
    assert entropy([1.0], bins=5) is None
