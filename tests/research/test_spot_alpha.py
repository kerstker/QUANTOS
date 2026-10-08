from decimal import Decimal as D

import numpy as np
import pytest

from services.research.spot_alpha import SpotAccount, SpotRules, features, target, fit_mature_ridge


def rules():
    return SpotRules('BTCUSDT', 'USDT', D('.001'), D('.001'), D('100'), D('5'), D('1000000'), D('.01'), 5)


def test_fractional_spot_cash_not_perpetual_margin_and_exact_fees():
    account = SpotAccount()
    fill = account.rebalance(D('.25'), D('10000'), D('10000'), rules(), D('.001'), D('.0005'))
    assert fill['quantity'] == '0.037'
    assert account.quantity == D('.037')
    assert account.cash == D('1129.444815')
    assert account.fees == D('.370185')
    assert account.equity(D('10000')) == D('1499.444815')


def test_no_borrow_no_short_and_dust_is_not_deleted():
    account = SpotAccount()
    account.rebalance(D(1), D('10000'), D('10000'), rules(), D('.001'), D('.001'))
    assert account.cash >= 0
    quantity = account.quantity
    skipped = account.rebalance(D(0), D('1'), D('1'), rules(), D('.001'), D('.001'))
    assert skipped['status'] == 'SKIPPED_MINIMUM'
    assert account.quantity == quantity
    for weight in [D('-1'), D('NaN'), D('2')]:
        with pytest.raises(ValueError):
            account.rebalance(weight, D('100'), D('100'), rules(), D('.001'), D('.001'))


def test_loss_budget_latches_marked_losses_and_cannot_reenter():
    account = SpotAccount()
    account.rebalance(D('.25'), D('100'), D('100'), rules(), D('.001'), D('.001'))
    assert account.observe(D('50'), 1000)
    assert account.halted_at == 1000
    fill = account.rebalance(D('.25'), D('50'), D('50'), rules(), D('.001'), D('.001'))
    assert fill['side'] == 'SELL'
    assert account.quantity == 0
    account.rebalance(D('.25'), D('500'), D('500'), rules(), D('.001'), D('.001'))
    assert account.quantity == 0
    assert account.halted_at == 1000


def test_breakout_window_excludes_today_and_does_not_mutate_actual_position():
    closes = np.array([100.]*55 + [101.])
    assert target('B', closes, False, 0., None, .001, .0005) == .25
    assert target('B', np.array([100.]*55 + [99.]), True, .25, None, .001, .0005) == 0
    assert target('A', closes, False, 0., None, .001, .0005) == .25


def test_features_are_prefix_invariant_and_reject_nonpositive_volume():
    close = np.arange(100., 170.)
    bars = np.column_stack([close, close+1, close-1, close, np.ones(70)*10])
    original = features(bars)
    extreme = np.vstack([bars, [1e8, 2e8, 1., 1e8, 1e15]])
    np.testing.assert_equal(original, features(extreme)[:70])
    assert original[-1, 5] == 0
    bars[-1, 4] = 0
    assert np.isnan(features(bars)[-1]).all()


def test_ridge_hysteresis_uses_full_roundtrip_threshold():
    closes = np.ones(60)
    assert target('C', closes, False, 0., .0034, .001, .0005) == 0
    assert target('C', closes, False, 0., .0036, .001, .0005) == .25
    assert target('C', closes, True, .25, .0001, .001, .0005) == .25
    assert target('C', closes, True, .25, 0., .001, .0005) == 0


def test_ridge_excludes_unmatured_labels_and_is_prefix_invariant():
    x = np.tile(np.arange(800.)[:, None], (1, 7))
    prices = 100 + np.arange(800.)
    times = np.arange(800) * 1000
    assert fit_mature_ridge(x, prices, times, 730000) is None
    scaler, model, indices = fit_mature_ridge(x, prices, times, 731000)
    assert len(indices) == 730 and indices[-1] == 729
    before = model.predict(scaler.transform(x[730:731]))
    prices[731:] = 1e12
    x[731:] = -1e12
    scaler2, model2, indices2 = fit_mature_ridge(x, prices, times, 731000)
    np.testing.assert_equal(indices, indices2)
    np.testing.assert_equal(before, model2.predict(scaler2.transform(x[730:731])))
