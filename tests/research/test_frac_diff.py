"""Fractional differentiation (tests-first).

Ordinary differencing (d=1) makes a series stationary but ERASES memory; the level (d=0)
keeps memory but is non-stationary. Fractional d in (0,1) trades the two: stationary enough
to model, while retaining predictive memory (Lopez de Prado). Pure, deterministic, PIT
(each output is a backward weighted sum).
"""
from services.research.frac_diff import frac_diff, ffd_weights


def test_d0_is_identity():
    s = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert frac_diff(s, 0.0) == s


def test_d1_is_first_difference():
    assert frac_diff([1.0, 2.0, 4.0, 7.0], 1.0) == [1.0, 2.0, 3.0]


def test_fractional_weights_decay_and_alternate():
    w = ffd_weights(0.5, thresh=1e-3)
    assert w[0] == 1.0 and w[1] < 0 and abs(w[1]) > abs(w[2])      # decaying magnitude


def test_fractional_output_nonempty_and_deterministic():
    s = [float(i * i % 13) for i in range(60)]
    a = frac_diff(s, 0.4, thresh=1e-3)
    b = frac_diff(s, 0.4, thresh=1e-3)
    assert len(a) > 0 and a == b
