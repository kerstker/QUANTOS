"""Strict L2 paper retest of the frozen Alpha book on Binance USDⓈ-M perpetuals.

This driver is intentionally separate from the legacy forward worker.  It
does not read credentials and it never calls an order endpoint.  The only network
calls are public market-data GETs (exchangeInfo, klines, mark-price klines,
funding history, bookTicker, and Data Vision OI metrics).

The test recomputes the three frozen expressions on a fresh quote-asset panel
rather than reusing a different venue's decision weights::

    carry_oi_majors = composite negative funding carry
    crowd_contra    = cs_rank(neg(global_ls))
    top_contra      = cs_zscore(neg(top_ls))

Since 2026-07-25 the strategy registry is SHARED with the forward paper track
(``tools/forward_track_advance.py::_ALPHAS`` via the same ``_alpha_spec``
normalization), so the 7 pre-registered crowd_contra variant CANDIDATEs are
simulated under the identical realistic-execution standard — their own
expr/quantile/cadence_days, with 3d/14d cadences honoured on the daily-close
decision grid.  ``equal_weight_combo`` still blends ONLY the three frozen
originals, byte-identical to the pre-variant wiring.

It then simulates cadence-grid rebalances with the current Binance contract filters,
the user's 4 bps taker assumption, funding cash flow, current best-bid/ask
spread calibration, and the shared Almgren-Chriss impact primitive.  Historical
best-bid/ask and historical filter snapshots are not available from the public
API, so those two inputs are explicitly labelled as current calibration data in
the report rather than being presented as historical tick-level truth.

Readiness remains ``L2_DRY_RUN_ONLY``.  The report is evidence for research and
paper trading, not a live-trading authorization.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
import urllib.request
import zipfile
import io
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlencode

from services.execution.impact import AlmgrenChrissImpact
from services.deployment.runtime import (
    evaluate_and_record_deployment,
    open_deployment_decision_store,
)
from services.deployment.gate import ReadinessLevel
from services.risk.research_risk.dry_run_guard import evaluate_dry_run_risk
from services.risk.research_risk.domain.models import RiskConfig
from quantos.contracts.governance import Environment
from services.research import alpha_expression as ax
from tools import forward_track_advance as forward_track
from tools.binance_oi_metrics_fetch import parse_metrics_zip
from tools.cache_io import atomic_write_json
from tools.continuous_paper_ledger import ContinuousPaperLedger


FAPI = "https://fapi.binance.com"
VISION = "https://data.binance.vision"
DAY_MS = 86_400_000
MAJOR_TICKERS = (
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX",
    "BCH", "DOT", "LTC", "UNI", "LINK", "ATOM",
)
ALPHA_IDS = ("carry_oi_majors", "crowd_contra", "top_contra")
QUANTILE = 0.30
DEFAULT_DAYS = 180
DEFAULT_AUM = 1_000_000.0
DEFAULT_TAKER_BPS = 4.0
DEFAULT_PARTICIPATION = 0.10
DEFAULT_OUTPUT = os.path.join("data", "cache", "binance_usdc_alpha_retest.json")

FUNDING = ("field", "funding")


def _zneg(op: str, window: int):
    return ("cs_zscore", ("neg", (op, FUNDING, window)))


# This is the frozen expression used by the existing carry research path.
CARRY = (
    "div",
    ("add", ("add", _zneg("decay_linear", 3), _zneg("decay_linear", 7)),
     ("add", _zneg("decay_linear", 14), _zneg("ts_mean", 7))),
    ("const", 4.0),
)
CROWD_CONTRA = ("cs_rank", ("neg", ("field", "global_ls")))
TOP_CONTRA = ("cs_zscore", ("neg", ("field", "top_ls")))
EXPRESSIONS = {
    "carry_oi_majors": CARRY,
    "crowd_contra": CROWD_CONTRA,
    "top_contra": TOP_CONTRA,
}

# --- Strategy registry (shared with the forward paper track) -----------------
# The continuous paper measures EXACTLY the strategies registered for the
# forward paper track (tools/forward_track_advance.py::_ALPHAS): the three
# frozen originals above plus the 2026-07-24 pre-registered crowd_contra
# variant family (7 CANDIDATEs; the 9 REJECTED variants stay unregistered by
# the same discipline).  Both paper systems normalize every entry through the
# SAME ``_alpha_spec`` helper, so expr/quantile/cadence_days cannot drift
# apart; the three originals keep their weekly/q=0.30 defaults byte-identical
# to the pre-variant wiring (their expressions are tuple-identical to the
# frozen CARRY/CROWD_CONTRA/TOP_CONTRA above, pinned by test).  Honest note:
# the variant family's DSR-adjusted in-sample edge is deeply skeptical
# (17-trial multiple-comparison tax; 3d-cadence DSR ~0.001, baseline 0.84) —
# registering the CANDIDATEs here is one more evidence dimension under the
# realistic-execution standard, NOT a promotion.
STRATEGY_SPECS: dict[str, dict[str, Any]] = {
    alpha_id: dict(forward_track._alpha_spec(entry))
    for alpha_id, entry in forward_track._ALPHAS.items()
}


class RetestDataError(RuntimeError):
    """Public data is incomplete or malformed for a strict paper retest."""


def _get_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "quantos-usdc-paper-retest", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # public data only
        return json.loads(response.read().decode("utf-8"))


def _get_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "quantos-usdc-paper-retest", "Accept": "application/zip"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # public data only
        return response.read()


def _finite(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RetestDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result):
        raise RetestDataError(f"{field} is not finite")
    return result


def _day_string(day_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day_ms / 1000))


def fetch_exchange_info(quote_asset: str = "USDC", *, http_get=_get_json) -> dict[str, dict[str, Any]]:
    """Return current TRADING quote-asset-margined perpetual metadata and filters."""
    quote = str(quote_asset).upper().strip()
    if quote not in {"USDT", "USDC"}:
        raise ValueError("quote_asset must be USDT or USDC")
    payload = http_get(f"{FAPI}/fapi/v1/exchangeInfo")
    if not isinstance(payload, Mapping):
        raise RetestDataError("exchangeInfo is not an object")
    result: dict[str, dict[str, Any]] = {}
    for row in payload.get("symbols", []):
        if not isinstance(row, Mapping):
            continue
        if (row.get("contractType") != "PERPETUAL" or row.get("quoteAsset") != quote
                or row.get("marginAsset") != quote or row.get("status") != "TRADING"):
            continue
        base = str(row.get("baseAsset", "")).upper()
        if base not in MAJOR_TICKERS:
            continue
        symbol = str(row.get("symbol", "")).upper()
        filters = row.get("filters")
        if not isinstance(filters, list):
            raise RetestDataError(f"{symbol}: exchange filters missing")
        by_type = {str(f.get("filterType")): f for f in filters if isinstance(f, Mapping)}
        lot = by_type.get("MARKET_LOT_SIZE") or by_type.get("LOT_SIZE")
        price_filter = by_type.get("PRICE_FILTER")
        notional_filter = by_type.get("MIN_NOTIONAL")
        if not isinstance(lot, Mapping) or not isinstance(price_filter, Mapping) or not isinstance(notional_filter, Mapping):
            raise RetestDataError(f"{symbol}: required Binance filters missing")
        result[base] = {
            "symbol": symbol,
            "base_asset": base,
            "quote_asset": quote,
            "margin_asset": quote,
            "contract_type": "PERPETUAL",
            "status": "TRADING",
            "step_size": _finite(lot.get("stepSize"), f"{symbol}.stepSize"),
            "min_qty": _finite(lot.get("minQty"), f"{symbol}.minQty"),
            "max_qty": _finite(lot.get("maxQty"), f"{symbol}.maxQty"),
            "min_notional": _finite(notional_filter.get("notional"), f"{symbol}.notional"),
            "tick_size": _finite(price_filter.get("tickSize"), f"{symbol}.tickSize"),
            "filter_source": "BINANCE_PUBLIC_EXCHANGE_INFO_CURRENT",
        }
    if len(result) < 4:
        raise RetestDataError(f"{quote} perpetual universe unexpectedly thin: {sorted(result)}")
    return {ticker: result[ticker] for ticker in sorted(result)}


def fetch_usdc_exchange_info(*, http_get=_get_json) -> dict[str, dict[str, Any]]:
    """Backward-compatible USDC wrapper used by existing callers/tests."""
    return fetch_exchange_info("USDC", http_get=http_get)


def fetch_daily_klines(
    symbol: str,
    start_day: int,
    end_day: int,
    *,
    mark: bool = False,
    http_get=_get_json,
) -> dict[int, dict[str, float]]:
    endpoint = "markPriceKlines" if mark else "klines"
    query = urlencode({
        "symbol": symbol,
        "interval": "1d",
        "startTime": int(start_day),
        "endTime": int(end_day + DAY_MS - 1),
        "limit": 1000,
    })
    payload = http_get(f"{FAPI}/fapi/v1/{endpoint}?{query}")
    if not isinstance(payload, list):
        raise RetestDataError(f"{symbol}: {endpoint} response is not a list")
    out: dict[int, dict[str, float]] = {}
    for row in payload:
        if not isinstance(row, (list, tuple)) or len(row) < 8:
            raise RetestDataError(f"{symbol}: malformed {endpoint} row")
        day = int(row[0])
        if day < start_day or day > end_day:
            continue
        close = _finite(row[4], f"{symbol}.close@{day}")
        if close <= 0:
            raise RetestDataError(f"{symbol}: non-positive close")
        # Mark-price klines have zero volume.  Volume is only consumed from the
        # trade-kline endpoint for impact/participation, never fabricated here.
        quote_volume = 0.0 if mark else _finite(row[7], f"{symbol}.quote_volume@{day}")
        if quote_volume < 0:
            raise RetestDataError(f"{symbol}: negative quote volume")
        out[day] = {"close": close, "quote_volume": quote_volume}
    return out


def fetch_daily_funding(
    symbol: str,
    start_day: int,
    end_day: int,
    *,
    http_get=_get_json,
) -> dict[int, float]:
    out: dict[int, float] = {}
    # Some quote-asset contracts settle more frequently than the usual 8-hour
    # cadence.  A single 1000-row request can therefore stop well before the
    # requested end date; paginate until the last event instead of silently
    # creating a stale tail in the paper panel.
    cursor = int(start_day)
    end_ms = int(end_day + DAY_MS - 1)
    for _ in range(20):
        query = urlencode({
            "symbol": symbol,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        })
        payload = http_get(f"{FAPI}/fapi/v1/fundingRate?{query}")
        if not isinstance(payload, list):
            raise RetestDataError(f"{symbol}: funding response is not a list")
        if not payload:
            break
        last_ts = cursor
        for row in payload:
            if not isinstance(row, Mapping):
                raise RetestDataError(f"{symbol}: malformed funding row")
            ts = int(row.get("fundingTime"))
            if ts < start_day or ts > end_ms:
                continue
            rate = _finite(row.get("fundingRate"), f"{symbol}.funding@{ts}")
            day = (ts // DAY_MS) * DAY_MS
            out[day] = out.get(day, 0.0) + rate
            last_ts = max(last_ts, ts)
        if last_ts >= end_ms or len(payload) < 1000:
            break
        cursor = last_ts + 1
    return out


def fetch_book_spreads(symbols: Sequence[str], *, http_get=_get_json) -> dict[str, dict[str, float]]:
    """Read current public best bid/ask for calibration; no order endpoint."""
    result: dict[str, dict[str, float]] = {}
    for symbol in sorted(symbols):
        payload = http_get(f"{FAPI}/fapi/v1/ticker/bookTicker?symbol={symbol}")
        if not isinstance(payload, Mapping):
            raise RetestDataError(f"{symbol}: bookTicker is not an object")
        bid, ask = _finite(payload.get("bidPrice"), f"{symbol}.bid"), _finite(payload.get("askPrice"), f"{symbol}.ask")
        if bid <= 0 or ask <= 0 or ask < bid:
            raise RetestDataError(f"{symbol}: invalid bid/ask")
        mid = (bid + ask) / 2.0
        result[symbol] = {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread_bps": (ask - bid) / mid * 10_000.0,
            "observed_at": int(payload.get("time", int(time.time() * 1000))),
        }
    return result


def _metrics_url(symbol: str, day: int) -> str:
    date = _day_string(day)
    return f"{VISION}/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip"


def fetch_metrics(
    symbol: str,
    days: Sequence[int],
    *,
    max_workers: int = 16,
    get_bytes=_get_bytes,
) -> tuple[dict[int, dict[str, float]], list[dict[str, Any]]]:
    """Fetch EOD OI/positioning metrics; missing archives are recorded, not filled."""
    def one(day: int) -> tuple[int, dict[str, float] | None, dict[str, Any] | None]:
        url = _metrics_url(symbol, day)
        try:
            raw = get_bytes(url)
            rec = parse_metrics_zip(raw)
            required = ("oi", "oi_value", "top_ls", "global_ls")
            if not rec or any(
                key not in rec or rec[key] is None or not math.isfinite(float(rec[key]))
                for key in required
            ):
                raise RetestDataError("metrics archive has no finite required EOD record")
            # ``taker_ls`` is an auxiliary field and a small number of daily
            # CSVs end with a blank taker value.  It is retained as None: the
            # three frozen expressions never read it, and the crowd_taker_combo
            # variant simply drops a None member from that day's cross-section
            # (the leg builder requires >= 4 valid names).  The required OI,
            # global-L/S, and top-trader fields still fail closed.
            clean = {
                key: (None if value is None else float(value))
                for key, value in rec.items()
            }
            return day, clean, None
        except Exception as exc:
            return day, None, {"symbol": symbol, "day": day, "url": url, "error": type(exc).__name__}

    out: dict[int, dict[str, float]] = {}
    failures: list[dict[str, Any]] = []
    workers = max(1, min(int(max_workers), len(days) or 1))
    if workers == 1:
        values = (one(day) for day in days)
        for day, rec, failure in values:
            if rec is not None:
                out[day] = rec
            if failure is not None:
                failures.append(failure)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for day, rec, failure in pool.map(one, days):
                if rec is not None:
                    out[day] = rec
                if failure is not None:
                    failures.append(failure)
    return out, failures


def _quantize_target(weights: Mapping[str, float], prices: Mapping[str, float], filters: Mapping[str, Mapping[str, Any]], aum: float) -> tuple[dict[str, float], list[dict[str, Any]]]:
    targets: dict[str, float] = {}
    omitted: list[dict[str, Any]] = []
    for ticker, weight in sorted(weights.items()):
        px = float(prices.get(ticker, 0.0))
        filt = filters.get(ticker)
        if px <= 0 or filt is None:
            omitted.append({"instrument": ticker, "reason": "MISSING_PRICE_OR_FILTER"})
            continue
        raw = float(weight) * float(aum) / px
        if abs(raw) <= 1e-15:
            # A combo can contain a name whose sleeve weights cancel exactly;
            # zero is a deliberate flat target, not a rejected min-qty order.
            continue
        step = float(filt["step_size"])
        if step <= 0:
            raise RetestDataError(f"{ticker}: non-positive step size")
        quantity = math.floor(abs(raw) / step + 1e-12) * step
        quantity = round(quantity, 12)
        quantity = min(quantity, float(filt["max_qty"]))
        if quantity < float(filt["min_qty"]):
            omitted.append({"instrument": ticker, "reason": "MIN_QTY", "raw_qty": raw})
            continue
        if quantity * px < float(filt["min_notional"]):
            omitted.append({"instrument": ticker, "reason": "MIN_NOTIONAL", "raw_qty": raw})
            continue
        targets[ticker] = math.copysign(quantity, raw)
    return targets, omitted


def _legs(signal_row: Sequence[float | None], instruments: Sequence[str], quantile: float = QUANTILE) -> dict[str, float]:
    valid = [i for i, value in enumerate(signal_row) if value is not None and math.isfinite(float(value))]
    if len(valid) < 4:
        return {}
    valid.sort(key=lambda index: float(signal_row[index]))
    k = max(1, int(round(len(valid) * float(quantile))))
    longs, shorts = valid[-k:], valid[:k]
    weights: dict[str, float] = {}
    for index in longs:
        weights[instruments[index]] = 0.5 / len(longs)
    for index in shorts:
        ticker = instruments[index]
        weights[ticker] = weights.get(ticker, 0.0) - 0.5 / len(shorts)
    return weights


def _cadence_rows(days: Sequence[int], cadence_days: int) -> list[int]:
    """First row + every row >= ``cadence_days`` after the previous decision row.

    ``cadence_days=7`` is byte-identical to the historical weekly grid; the
    forward paper track's ``_cadence_rows`` uses the same construction (its
    wall-clock ``cadence - 12h`` jitter tolerance is not needed here because
    this replay decides on the deterministic daily-close grid).
    """
    rows: list[int] = []
    previous = None
    step = int(cadence_days) * DAY_MS
    for index, day in enumerate(days):
        if previous is None or day - previous >= step:
            rows.append(index)
            previous = day
    return rows


def _weekly_rows(days: Sequence[int]) -> list[int]:
    return _cadence_rows(days, 7)


def _stats(curve: Sequence[float], ppy: int = 365) -> dict[str, float | None]:
    if not curve:
        return {"total_return": None, "sharpe": None, "max_drawdown": None, "calmar": None}
    start = float(curve[0])
    total = float(curve[-1]) / start - 1.0 if start > 0 else None
    returns = [float(curve[i]) / float(curve[i - 1]) - 1.0 for i in range(1, len(curve)) if curve[i - 1] > 0]
    mean = statistics.fmean(returns) if returns else 0.0
    sd = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = mean / sd * math.sqrt(ppy) if sd > 0 else None
    peak = float(curve[0])
    max_dd = 0.0
    for value in curve:
        peak = max(peak, float(value))
        if peak > 0:
            max_dd = max(max_dd, 1.0 - float(value) / peak)
    calmar = total / max_dd if total is not None and max_dd > 0 else None
    return {
        "total_return": total,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": calmar,
    }


def simulate(
    strategy_id: str,
    days: Sequence[int],
    weights_by_day: Mapping[int, Mapping[str, float]],
    prices: Mapping[int, Mapping[str, float]],
    funding: Mapping[int, Mapping[str, float]],
    quote_volume: Mapping[int, Mapping[str, float]],
    filters: Mapping[str, Mapping[str, Any]],
    spreads: Mapping[str, Mapping[str, float]],
    *,
    aum: float,
    fee_bps: float,
    participation: float,
    impact: AlmgrenChrissImpact,
) -> dict[str, Any]:
    cash = float(aum)
    positions: dict[str, float] = {}
    previous_prices: dict[str, float] = {}
    curve: list[float] = []
    trades: list[dict[str, Any]] = []
    rebalances: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    price_pnl = funding_pnl = fee_cost = spread_cost = impact_cost = 0.0
    turnover = 0.0
    for day in days:
        day_prices = prices[day]
        # Mark-to-market and funding for the position held over the preceding
        # close-to-close interval.  A new rebalance at today's close therefore
        # cannot consume today's future funding observation.
        for ticker, quantity in positions.items():
            if ticker in previous_prices:
                price_pnl += quantity * (day_prices[ticker] - previous_prices[ticker])
            rate = float(funding.get(day, {}).get(ticker, 0.0))
            cash_flow = -quantity * day_prices[ticker] * rate
            cash += cash_flow
            funding_pnl += cash_flow

        if day in weights_by_day:
            target, skipped = _quantize_target(weights_by_day[day], day_prices, filters, aum)
            omitted.extend({"day": day, **row} for row in skipped)
            trades_before = len(trades)
            turnover_before = turnover
            all_names = set(positions) | set(target)
            for ticker in sorted(all_names):
                delta = float(target.get(ticker, 0.0) - positions.get(ticker, 0.0))
                if abs(delta) <= 1e-15:
                    continue
                mid = day_prices[ticker]
                notional = abs(delta) * mid
                adv = float(quote_volume.get(day, {}).get(ticker, 0.0))
                # If ADV is absent, the trade cannot be assigned a truthful
                # impact cost and is rejected from this paper simulation.
                if adv <= 0:
                    omitted.append({"day": day, "instrument": ticker, "reason": "MISSING_ADV"})
                    continue
                participation_actual = notional / adv
                impact_bps = impact.temporary_bps(notional, adv) + impact.permanent_bps(notional, adv)
                # The requested participation is a cap/diagnostic.  A one-day
                # order larger than it is retained but explicitly flagged.
                participation_flag = participation_actual > participation
                half_spread_bps = float(spreads[ticker]["spread_bps"]) / 2.0
                friction_bps = half_spread_bps + impact_bps
                direction = 1.0 if delta > 0 else -1.0
                execution_price = mid * (1.0 + direction * friction_bps / 10_000.0)
                fee = notional * fee_bps / 10_000.0
                spread = notional * half_spread_bps / 10_000.0
                impact_charge = notional * impact_bps / 10_000.0
                cash -= delta * execution_price + fee
                positions[ticker] = float(target.get(ticker, 0.0))
                turnover += notional
                fee_cost += fee
                spread_cost += spread
                impact_cost += impact_charge
                trades.append({
                    "day": day,
                    "instrument": ticker,
                    "side": "BUY" if delta > 0 else "SELL",
                    "quantity": abs(delta),
                    "mark_price": mid,
                    "execution_price": execution_price,
                    "notional": notional,
                    "fee_bps": fee_bps,
                    "fee": fee,
                    "spread_bps": half_spread_bps * 2.0,
                    "spread_cost": spread,
                    "impact_bps": impact_bps,
                    "impact_cost": impact_charge,
                    "participation": participation_actual,
                    "participation_over_cap": participation_flag,
                    "step_size": filters[ticker]["step_size"],
                    "min_qty": filters[ticker]["min_qty"],
                    "min_notional": filters[ticker]["min_notional"],
                })
            rebalances.append({
                "day": day,
                "requested_weights": dict(sorted(weights_by_day[day].items())),
                "target_quantities": dict(sorted(target.items())),
                "skipped_orders": list(skipped),
                "trade_count": len(trades) - trades_before,
                "turnover": turnover - turnover_before,
                "status": "EXECUTED" if len(trades) > trades_before else "NO_FILL",
            })
        market_value = sum(quantity * day_prices[ticker] for ticker, quantity in positions.items())
        curve.append(cash + market_value)
        previous_prices = dict(day_prices)

    # Force a final paper liquidation so the reported PnL includes the cost of
    # closing positions; this is not a live order and remains a mark-only
    # accounting operation.
    if days and positions:
        day = days[-1]
        day_prices = prices[day]
        for ticker, quantity in list(positions.items()):
            if abs(quantity) <= 1e-15:
                continue
            mid = day_prices[ticker]
            notional = abs(quantity) * mid
            adv = float(quote_volume[day].get(ticker, 0.0))
            if adv <= 0:
                raise RetestDataError(f"{strategy_id}: final ADV missing for {ticker}")
            impact_bps = impact.temporary_bps(notional, adv) + impact.permanent_bps(notional, adv)
            half_spread_bps = float(spreads[ticker]["spread_bps"]) / 2.0
            direction = -1.0 if quantity > 0 else 1.0
            friction_bps = half_spread_bps + impact_bps
            execution_price = mid * (1.0 + direction * friction_bps / 10_000.0)
            fee = notional * fee_bps / 10_000.0
            spread = notional * half_spread_bps / 10_000.0
            impact_charge = notional * impact_bps / 10_000.0
            cash += quantity * execution_price - fee
            fee_cost += fee
            spread_cost += spread
            impact_cost += impact_charge
            turnover += notional
            trades.append({
                "day": day,
                "instrument": ticker,
                "side": "SELL" if quantity > 0 else "BUY",
                "quantity": abs(quantity),
                "mark_price": mid,
                "execution_price": execution_price,
                "notional": notional,
                "fee_bps": fee_bps,
                "fee": fee,
                "spread_bps": half_spread_bps * 2.0,
                "spread_cost": spread,
                "impact_bps": impact_bps,
                "impact_cost": impact_charge,
                "participation": notional / adv,
                "participation_over_cap": notional / adv > participation,
                "closeout": True,
            })
            positions[ticker] = 0.0
        curve[-1] = cash

    gross = price_pnl + funding_pnl
    costs = fee_cost + spread_cost + impact_cost
    net = gross - costs
    final_equity = float(curve[-1]) if curve else aum
    residual = abs((final_equity - aum) - net)
    daily_returns = [
        float(curve[index]) / float(curve[index - 1]) - 1.0
        for index in range(1, len(curve))
        if float(curve[index - 1]) > 0.0
    ]
    return {
        "strategy_id": strategy_id,
        "n_days": len(days),
        "n_rebalances": len(weights_by_day),
        "n_trades": len(trades),
        "turnover": turnover,
        "gross_price_pnl": price_pnl,
        "funding_pnl": funding_pnl,
        "gross_pnl": gross,
        "fee_cost": fee_cost,
        "spread_cost": spread_cost,
        "impact_cost": impact_cost,
        "total_costs": costs,
        "net_pnl": final_equity - aum,
        "maker0_reference_net_pnl": (final_equity - aum) + fee_cost,
        "final_equity": final_equity,
        "reconciliation_residual": residual,
        "stats": _stats(curve),
        "omitted_orders": omitted,
        "participation_over_cap_count": sum(1 for row in trades if row.get("participation_over_cap")),
        "trades": trades,
        "rebalances": rebalances,
        "daily_returns": daily_returns,
        "equity_curve": [{"day": day, "equity": value} for day, value in zip(days, curve)],
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
    }


def _build_weights(panel: ax.FieldPanel, instruments: Sequence[str], days: Sequence[int]) -> dict[str, dict[int, dict[str, float]]]:
    signals = {
        alpha_id: ax.evaluate(spec["expr"], panel)
        for alpha_id, spec in STRATEGY_SPECS.items()
    }
    output: dict[str, dict[int, dict[str, float]]] = {}
    for alpha_id, signal in signals.items():
        spec = STRATEGY_SPECS[alpha_id]
        rows = _cadence_rows(days, int(spec["cadence_days"]))
        strategy_weights: dict[int, dict[str, float]] = {}
        for row in rows:
            weights = _legs(signal[row], instruments, quantile=float(spec["quantile"]))
            if weights:
                strategy_weights[days[row]] = weights
        output[alpha_id] = strategy_weights
    # The combo intentionally covers ONLY the three frozen originals, so its
    # weights stay byte-identical to the pre-variant wiring and the historical
    # ledger rows replay unchanged.  Variant-family sleeves are not blended in.
    combo: dict[int, dict[str, float]] = {}
    for day in sorted(set().union(*(set(output[alpha_id]) for alpha_id in ALPHA_IDS))):
        merged: dict[str, float] = {}
        for alpha_id in ALPHA_IDS:
            for ticker, weight in output[alpha_id].get(day, {}).items():
                merged[ticker] = merged.get(ticker, 0.0) + weight / len(ALPHA_IDS)
        combo[day] = merged
    output["equal_weight_combo"] = combo
    return output


def _weekly_risk_series(
    days: Sequence[int],
    strategy_weights: Mapping[int, Mapping[str, float]],
    equity_curve: Sequence[Mapping[str, Any]],
) -> tuple[list[float], list[float], list[int]]:
    """Return a weekly (ppy=52) risk series from the daily mark-to-market curve.

    The execution simulation marks every complete day so that funding and price
    PnL are auditable.  The frozen strategy, however, makes decisions weekly;
    the strict research-risk gate therefore receives the equity observations at
    rebalance dates (plus the final observation) rather than silently annualising
    a weekly strategy as if it were a daily one.
    """
    by_day = {int(row["day"]): float(row["equity"]) for row in equity_curve}
    indices = [index for index, day in enumerate(days) if day in strategy_weights]
    if days and (len(days) - 1) not in indices:
        indices.append(len(days) - 1)
    indices = sorted(set(indices))
    sampled_days = [int(days[index]) for index in indices if int(days[index]) in by_day]
    sampled_curve = [by_day[day] for day in sampled_days]
    returns = [
        sampled_curve[index] / sampled_curve[index - 1] - 1.0
        for index in range(1, len(sampled_curve))
        if sampled_curve[index - 1] > 0.0
    ]
    # The gate contract aligns each return with the post-period equity value.
    return returns, sampled_curve[1:], sampled_days[1:]


def _full_research_risk_attempt(
    simulations: Mapping[str, Mapping[str, Any]],
    weights: Mapping[str, Mapping[int, Mapping[str, float]]],
    days: Sequence[int],
) -> dict[str, Any]:
    """Run the nine-engine gate in its strict mode without manufacturing inputs.

    Capacity in the canonical gate is denominated in CNY and the model-alpha
    metadata must come from the immutable research catalog.  This public Binance
    replay has USDT quote volumes and no approved catalog snapshot, so passing
    converted or guessed values would be a false clearance.  ``full_inputs`` is
    intentionally omitted; the gate returns an explicit pending/rejected record
    naming every missing full-engine input instead.
    """
    result: dict[str, Any] = {
        "mode": "FULL_NINE_ENGINE_REQUIRED",
        # 7d-cadence strategies are sampled weekly (ppy=52).  3d/14d variant
        # strategies are sampled on their own rebalance grid, so the ppy=52
        # label is an approximation for them; the gate is a PENDING-full-inputs
        # diagnostic in every case, never a clearance.
        "annualization": "weekly ppy=52 (non-7d cadence strategies sampled on their own grid; label approximate)",
        "provenance": "REAL_RESEARCH_RISK_FULL_PENDING",
        "strategies": {},
        "all_passed": False,
    }
    config = RiskConfig(periods_per_year=52.0)
    for strategy_id, simulation in simulations.items():
        series, curve, timestamps = _weekly_risk_series(
            days,
            weights.get(strategy_id, {}),
            simulation.get("equity_curve", []),
        )
        # A non-empty latest target is the only honest portfolio weight snapshot
        # available here.  It is not used to bypass the missing full inputs.
        latest_weights = {}
        strategy_points = weights.get(strategy_id, {})
        if strategy_points:
            latest_day = sorted(strategy_points)[-1]
            latest_weights = dict(strategy_points[latest_day])
        gate = evaluate_dry_run_risk(
            list(series),
            latest_weights,
            list(curve),
            timestamps=list(timestamps),
            config=config,
            full_inputs=None,
            require_full_inputs=True,
        )
        result["strategies"][strategy_id] = {
            **gate,
            "risk_periods": len(series),
            "risk_timestamp_count": len(timestamps),
            "cadence_days": int(STRATEGY_SPECS.get(strategy_id, {}).get("cadence_days", 7)),
            "full_input_status": {
                "capacity_cny": "MISSING_USDT_TO_CNY_PROVENANCE",
                "daily_volume_cny": "MISSING_USDT_TO_CNY_PROVENANCE",
                "alpha_returns": "AVAILABLE_BUT_NOT_AUTHORIZED_AS_FULL_BUNDLE",
                "regime_labels": "MISSING_APPROVED_PIT_REGIME_LABELS",
                "alpha_metadata": "MISSING_IMMUTABLE_RESEARCH_CATALOG_SNAPSHOT",
            },
        }
    return result


def _paper_deployment_gate(quote: str, generated_at: int) -> dict[str, Any]:
    """Evaluate and durably record the PAPER deployment gate, fail-closed."""
    subject = f"binance_{quote.lower()}_alpha_continuous_paper"
    try:
        store_path = os.path.join("data", "cache", "deployment_state.db")
        store = open_deployment_decision_store(store_path, create=True)
        try:
            decision = evaluate_and_record_deployment(
                store,
                Environment.PAPER,
                readiness=ReadinessLevel.L2_DRY_RUN_ONLY,
                subject=subject,
                # No human approval or approved content-hash pins are fabricated
                # by a public-data research driver.
                approval=None,
                actor=None,
                versions=(),
                approved_pins=None,
                at=int(generated_at),
            )
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                close()
        return {
            "environment": Environment.PAPER.value,
            "subject": subject,
            "allowed": bool(decision.allowed),
            "state": "ELIGIBLE_NOT_APPLIED" if decision.allowed else "DENIED",
            "reasons": list(decision.reasons),
            "readiness": ReadinessLevel.L2_DRY_RUN_ONLY.value,
            "human_approval_supplied": False,
            "version_pins_supplied": False,
            "recorded": True,
            "store": store_path,
        }
    except Exception as exc:
        # A deployment decision that cannot be recorded is itself a block.
        return {
            "environment": Environment.PAPER.value,
            "subject": subject,
            "allowed": False,
            "state": "DENIED",
            "reasons": ["DEPLOYMENT_STATE_ERROR", type(exc).__name__],
            "readiness": ReadinessLevel.L2_DRY_RUN_ONLY.value,
            "human_approval_supplied": False,
            "version_pins_supplied": False,
            "recorded": False,
            "store": os.path.join("data", "cache", "deployment_state.db"),
        }


def _persist_continuous_rebalances(
    quote: str,
    output_path: str,
    simulations: Mapping[str, Mapping[str, Any]],
    deployment_gate: Mapping[str, Any],
    *,
    ledger_path: str | None = None,
) -> dict[str, Any]:
    """Persist every weekly target in an append-only, idempotent paper ledger."""
    resolved_ledger_path = ledger_path or os.path.join(
        "data", "cache", f"binance_{quote.lower()}_continuous_paper.db"
    )
    ledger = ContinuousPaperLedger(resolved_ledger_path, create=True)
    status = (
        "PAPER_REBALANCE_EXECUTED_DIAGNOSTIC"
        if bool(deployment_gate.get("allowed"))
        else "PAPER_REBALANCE_DIAGNOSTIC_BLOCKED_DEPLOYMENT_GATE"
    )
    per_strategy: dict[str, dict[str, int]] = {}
    try:
        for strategy_id in sorted(simulations):
            inserted, replayed = ledger.append_many(
                strategy_id,
                list(simulations[strategy_id].get("rebalances", [])),
                status=status,
            )
            per_strategy[strategy_id] = {"inserted": inserted, "replayed": replayed}
    finally:
        ledger.close()
    return {
        "mode": "CONTINUOUS_WEEKLY_REBALANCE",
        "ledger": resolved_ledger_path,
        "source_report": output_path,
        "append_only": True,
        "idempotent": True,
        "rebalances": per_strategy,
        "status": status,
        "paper_orders_are_not_live_orders": True,
    }


def run_retest(*, quote_asset: str = "USDC", days: int = DEFAULT_DAYS, aum: float = DEFAULT_AUM, fee_bps: float = DEFAULT_TAKER_BPS,
               participation: float = DEFAULT_PARTICIPATION, output_path: str = DEFAULT_OUTPUT,
               now_ms: int | None = None, metrics_workers: int = 16,
               ledger_path: str | None = None) -> dict[str, Any]:
    if days < 35:
        raise ValueError("days must be at least 35 to produce a meaningful weekly sample")
    if not math.isfinite(aum) or aum <= 0 or not math.isfinite(fee_bps) or fee_bps < 0:
        raise ValueError("aum must be positive and fee_bps must be non-negative")
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    quote = str(quote_asset).upper().strip()
    exchange = fetch_exchange_info(quote)
    instruments = sorted(exchange)
    end_day = (now_ms // DAY_MS) * DAY_MS - DAY_MS  # last complete UTC day
    start_day = end_day - (int(days) - 1) * DAY_MS
    day_list = list(range(start_day, end_day + 1, DAY_MS))
    prices: dict[int, dict[str, float]] = {day: {} for day in day_list}
    volumes: dict[int, dict[str, float]] = {day: {} for day in day_list}
    funding: dict[int, dict[str, float]] = {day: {} for day in day_list}
    metrics: dict[str, dict[int, dict[str, float]]] = {}
    failures: list[dict[str, Any]] = []
    source_counts: dict[str, dict[str, int]] = {}
    for ticker in instruments:
        symbol = exchange[ticker]["symbol"]
        trade_rows = fetch_daily_klines(symbol, start_day, end_day, mark=False)
        mark_rows = fetch_daily_klines(symbol, start_day, end_day, mark=True)
        rate_rows = fetch_daily_funding(symbol, start_day, end_day)
        metric_rows, metric_failures = fetch_metrics(symbol, day_list, max_workers=metrics_workers)
        failures.extend(metric_failures)
        metrics[ticker] = metric_rows
        for day in day_list:
            if day in mark_rows:
                prices[day][ticker] = mark_rows[day]["close"]
            if day in trade_rows:
                volumes[day][ticker] = trade_rows[day]["quote_volume"]
            if day in rate_rows:
                funding[day][ticker] = rate_rows[day]
        source_counts[ticker] = {
            "mark_days": len(mark_rows),
            "trade_kline_days": len(trade_rows),
            "funding_days": len(rate_rows),
            "metrics_days": len(metric_rows),
            "taker_ls_missing_days": sum(1 for row in metric_rows.values() if row.get("taker_ls") is None),
        }
    # Strictly use only dates with complete inputs for every active USDC name.
    complete_days = [
        day for day in day_list
        if all(ticker in prices[day] and ticker in volumes[day] and ticker in funding[day]
               and day in metrics[ticker] for ticker in instruments)
    ]
    if len(complete_days) < 35:
        raise RetestDataError(
            f"only {len(complete_days)} complete {quote} PIT days; missing inputs were not synthesized"
        )
    prices = {day: prices[day] for day in complete_days}
    volumes = {day: volumes[day] for day in complete_days}
    funding = {day: funding[day] for day in complete_days}
    matrices: dict[str, list[list[float | None]]] = {
        "close": [[prices[day][ticker] for ticker in instruments] for day in complete_days],
        "volume": [[volumes[day][ticker] for ticker in instruments] for day in complete_days],
        "funding": [[funding[day][ticker] for ticker in instruments] for day in complete_days],
    }
    for field in ("oi", "oi_value", "top_ls", "global_ls", "taker_ls"):
        matrices[field] = [[metrics[ticker][day].get(field) for ticker in instruments] for day in complete_days]
    panel = ax.FieldPanel(instruments, complete_days, matrices)
    weights = _build_weights(panel, instruments, complete_days)
    spread_snapshot = fetch_book_spreads([exchange[ticker]["symbol"] for ticker in instruments])
    spreads = {ticker: spread_snapshot[exchange[ticker]["symbol"]] for ticker in instruments}
    impact = AlmgrenChrissImpact()
    simulations = {}
    for strategy_id, strategy_weights in weights.items():
        simulations[strategy_id] = simulate(
            strategy_id, complete_days, strategy_weights, prices, funding, volumes, exchange, spreads,
            aum=float(aum), fee_bps=float(fee_bps), participation=float(participation), impact=impact,
        )
        # The full daily curve is useful for local verification but the report
        # intentionally keeps all trade details because this is the audit seam.
    generated_at = int(time.time() * 1000)
    risk_gate = _full_research_risk_attempt(simulations, weights, complete_days)
    deployment_gate = _paper_deployment_gate(quote, generated_at)
    continuous_paper = _persist_continuous_rebalances(
        quote,
        output_path,
        simulations,
        deployment_gate,
        ledger_path=ledger_path,
    )
    strict_paper_verdict = (
        "PAPER_READY_DIAGNOSTIC_ONLY"
        if deployment_gate.get("allowed") and risk_gate.get("all_passed")
        else "PAPER_ONLY_PENDING_FULL_DEPLOYMENT_GATE"
    )
    classification = (
        "PAPER_READY_FOR_REVIEW"
        if deployment_gate.get("allowed") and risk_gate.get("all_passed")
        else "REJECTED"
    )
    report = {
        "schema_version": 2,
        "report_type": f"BINANCE_{quote}_PERPETUAL_ALPHA_RETEST",
        "generated_at": generated_at,
        "readiness": "L2_DRY_RUN_ONLY",
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "quote_asset": quote,
        "margin_asset": quote,
        "contract_type": "PERPETUAL",
        "venue": "BINANCE_USDM_PUBLIC_MARKET_DATA",
        "universe": instruments,
        "excluded_major_tickers": sorted(set(MAJOR_TICKERS) - set(instruments)),
        "data_window": {
            "requested_days": days,
            "complete_days": len(complete_days),
            "start_day": complete_days[0],
            "end_day": complete_days[-1],
            "start": _day_string(complete_days[0]),
            "end": _day_string(complete_days[-1]),
            "pitrule": "daily signal at t, mark/funding PnL after t; current-day dropped",
        },
        "alpha_expressions": {
            "carry_oi_majors": "composite negative funding carry",
            "crowd_contra": "cs_rank(neg(global_ls))",
            "top_contra": "cs_zscore(neg(top_ls))",
            "combination": "equal-weighted aggregate of the three frozen Alpha target weights",
        },
        "strategy_registry": {
            "provenance": "FORWARD_TRACK_REGISTRY_SHARED_ALPHA_SPEC",
            "source": "tools/forward_track_advance.py::_ALPHAS normalized by the same _alpha_spec in both paper systems",
            "combo_scope": "equal_weight_combo covers ONLY the three frozen originals; variant sleeves are not blended in (combo weights byte-identical to pre-variant wiring)",
            "variant_family_note": (
                "2026-07-24 pre-registered crowd_contra variant CANDIDATEs (7 of 16; REJECTED variants stay "
                "unregistered). DSR-adjusted in-sample edge is deeply skeptical "
                "(evidence/crowd_contra_variants.json: 3d-cadence DSR ~0.001 vs baseline 0.84, 17-trial family) — "
                "realistic-paper registration is one more evidence dimension, NOT a promotion."
            ),
            "strategies": {
                alpha_id: {
                    "expr": ax.serialize(spec["expr"]),
                    "quantile": float(spec["quantile"]),
                    "cadence_days": int(spec["cadence_days"]),
                }
                for alpha_id, spec in sorted(STRATEGY_SPECS.items())
            },
        },
        "execution_assumptions": {
            "fee_mode": "TAKER",
            "taker_fee_bps": fee_bps,
            "fee_source": "USER_PROVIDED_BINANCE_TAKER_BENCHMARK",
            "maker_fee_bps_reference": 0.0,
            "spread": "current public bookTicker half-spread applied to historical mark executions",
            "spread_source": "BINANCE_PUBLIC_BOOK_TICKER_CURRENT_CALIBRATION",
            "impact_model": "Almgren-Chriss temporary sqrt + permanent linear",
            "impact_parameters": {"temp_coef": impact.temp_coef, "perm_coef": impact.perm_coef, "temp_exponent": impact.temp_exponent},
            "participation_cap": participation,
            "quantity_rule": "current exchangeInfo MARKET_LOT_SIZE/LOT_SIZE floor + minQty + MIN_NOTIONAL",
            "forced_final_liquidation": True,
            "historical_microstructure_limit": "historical best bid/ask and historical filter snapshots are unavailable from public endpoints; current values are labelled calibration, not substituted silently",
        },
        "gate_scope": {
            "execution_realism": "RUN",
            "research_risk_gate": "FULL_NINE_ENGINE_ATTEMPTED",
            "pre_trade_risk_gate": "NOT_AUTHORIZED_FOR_HISTORICAL_REPLAY",
            "deployment_gate": deployment_gate["state"],
            "reason": "The replay runs after a fail-closed full-gate attempt; no missing USDT/CNY, PIT-regime, or catalog fields are synthesized.",
        },
        "filters": exchange,
        "book_ticker_calibration": spread_snapshot,
        "source_counts": source_counts,
        "metrics_failures": failures[:500],
        "metrics_failure_count": len(failures),
        "simulations": simulations,
        "risk_gate": risk_gate,
        "deployment_gate": deployment_gate,
        "continuous_paper": continuous_paper,
        "paper_verdict": strict_paper_verdict,
        "classification": classification,
        "acceptance": {
            "reconciliation_residual_max": max(float(row["reconciliation_residual"]) for row in simulations.values()),
            "reconciliation_pass": all(float(row["reconciliation_residual"]) <= 1e-6 for row in simulations.values()),
            "complete_data_pass": len(complete_days) >= 35,
            "paper_only_pass": True,
            "historical_microstructure_exact": False,
            "continuous_rebalance_pass": all(
                int(value.get("n_rebalances", 0)) >= 1
                and len(value.get("rebalances", [])) == int(value.get("n_rebalances", 0))
                for value in simulations.values()
            ),
            "full_research_risk_gate_pass": bool(risk_gate.get("all_passed")),
            "paper_deployment_gate_pass": bool(deployment_gate.get("allowed")),
        },
    }
    atomic_write_json(output_path, report, indent=2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quote-asset", choices=("USDT", "USDC"), default="USDC")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--aum", type=float, default=DEFAULT_AUM)
    parser.add_argument("--fee-bps", type=float, default=DEFAULT_TAKER_BPS)
    parser.add_argument("--participation", type=float, default=DEFAULT_PARTICIPATION)
    parser.add_argument("--metrics-workers", type=int, default=16)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--ledger",
        default=None,
        help="append-only continuous-paper ledger path; defaults to the quote-specific legacy path",
    )
    args = parser.parse_args()
    report = run_retest(
        quote_asset=args.quote_asset,
        days=args.days,
        aum=args.aum,
        fee_bps=args.fee_bps,
        participation=args.participation,
        output_path=args.output,
        metrics_workers=args.metrics_workers,
        ledger_path=args.ledger,
    )
    print(json.dumps({
        "quote_asset": report["quote_asset"],
        "universe": report["universe"],
        "window": report["data_window"],
        "simulations": {
            key: {
                "net_pnl": value["net_pnl"],
                "net_return": value["stats"]["total_return"],
                "sharpe": value["stats"]["sharpe"],
                "max_drawdown": value["stats"]["max_drawdown"],
                "fee_cost": value["fee_cost"],
                "spread_cost": value["spread_cost"],
                "impact_cost": value["impact_cost"],
                "funding_pnl": value["funding_pnl"],
                "reconciliation_residual": value["reconciliation_residual"],
                "n_trades": value["n_trades"],
            }
            for key, value in report["simulations"].items()
        },
        "output": args.output,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
