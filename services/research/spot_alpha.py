"""Offline spot cash mechanics for the fixed September 6 research scenarios.

No perpetual margin, borrowing, authority, network, or order-submission path.
Decimal cash/fills; floating arrays only for features and descriptive statistics.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

D = Decimal
DAY = 86_400_000
MINUTE = 60_000


def _finite(value: Decimal, *, positive: bool = False) -> None:
    if not isinstance(value, D) or not value.is_finite() or (positive and value <= 0):
        raise ValueError('finite Decimal required')


@dataclass(frozen=True)
class SpotRules:
    symbol: str
    quote: str
    step: Decimal
    minimum_quantity: Decimal
    maximum_quantity: Decimal
    minimum_notional: Decimal
    maximum_notional: Decimal
    tick: Decimal
    average_minutes: int

    def __post_init__(self):
        if self.quote not in ('USDT', 'USDC') or self.symbol not in ('BTC'+self.quote, 'ETH'+self.quote):
            raise ValueError('unsupported spot identity')
        for value in (self.step, self.minimum_quantity, self.maximum_quantity,
                      self.minimum_notional, self.maximum_notional, self.tick):
            _finite(value, positive=True)
        if self.maximum_quantity < self.minimum_quantity or self.maximum_notional < self.minimum_notional:
            raise ValueError('inverted rule bounds')
        if type(self.average_minutes) is not int or self.average_minutes < 0:
            raise ValueError('invalid notional averaging interval')


@dataclass
class SpotAccount:
    cash: Decimal = D('1500')
    quantity: Decimal = D('0')
    fees: Decimal = D('0')
    loss_limit: Decimal | None = D('150')
    halted_at: int | None = None

    def __post_init__(self):
        for value in (self.cash, self.quantity, self.fees):
            _finite(value)
            if value < 0:
                raise ValueError('negative spot balance')
        if self.loss_limit is not None:
            _finite(self.loss_limit, positive=True)

    def equity(self, mark: Decimal) -> Decimal:
        _finite(mark, positive=True)
        return self.cash + self.quantity * mark

    def observe(self, mark: Decimal, at: int) -> bool:
        if self.loss_limit is not None and self.halted_at is None and self.equity(mark) <= D('1500') - self.loss_limit:
            self.halted_at = at
            return True
        return False

    def rebalance(self, weight: Decimal, reference: Decimal, average: Decimal,
                  rules: SpotRules, fee: Decimal, slip: Decimal) -> dict:
        for value in (weight, fee, slip):
            _finite(value)
        for value in (reference, average):
            _finite(value, positive=True)
        if not 0 <= weight <= 1 or not 0 <= fee < 1 or not 0 <= slip < 1:
            raise ValueError('invalid weight or cost')
        if self.halted_at is not None:
            weight = D(0)
        equity = self.equity(reference)
        desired = (weight * equity / reference / rules.step).to_integral_value(rounding=ROUND_FLOOR) * rules.step
        delta = desired - self.quantity
        if not delta:
            return {'status': 'NO_CHANGE'}
        buy = delta > 0
        price = reference * (1 + slip if buy else 1 - slip)
        price = (price / rules.tick).to_integral_value(rounding=ROUND_CEILING if buy else ROUND_FLOOR) * rules.tick
        if price <= 0:
            return {'status': 'REJECTED_PRICE', 'reference': str(reference)}
        quantity = abs(delta)
        if buy:
            affordable = (self.cash / (price * (1 + fee)) / rules.step).to_integral_value(rounding=ROUND_FLOOR) * rules.step
            quantity = min(quantity, affordable)
        else:
            quantity = min(quantity, self.quantity)
        notional = quantity * average
        if quantity < rules.minimum_quantity or notional < rules.minimum_notional:
            return {'status': 'SKIPPED_MINIMUM', 'requested_quantity': str(abs(delta))}
        if quantity > rules.maximum_quantity or notional > rules.maximum_notional:
            return {'status': 'REJECTED_MAXIMUM', 'requested_quantity': str(quantity)}
        if quantity % rules.step:
            raise ValueError('quantity violates spot step')
        cost = quantity * price * fee
        signed = quantity if buy else -quantity
        cash = self.cash - signed * price - cost
        position = self.quantity + signed
        if cash < 0 or position < 0:
            raise ValueError('spot borrowing or short sale prohibited')
        self.cash, self.quantity, self.fees = cash, position, self.fees + cost
        return dict(status='FILLED', side='BUY' if buy else 'SELL', quantity=str(quantity),
                    price=str(price), fee=str(cost), reference=str(reference),
                    cash=str(cash), position=str(position))


def features(bars: np.ndarray) -> np.ndarray:
    """Columns open/high/low/close/base-volume; only complete historical days."""
    bars = np.asarray(bars, dtype=float)
    if bars.ndim != 2 or bars.shape[1] != 5:
        raise ValueError('daily OHLCV required')
    result = np.full((len(bars), 7), np.nan)
    close = bars[:, 3]
    for i in range(28, len(bars)):
        window = bars[i-28:i+1]
        if not np.isfinite(window).all() or (window[:, :4] <= 0).any() or (window[:, 4] <= 0).any():
            continue
        returns = close[i-27:i+1] / close[i-28:i] - 1
        log_volume = np.log(bars[i-27:i+1, 4])
        sd = float(log_volume.std(ddof=1))
        result[i] = [close[i] / close[i-k] - 1 for k in (1, 7, 28)] + [
            float(returns.std(ddof=1)), close[i] / close[i-27:i+1].mean() - 1,
            0. if sd < 1e-14 else (log_volume[-1] - log_volume.mean()) / sd,
            (bars[i, 1] - bars[i, 2]) / close[i]]
    return result


def fit_mature_ridge(x: np.ndarray, reference: np.ndarray, execution_at: np.ndarray,
                     fitting_at: int):
    """Training rows with both execution references available before fitting."""
    labels = reference[1:] / reference[:-1] - 1
    valid = (execution_at[1:] < fitting_at) & np.isfinite(labels) & np.isfinite(x[:-1]).all(axis=1)
    indices = np.flatnonzero(valid)[-730:]
    if len(indices) != 730:
        return None
    scaler = StandardScaler().fit(x[indices])
    model = Ridge(alpha=1., fit_intercept=True, solver='cholesky')
    model.fit(scaler.transform(x[indices]), labels[indices])
    return scaler, model, indices


def target(strategy: str, closes: np.ndarray, held: bool, previous: float,
           prediction: float | None, fee: float, slip: float) -> float:
    if strategy == 'cash':
        return 0.
    if strategy == 'passive25':
        return .25
    if strategy == 'hold100':
        return 1.
    if strategy == 'A':
        if len(closes) < 29 or not np.isfinite(closes[-29:]).all():
            return previous if held else 0.
        return .25 if closes[-1] / closes[-29] > 1 else 0.
    if strategy == 'B':
        if len(closes) < 56 or not np.isfinite(closes[-56:]).all():
            return previous if held else 0.
        if not held and closes[-1] > np.max(closes[-56:-1]):
            return .25
        if held and closes[-1] < np.min(closes[-21:-1]):
            return 0.
        return previous
    if strategy == 'C':
        if prediction is None or not np.isfinite(prediction):
            return previous if held else 0.
        if held:
            return 0. if prediction <= 0 else .25
        return .25 if prediction > 2*(fee+slip)+.0005 else 0.
    raise ValueError('unknown fixed strategy')
