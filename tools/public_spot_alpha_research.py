"""Offline fixed public spot scenarios, explicit rule assumptions, no authority."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import io
import json
from pathlib import Path
import time
import zipfile

import numpy as np

from services.research.spot_alpha import (
    DAY, MINUTE, SpotAccount, SpotRules, features, fit_mature_ridge, target,
)
from services.research.trial_ledger import TrialLedger, TrialRecord
from tools.binance_spot_archive_capture import verify_archive

STRATEGIES = ('A', 'B', 'C', 'cash', 'passive25', 'hold100')
ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/engineering/plans/2026-09-06-public-spot-alpha-screen.md'


def digest(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def timestamp(date: str) -> int:
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()*1000)


def decode_archive(raw: bytes) -> np.ndarray:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        with archive.open(archive.namelist()[0]) as stream:
            data = np.loadtxt(stream, delimiter=',', usecols=range(8), ndmin=2)
    if not len(data) or not np.isfinite(data).all():
        raise ValueError('empty or nonfinite minute archive')
    scale = 1000 if data[0, 0] >= 10**15 else 1
    if (data[:, 0] % (MINUTE*scale)).any() or (np.diff(data[:, 0]) <= 0).any():
        raise ValueError('unordered/duplicate/misaligned minute bars')
    if ((data[:, 6] < data[:, 0]) | (data[:, 6] >= data[:, 0] + MINUTE*scale)).any():
        raise ValueError('invalid minute close boundary')
    data[:, 0] /= scale
    data[:, 6] = (data[:, 6]+1)/scale-1
    if ((data[:, 1:5] <= 0).any() or (data[:, [5, 7]] < 0).any()
            or (data[:, 2] < np.maximum(data[:, 1], data[:, 4])).any()
            or (data[:, 3] > np.minimum(data[:, 1], data[:, 4])).any()):
        raise ValueError('invalid OHLCV')
    if ((data[:, 0] >= timestamp('2025-01-01')) != (scale == 1000)).any():
        raise ValueError('unexpected spot archive timestamp era')
    return data


def load_market(directory: Path, symbol: str):
    pieces, provenance = [], []
    for path in sorted(directory.glob(f'{symbol}-1m-*.zip')):
        raw = path.read_bytes()
        record = json.loads(path.with_suffix('.zip.json').read_text())
        checksum = verify_archive(raw, record['official_checksum'], path.name)
        if record['symbol'] != symbol or record['sha256'] != checksum:
            raise ValueError('archive identity mismatch')
        pieces.append(decode_archive(raw))
        provenance.append(record)
    if not pieces:
        raise ValueError('no verified minute data')
    minute = np.concatenate(pieces)
    if (np.diff(minute[:, 0]) <= 0).any():
        raise ValueError('overlapping archive partitions')
    days = np.arange(timestamp('2022-11-01'), timestamp('2026-08-01'), DAY, dtype=np.int64)
    daily = np.full((len(days), 5), np.nan)
    refs = np.full(len(days), np.nan)
    times = minute[:, 0].astype(np.int64)
    for i, day in enumerate(days):
        lo, hi = np.searchsorted(times, [day, day+DAY])
        rows = minute[lo:hi]
        if (len(rows) == 1440 and np.all(np.diff(rows[:, 0]) == MINUTE)
                and np.all(rows[:, 6] == rows[:, 0]+MINUTE-1)):
            daily[i] = rows[0, 1], rows[:, 2].max(), rows[:, 3].min(), rows[-1, 4], rows[:, 5].sum()
        at = day+DAY+5*MINUTE
        index = int(np.searchsorted(times, at))
        if index < len(times) and times[index] == at:
            refs[i] = minute[index, 1]
    return minute, days, daily, refs, provenance


def spot_rules(raw: dict, symbol: str) -> SpotRules:
    row = raw['symbols'][0]
    if len(raw['symbols']) != 1 or row['symbol'] != symbol or row['status'] != 'TRADING' or not row['isSpotTradingAllowed'] or 'MARKET' not in row['orderTypes']:
        raise ValueError('spot market identity/status/order type unavailable')
    filters = {f['filterType']: f for f in row['filters']}
    known = {'PRICE_FILTER', 'LOT_SIZE', 'MARKET_LOT_SIZE', 'NOTIONAL', 'PERCENT_PRICE_BY_SIDE',
             'ICEBERG_PARTS', 'TRAILING_DELTA', 'MAX_NUM_ORDERS', 'MAX_NUM_ORDER_LISTS',
             'MAX_NUM_ALGO_ORDERS', 'MAX_NUM_ORDER_AMENDS'}
    if set(filters) - known:
        raise ValueError('unsupported spot filter')
    lot, market, notional = (filters[k] for k in ('LOT_SIZE', 'MARKET_LOT_SIZE', 'NOTIONAL'))
    if D(market['stepSize']) != 0 or not notional['applyMinToMarket']:
        raise ValueError('unsupported market quantity/notional policy')
    # Market orders have no submitted limit price. Price-percent, iceberg,
    # trailing/algo/amend/order-list filters do not constrain this one-order
    # immediate-fill proxy. PRICE_FILTER tick is an adverse fill-price rounding
    # assumption. NOTIONAL uses completed-trade VWAP, never the future minute.
    return SpotRules(symbol, row['quoteAsset'], D(lot['stepSize']),
                     max(D(lot['minQty']), D(market['minQty'])),
                     min(D(lot['maxQty']), D(market['maxQty'])),
                     D(notional['minNotional']),
                     D(notional['maxNotional']) if notional['applyMaxToMarket'] else D('1e50'),
                     D(filters['PRICE_FILTER']['tickSize']), int(notional['avgPriceMins']))


def ridge_predictions(days, daily, references):
    x = features(daily)
    execution_at = days + DAY + 5*MINUTE
    predictions = np.full(len(days), np.nan)
    model = None
    fits = []
    for i, day in enumerate(days):
        if datetime.fromtimestamp(int(day)//1000, timezone.utc).weekday() == 6:
            fitting_at = int(day+DAY+1)
            started = time.perf_counter_ns()
            fitted = fit_mature_ridge(x[:i+1], references[:i+1], execution_at[:i+1], fitting_at)
            elapsed = (time.perf_counter_ns()-started+999999)//1000000
            if fitted is not None:
                model = fitted
                fits.append(dict(decision_day=int(day), activated_at=fitting_at+elapsed,
                                 training_last_label_exit=int(execution_at[fitted[2][-1]+1]),
                                 training_first_index=int(fitted[2][0]), samples=730, elapsed_ms=elapsed))
                if fitting_at+elapsed > execution_at[i]:
                    continue
        if model is not None and np.isfinite(x[i]).all():
            predictions[i] = model[1].predict(model[0].transform(x[i:i+1]))[0]
    valid = np.isfinite(predictions[:-1]) & np.isfinite(references[:-1]) & np.isfinite(references[1:])
    valid &= execution_at[:-1] >= timestamp('2025-01-01')
    realized = references[1:] / references[:-1] - 1
    p, y = predictions[:-1][valid], realized[valid]
    calibration = dict(samples=len(y), mean_prediction=float(p.mean()) if len(p) else None,
                       mean_label=float(y.mean()) if len(y) else None,
                       rmse=float(np.sqrt(np.mean((p-y)**2))) if len(y) else None,
                       correlation=float(np.corrcoef(p, y)[0, 1]) if len(y)>2 and p.std()>0 and y.std()>0 else None)
    return predictions, fits, calibration


def average_price(minute, index: int, rules: SpotRules) -> D:
    if rules.average_minutes == 0:
        return D(str(minute[index, 1]))
    rows = minute[index-rules.average_minutes:index]
    if len(rows) != rules.average_minutes or rows[0, 0] != minute[index, 0]-rules.average_minutes*MINUTE or (np.diff(rows[:, 0]) != MINUTE).any():
        raise ValueError('notional VWAP history missing')
    volume = sum((D(str(v)) for v in rows[:, 5]), D(0))
    if volume <= 0:
        raise ValueError('no completed trades for notional VWAP')
    return sum((D(str(v)) for v in rows[:, 7]), D(0)) / volume


def replay(minute, days, daily, predictions, rules, strategy, slip_bps, stop, *,
           start=timestamp('2025-01-01')+5*MINUTE,
           end=timestamp('2026-08-01')+5*MINUTE):
    if strategy not in STRATEGIES or slip_bps not in (2, 5, 10) or type(stop) is not bool:
        raise ValueError('unregistered scenario')
    if start >= end or (end-start) % DAY or start % DAY != 5*MINUTE:
        raise ValueError('invalid fixed daily evaluation interval')
    times = minute[:, 0].astype(np.int64)
    lo, hi = np.searchsorted(times, [start, end])
    if (hi >= len(times) or times[lo] != start or times[hi] != end
            or (np.diff(times[lo:hi+1]) != MINUTE).any()
            or (minute[lo:hi, 6] != minute[lo:hi, 0]+MINUTE-1).any()):
        raise ValueError('evaluation minute coverage missing; no performance computed')
    fee, slip = D('.001'), D(slip_bps)/10000
    account = SpotAccount(loss_limit=D(150) if stop else None)
    records, curve, decisions, episodes = [], [{'at':start, 'equity':'1500'}], [], []
    state, entered, peak, minute_dd = 0., None, 1500., 0.
    exposure_sum, exposure_n, risk_minutes = 0., 0, 0

    def trade(index, desired, reason):
        nonlocal entered
        was_held = account.quantity > 0
        result = account.rebalance(D(str(desired)), D(str(minute[index, 1])),
                                   average_price(minute, index, rules), rules, fee, slip)
        result.update(at=int(times[index]), reason=reason, target=desired)
        if result['status'] != 'NO_CHANGE':
            records.append(result)
        if not was_held and account.quantity > 0:
            entered = int(times[index])
        if was_held and account.quantity == 0:
            episodes.append(dict(start=entered, end=int(times[index]), closed=True))
            entered = None

    def observe_segment(left, right):
        nonlocal peak, minute_dd, exposure_sum, exposure_n, risk_minutes
        if right <= left:
            return
        prices = minute[left:right, 1]
        equity = float(account.cash) + float(account.quantity)*prices
        peaks = np.maximum.accumulate(np.r_[peak, equity])[1:]
        minute_dd = max(minute_dd, float(np.max(1-equity/peaks)))
        peak = max(peak, float(equity.max()))
        exposure_sum += float(np.sum(float(account.quantity)*prices/equity))
        exposure_n += len(prices)
        risk_minutes += len(prices) if account.quantity > 0 else 0

    for index in range(int(lo), int(hi), 1440):
        at = int(times[index])
        decision_day = at-5*MINUTE-DAY
        i = int(np.searchsorted(days, decision_day))
        if i >= len(days) or days[i] != decision_day:
            raise ValueError('decision day missing')
        # Observe first. A threshold first detected now cannot exit until the
        # next quote. A latch already set earlier remains permanently active.
        triggered = account.observe(D(str(minute[index, 1])), at)
        if not triggered:
            pred = float(predictions[i]) if np.isfinite(predictions[i]) else None
            state = target(strategy, daily[:i+1, 3], account.quantity > 0, state, pred, float(fee), float(slip))
            ready = (strategy not in ('A', 'B', 'C') or
                     (pred is not None if strategy == 'C' else
                      np.isfinite(daily[max(0, i-(28 if strategy == 'A' else 55)):i+1, 3]).all()))
            decisions.append(dict(at=at, available_after=int(decision_day+DAY), target=state,
                                  actual_quantity=str(account.quantity), ready=bool(ready), prediction=pred))
            if account.halted_at is not None or (ready and (strategy != 'hold100' or index == lo)):
                trade(index, state, 'ACCOUNT_EXIT' if account.halted_at is not None else 'DAILY_TARGET')
                triggered = account.observe(D(str(minute[index, 1])), at)
        block_end = min(index+1440, int(hi))
        trigger_index = index if triggered else None
        if account.halted_at is None and account.loss_limit is not None:
            marks = minute[index:block_end, 1]
            estimates = float(account.cash) + float(account.quantity)*marks
            for offset in np.flatnonzero(estimates <= float(D(1500)-account.loss_limit)+1e-8):
                candidate = index+int(offset)
                if account.observe(D(str(minute[candidate, 1])), int(times[candidate])):
                    trigger_index = candidate
                    break
        if trigger_index is not None:
            exit_index = trigger_index+1
            observe_segment(index, exit_index)
            trade(exit_index, 0., 'LOSS_BUDGET_NEXT_QUOTE')
            observe_segment(exit_index, block_end)
        else:
            observe_segment(index, block_end)
        curve.append(dict(at=int(times[block_end]), equity=str(account.equity(D(str(minute[block_end, 1])))),
                          cash=str(account.cash), quantity=str(account.quantity), fees=str(account.fees)))
    if entered is not None:
        episodes.append(dict(start=entered, end=end, closed=False))
    fills = [r for r in records if r['status'] == 'FILLED']
    equities = np.array([float(r['equity']) for r in curve])
    returns = equities[1:]/equities[:-1]-1
    deviation = float(returns.std(ddof=1)) if len(returns)>1 else None
    annual = {}
    for year in (2025, 2026):
        ids = [i for i, r in enumerate(curve[:-1]) if datetime.fromtimestamp(r['at']//1000, timezone.utc).year == year]
        if ids:
            annual[str(year)] = dict(pnl=float(equities[ids[-1]+1]-equities[ids[0]]),
                                     return_fraction=float(equities[ids[-1]+1]/equities[ids[0]]-1))
    final = equities[-1]
    metrics = dict(net_pnl=final-1500, net_return=final/1500-1, final_equity=final,
                   daily_max_drawdown=float(np.max(1-equities/np.maximum.accumulate(equities))),
                   minute_open_max_drawdown=max(minute_dd, 1-final/max(peak, final)),
                   annualized_volatility=deviation*np.sqrt(365) if deviation is not None else None,
                   sharpe=float(returns.mean()/deviation*np.sqrt(365)) if deviation is not None and deviation>0 else None,
                   average_exposure=exposure_sum/exposure_n, risk_time_fraction=risk_minutes/exposure_n,
                   turnover_initial_equity=float(sum((D(r['quantity'])*D(r['price']) for r in fills), D(0))/1500),
                   fees=str(account.fees), fills=len(fills), skipped=sum(r['status']!='FILLED' for r in records),
                   halted_at=account.halted_at, remaining_quantity=str(account.quantity),
                   holding_episodes=len(episodes), mean_holding_days=float(np.mean([(e['end']-e['start'])/DAY for e in episodes])) if episodes else 0., annual=annual)
    return dict(strategy=strategy, symbol=rules.symbol, slip_bps=slip_bps, loss_stop=stop,
                metrics=metrics, equity=curve, orders=records, decisions=decisions, holding_episodes=episodes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--symbols', nargs='+', required=True, choices=('BTCUSDT', 'ETHUSDT', 'BTCUSDC', 'ETHUSDC'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in [Path(__file__).resolve(), ROOT/'services/research/spot_alpha.py', ROOT/'tools/binance_spot_archive_capture.py']}
    run_id = args.output.name
    plan = digest(PLAN.read_bytes())
    ledger = TrialLedger(str(args.input/'spot-alpha-trials.db'))
    results, diagnostics = [], {}
    for symbol in args.symbols:
        print('loading', symbol, flush=True)
        rules_raw = (args.input/f'{symbol}-exchange-info.json').read_bytes()
        rules = spot_rules(json.loads(rules_raw), symbol)
        minute, days, daily, refs, provenance = load_market(args.input/'archives', symbol)
        dataset = digest(provenance)
        predictions, fits, calibration = ridge_predictions(days, daily, refs)
        diagnostics[symbol] = dict(minute_rows=len(minute), incomplete_days=int(np.isnan(daily[:, 0]).sum()),
                                   archive_manifest=provenance, fits=fits, calibration=calibration,
                                   rule_digest=digest(rules_raw), rules={k:str(v) for k,v in asdict(rules).items()})
        for strategy in STRATEGIES:
            for slip in (2, 5, 10):
                for stop in (True, False):
                    identity = dict(run=run_id, strategy=strategy, symbol=symbol, slip=slip, stop=stop,
                                    source=source, plan=plan, data=dataset, rules=digest(rules_raw))
                    trial = digest(identity)
                    family = 'TREND' if strategy in ('A', 'B') else 'RIDGE' if strategy == 'C' else 'BASELINE'
                    def append(status, index):
                        ledger.append(TrialRecord(trial, digest(strategy), digest(family), digest(identity), plan,
                                                 status, index, time.time_ns()//1_000_000))
                    append('PLANNED', 0)
                    append('RUNNING', 1)
                    try:
                        result = replay(minute, days, daily, predictions, rules, strategy, slip, stop)
                        result.update(trial_id=trial, identity=identity, status='TECHNICAL_VALIDATION_ONLY',
                                      rule_scope='CURRENT_RULE_SCENARIO_ONLY', authoritative=False, live_eligible=False)
                    except Exception as exc:
                        result = dict(trial_id=trial, identity=identity, status='FAILED', error=str(exc))
                    with (args.output/f'{trial}.json').open('x') as stream:
                        json.dump(result, stream, indent=2, allow_nan=False)
                    append('FAILED' if result['status'] == 'FAILED' else 'PASSED', 2)
                    results.append({k:v for k,v in result.items() if k not in ('equity','orders','decisions','holding_episodes')})
                    print(symbol, strategy, slip, stop, result.get('metrics', result.get('error')), flush=True)
    report = dict(source=source, plan_digest=plan, results=results, diagnostics=diagnostics,
                  ledger_verified=ledger.verify(), trial_status_meaning='PASSED means computation only, not candidate admission',
                  historical_evaluation='SEEN_HISTORY_NOT_UNTOUCHED_OOS', authority=False, live_eligible=False)
    with (args.output/'screen.json').open('x') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)


if __name__ == '__main__':
    main()
