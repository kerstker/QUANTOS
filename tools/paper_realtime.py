"""Run a public-market, paper-only mark-to-market cycle.

The weekly Alpha decision and order preview remain the source of paper fills.
This driver adds the missing *between rebalances* operation: it polls the
public Binance USD-M mark-price endpoint, reconstructs the append-only paper
fills, reconciles current equity/PnL, and stores a hash-chained observation.
It also carries the authoritative dry-run/backtest summary into the same
receipt, so an operator can verify both the execution seam and its research
reference without opening a private account path.

The default command is one cycle.  ``--follow`` is an explicit foreground
loop and exits cleanly on Ctrl-C; it never starts a detached process.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlencode

from quantos.contracts.paper import FillMethod, OrderSide, PaperFill
from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from services.execution.paper_execution_store import PaperExecutionStore
from services.execution.paper_realtime_store import PaperRealtimeStore
from services.execution.paper_reconciliation import reconcile
from tools.cache_io import atomic_write_json
from tools.binance_perp_mark_fetch import load_mark_prices
from tools.l2_paper_operator import run_operator
from tools.paper_health import build_report
from tools.paper_candidate_universe import paper_candidate_ids
from tools.paper_order_preview import run_preview


DEFAULT_OUTPUT = os.path.join("data", "cache", "paper_realtime_status.json")
DEFAULT_STORE = os.path.join("data", "cache", "paper_realtime.db")
DEFAULT_PAPER_STORE = os.path.join("data", "cache", "paper_order_ledger.db")
DEFAULT_OPERATOR_RECEIPT = os.path.join("data", "cache", "l2_paper_operator.json")
DEFAULT_HEALTH_OUTPUT = os.path.join("data", "cache", "paper_health.json")
DEFAULT_FUNDING_RATES = os.path.join("data", "cache", "crypto_funding_rates.json")
DEFAULT_MARK_HISTORY = os.path.join("data", "cache", "crypto_perp_mark_prices.json")
PUBLIC_MARK_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
PUBLIC_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
MAX_PUBLIC_MARK_AGE_MS = 5 * 60 * 1000
# Binance USD-M funding is normally settled every eight hours.  A twelve-hour
# tolerance catches a stale cache while allowing a small public-endpoint delay.
MAX_FUNDING_EVENT_AGE_MS = 12 * 60 * 60 * 1000
ALPHA_IDS = ("carry_oi_majors", "crowd_contra", "top_contra")
DEFAULT_AUM = 1_000_000.0
DEFAULT_COST_BPS = 10.0
REALTIME_HISTORY_LIMIT = 96
MARK_ONLY_REPLAY_REASON = "EXISTING_FILLED_PAPER_LEDGER_REPLAY"


class PublicMarkDataError(RuntimeError):
    """The public mark snapshot cannot support a paper valuation."""


def _http_get_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "quantos-paper-research", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # public data only
        return json.loads(response.read().decode("utf-8"))


def fetch_public_mark_prices(
    instruments: Sequence[str],
    *,
    http_get: Callable[[str], object] = _http_get_json,
) -> dict[str, Any]:
    """Fetch current public mark prices for exactly the requested universe.

    The endpoint is unauthenticated market data.  A partial response is an
    error: a paper account must never silently mark one leg at zero or with a
    request-time placeholder.
    """
    selected = tuple(sorted({str(item).upper() for item in instruments if str(item).strip()}))
    if not selected:
        raise PublicMarkDataError("paper mark universe is empty")
    payload = http_get(PUBLIC_MARK_URL)
    rows = payload if isinstance(payload, list) else [payload]
    wanted = {f"{ticker}USDT": ticker for ticker in selected}
    marks: dict[str, float] = {}
    source_times: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol", ""))
        ticker = wanted.get(symbol)
        if ticker is None:
            continue
        try:
            mark = float(row["markPrice"])
            observed_at = int(row["time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PublicMarkDataError(f"public mark row is malformed for {ticker}") from exc
        if not math.isfinite(mark) or mark <= 0 or observed_at < 0:
            raise PublicMarkDataError(f"public mark row is invalid for {ticker}")
        marks[ticker] = mark
        source_times[ticker] = observed_at
    missing = sorted(set(selected) - set(marks))
    if missing:
        raise PublicMarkDataError(f"public mark response is missing {missing}")
    return {
        "provider": "BINANCE_PUBLIC_MARK_PRICE",
        "private_access": False,
        "observed_at": max(source_times.values()),
        "marks": marks,
        "source_times": source_times,
    }


def fetch_public_funding_history(
    instruments: Sequence[str],
    *,
    http_get: Callable[[str], object] = _http_get_json,
) -> dict[str, Any]:
    """Fetch recent public funding events and their point-in-time marks.

    The Binance endpoint is public and does not require an account key.  A
    single all-symbol request is used so a five-minute paper worker does not
    create one request per position.  Missing instruments are deliberately
    retained as empty series; ``funding_cash_flow`` then marks the valuation
    incomplete instead of inventing a rate or mark.
    """
    selected = tuple(sorted({str(item).upper() for item in instruments if str(item).strip()}))
    if not selected:
        raise PublicMarkDataError("funding universe is empty")
    url = f"{PUBLIC_FUNDING_URL}?{urlencode({'limit': 1000})}"
    payload = http_get(url)
    if not isinstance(payload, list):
        raise PublicMarkDataError("public funding response is not a list")
    wanted = {f"{ticker}USDT": ticker for ticker in selected}
    rates: dict[str, dict[str, float]] = {ticker: {} for ticker in selected}
    marks: dict[str, dict[str, float]] = {ticker: {} for ticker in selected}
    def ingest(rows: object, allowed: set[str]) -> None:
        if not isinstance(rows, list):
            raise PublicMarkDataError("public funding response is not a list")
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            ticker = wanted.get(str(row.get("symbol", "")))
            if ticker is None or ticker not in allowed:
                continue
            try:
                timestamp = int(row["fundingTime"])
                rate = float(row["fundingRate"])
            except (KeyError, TypeError, ValueError) as exc:
                raise PublicMarkDataError(
                    f"public funding row is malformed for {ticker}"
                ) from exc
            if timestamp < 0 or not math.isfinite(rate):
                raise PublicMarkDataError(f"public funding row is invalid for {ticker}")
            rates[ticker][str(timestamp)] = rate
            raw_mark = row.get("markPrice")
            if raw_mark is not None and str(raw_mark).strip():
                try:
                    mark = float(raw_mark)
                except (TypeError, ValueError) as exc:
                    raise PublicMarkDataError(
                        f"public funding mark is malformed for {ticker}"
                    ) from exc
                if not math.isfinite(mark) or mark <= 0:
                    raise PublicMarkDataError(f"public funding mark is invalid for {ticker}")
                marks[ticker][str(timestamp)] = mark

    ingest(payload, set(selected))
    # The all-symbol response is capped.  If a requested symbol is absent or
    # clearly stale, retry only that symbol so one long-tail Alpha leg does
    # not make the whole paper account incomplete.
    now_ms = int(time.time() * 1000)
    stale = {
        ticker
        for ticker in selected
        if not rates[ticker]
        or (
            max(int(timestamp) for timestamp in rates[ticker]) > 1_000_000_000_000
            and now_ms - max(int(timestamp) for timestamp in rates[ticker])
            > MAX_FUNDING_EVENT_AGE_MS
        )
    }
    for ticker in sorted(stale):
        fallback_url = f"{PUBLIC_FUNDING_URL}?{urlencode({'symbol': f'{ticker}USDT', 'limit': 100})}"
        ingest(http_get(fallback_url), {ticker})
    observed = max(
        (int(timestamp) for series in rates.values() for timestamp in series),
        default=None,
    )
    return {
        "provider": "BINANCE_PUBLIC_FUNDING_RATE",
        "private_access": False,
        "observed_at": observed,
        "rates": rates,
        "marks": marks,
    }


def _merge_time_series(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Merge cached and freshly fetched timestamp-keyed series."""
    merged: dict[str, dict[str, Any]] = {}
    for source in (existing, incoming):
        if not isinstance(source, Mapping):
            continue
        for instrument, series in source.items():
            if not isinstance(series, Mapping):
                continue
            target = merged.setdefault(_ticker_key(instrument), {})
            target.update({str(timestamp): value for timestamp, value in series.items()})
    return merged


def _finite_float(value: object, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PublicMarkDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result):
        raise PublicMarkDataError(f"{field} is not finite")
    return result


def _paper_fills(rows: Sequence[Mapping[str, Any]], *, alpha_id: str) -> list[PaperFill]:
    """Reconstruct deterministic fills from the immutable paper-order ledger."""
    fills: list[PaperFill] = []
    for row in rows:
        if str(row.get("alpha_id", "")) != alpha_id:
            continue
        if row.get("paper_execution") != "PAPER_FILLED":
            continue
        try:
            run_id = str(row["run_id"])
            timestamp = int(row["decision_ts"])
            cost_bps = _finite_float(row.get("cost_bps", DEFAULT_COST_BPS), field="cost_bps")
        except (KeyError, TypeError, ValueError) as exc:
            raise PublicMarkDataError(f"paper ledger identity is malformed for {alpha_id}") from exc
        orders = row.get("orders")
        if not isinstance(orders, list):
            raise PublicMarkDataError(f"paper ledger orders are missing for {alpha_id}")
        for index, order in enumerate(orders):
            if not isinstance(order, Mapping):
                raise PublicMarkDataError(f"paper ledger order is malformed for {alpha_id}")
            try:
                instrument = str(order["instrument"]).upper()
                side = OrderSide(str(order["side"]))
                quantity = int(order["quantity"])
                price = _finite_float(order["decision_mark_price"], field="decision_mark_price")
            except (KeyError, TypeError, ValueError) as exc:
                raise PublicMarkDataError(f"paper ledger order is invalid for {alpha_id}") from exc
            if not instrument or quantity <= 0 or price <= 0:
                raise PublicMarkDataError(f"paper ledger order has invalid values for {alpha_id}")
            commission = quantity * price * cost_bps / 1e4
            fills.append(
                PaperFill(
                    fill_id=f"paper:{run_id}:{index}",
                    order_id=f"paper-order:{run_id}:{index}",
                    run_id=run_id,
                    instrument_id=instrument,
                    side=side,
                    quantity=quantity,
                    price=price,
                    fill_method=FillMethod.NEXT_CLOSE,
                    timestamp=timestamp,
                    commission=commission,
                    slippage=0.0,
                )
            )
    return fills


def _ticker_key(value: object) -> str:
    ticker = str(value).upper().strip()
    return ticker[:-4] if ticker.endswith("USDT") else ticker


def funding_cash_flow(
    fills: Sequence[PaperFill],
    *,
    funding_rates: Mapping[str, Any] | None,
    funding_marks: Mapping[str, Any] | None,
    observed_at: int,
) -> dict[str, Any]:
    """Compute deterministic perp funding cash flow from public cached data.

    Positive funding is paid by longs and received by shorts, hence the
    negative signed-position convention.  Only funding events strictly after
    a fill and at or before ``observed_at`` are applied.  A missing mark for a
    funding event is reported, never replaced with a current-price guess.
    """
    if funding_rates is None:
        return {
            "total": 0.0,
            "by_instrument": {},
            "events": 0,
            "missing_instruments": [],
            "missing_events": [],
            "data_available": False,
        }
    ordered_fills = sorted(fills, key=lambda item: (int(item.timestamp), item.fill_id))
    instruments = sorted({fill.instrument_id.upper() for fill in ordered_fills})
    normalized_rates: dict[str, Mapping[Any, Any]] = {}
    missing_instruments: list[str] = []
    for instrument in instruments:
        series = None
        for key, value in funding_rates.items():
            if _ticker_key(key) == instrument:
                series = value
                break
        if isinstance(series, Mapping):
            normalized_rates[instrument] = series
        else:
            missing_instruments.append(instrument)

    events: list[tuple[int, str, float]] = []
    for instrument, series in normalized_rates.items():
        for raw_ts, raw_rate in series.items():
            try:
                timestamp = int(raw_ts)
                rate = float(raw_rate)
            except (TypeError, ValueError) as exc:
                raise PublicMarkDataError(
                    f"funding cache row is malformed for {instrument}"
                ) from exc
            if timestamp < 0 or not math.isfinite(rate):
                raise PublicMarkDataError(
                    f"funding cache row is invalid for {instrument}"
                )
            if timestamp <= observed_at:
                events.append((timestamp, instrument, rate))
    events.sort()

    marks = funding_marks if isinstance(funding_marks, Mapping) else {}

    def mark_at_or_before(instrument: str, timestamp: int) -> float | None:
        raw_series = None
        for key, value in marks.items():
            if _ticker_key(key) == instrument and isinstance(value, Mapping):
                raw_series = value
                break
        if raw_series is None:
            return None
        candidates: list[tuple[int, float]] = []
        for raw_ts, raw_price in raw_series.items():
            try:
                ts = int(raw_ts)
                price = float(raw_price)
            except (TypeError, ValueError):
                continue
            if ts <= timestamp and math.isfinite(price) and price > 0:
                candidates.append((ts, price))
        return max(candidates, default=(0, 0.0))[1] or None

    positions: dict[str, int] = {}
    fill_index = 0
    by_instrument: dict[str, float] = {}
    missing_events: list[str] = []
    applied_events = 0
    # The valuation must distinguish "no event has happened yet" from a
    # cache that stopped updating after a position was opened.  Only currently
    # non-flat instruments are subject to this freshness check.
    current_positions: dict[str, int] = {}
    latest_fill_ts: dict[str, int] = {}
    for fill in ordered_fills:
        timestamp = int(fill.timestamp)
        if timestamp > observed_at:
            continue
        instrument = fill.instrument_id.upper()
        sign = 1 if fill.side == OrderSide.BUY else -1
        current_positions[instrument] = current_positions.get(instrument, 0) + sign * int(fill.quantity)
        latest_fill_ts[instrument] = max(timestamp, latest_fill_ts.get(instrument, timestamp))
    latest_event_ts: dict[str, int] = {}
    for timestamp, instrument, _rate in events:
        latest_event_ts[instrument] = max(timestamp, latest_event_ts.get(instrument, timestamp))
    for instrument, quantity in current_positions.items():
        if quantity == 0:
            continue
        last_known = max(
            latest_fill_ts.get(instrument, 0),
            latest_event_ts.get(instrument, 0),
        )
        if observed_at - last_known > MAX_FUNDING_EVENT_AGE_MS:
            missing_events.append(f"{instrument}@after:{last_known}")
    for timestamp, instrument, rate in events:
        while fill_index < len(ordered_fills) and int(ordered_fills[fill_index].timestamp) < timestamp:
            fill = ordered_fills[fill_index]
            sign = 1 if fill.side == OrderSide.BUY else -1
            positions[fill.instrument_id.upper()] = (
                positions.get(fill.instrument_id.upper(), 0) + sign * int(fill.quantity)
            )
            fill_index += 1
        quantity = positions.get(instrument, 0)
        if quantity == 0:
            continue
        price = mark_at_or_before(instrument, timestamp)
        if price is None:
            missing_events.append(f"{instrument}@{timestamp}")
            continue
        amount = -float(quantity) * price * rate
        by_instrument[instrument] = by_instrument.get(instrument, 0.0) + amount
        applied_events += 1
    return {
        "total": sum(by_instrument.values()),
        "by_instrument": by_instrument,
        "events": applied_events,
        "missing_instruments": missing_instruments,
        "missing_events": missing_events,
        "data_available": not missing_instruments and not missing_events,
    }


def build_mark_snapshot(
    alpha_id: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    marks: Mapping[str, float],
    observed_at: int,
    source_times: Mapping[str, int] | None = None,
    aum: float = DEFAULT_AUM,
    funding_rates: Mapping[str, Any] | None = None,
    funding_marks: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Value one Alpha's paper account at a public mark observation."""
    if alpha_id not in paper_candidate_ids():
        raise ValueError(f"unsupported alpha: {alpha_id}")
    if not math.isfinite(float(aum)) or float(aum) <= 0:
        raise ValueError("aum must be a positive finite number")
    fills = _paper_fills(rows, alpha_id=alpha_id)
    used_instruments = sorted({fill.instrument_id for fill in fills})
    missing = sorted(set(used_instruments) - set(marks))
    if missing:
        raise PublicMarkDataError(f"paper mark snapshot is missing {missing}")
    resolved_marks = {instrument: _finite_float(marks[instrument], field=f"mark:{instrument}")
                      for instrument in used_instruments}
    result = reconcile(fills, marks=resolved_marks, initial_capital=float(aum))
    funding = funding_cash_flow(
        fills,
        funding_rates=funding_rates,
        funding_marks=funding_marks,
        observed_at=int(observed_at),
    )
    funding_pnl = float(funding["total"])
    funding_by_instrument = funding["by_instrument"]
    by_instrument = {
        instrument: {
            "realized": round(pnl.realized, 8),
            "unrealized": round(pnl.unrealized, 8),
            "commission": round(pnl.commission, 8),
            "slippage": round(pnl.slippage, 8),
            "funding": round(float(funding_by_instrument.get(instrument, 0.0)), 8),
            "net": round(pnl.net, 8),
        }
        for instrument, pnl in sorted(result.by_instrument.items())
    }
    source = {str(k): int(v) for k, v in (source_times or {}).items()}
    return {
        "schema_version": 1,
        "flow": "PAPER_REALTIME_MARK",
        "alpha_id": alpha_id,
        "observed_at": int(observed_at),
        "provider": "BINANCE_PUBLIC_MARK_PRICE",
        "source_times": source,
        "n_fills": len(fills),
        "positions": {k: int(v) for k, v in sorted(result.positions.items())},
        "marks": resolved_marks,
        "cash": round(result.cash + funding_pnl, 8),
        "market_value": round(result.market_value, 8),
        "equity": round(result.equity + funding_pnl, 8),
        "realized_pnl": round(sum(p.realized for p in result.by_instrument.values()), 8),
        "unrealized_pnl": round(sum(p.unrealized for p in result.by_instrument.values()), 8),
        "funding_pnl": round(funding_pnl, 8),
        "funding_events": int(funding["events"]),
        "funding_data_complete": bool(funding["data_available"]),
        "funding_missing_instruments": list(funding["missing_instruments"]),
        "funding_missing_events": list(funding["missing_events"]),
        "gross_pnl": round(result.gross_trading_pnl + funding_pnl, 8),
        "costs": round(result.total_costs, 8),
        "net_pnl": round(result.total_net_pnl + funding_pnl, 8),
        "return_on_aum": round((result.total_net_pnl + funding_pnl) / float(aum), 10),
        "turnover": round(result.turnover, 8),
        "reconciliation_residual": round(
            abs(
                (result.total_pnl + funding_pnl)
                - (result.gross_trading_pnl + funding_pnl - result.total_costs)
            ),
            10,
        ),
        "by_instrument": by_instrument,
        "status": "PAPER_MARKED" if fills else "PAPER_NO_POSITION",
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "broker_connectivity": False,
        "credentials_accessed": False,
    }


def _backtest_summary(flow: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    stages = flow.get("stages") if isinstance(flow, Mapping) else None
    rows = (stages or {}).get("authoritative_dry_runs", []) if isinstance(stages, Mapping) else []
    result: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping) or row.get("status") != "PASS":
            continue
        alpha_id = str(row.get("alpha_id", ""))
        snap = row.get("snapshot")
        if not alpha_id or not isinstance(snap, Mapping):
            continue
        pnl = snap.get("pnl") if isinstance(snap.get("pnl"), Mapping) else {}
        result[alpha_id] = {
            "run_id": snap.get("run_id"),
            "n_periods": snap.get("n_periods"),
            "total_return": pnl.get("total_return"),
            "sharpe": pnl.get("sharpe"),
            "sharpe_vt": pnl.get("sharpe_vt"),
            "max_drawdown": pnl.get("max_drawdown"),
            "max_drawdown_vt": pnl.get("max_drawdown_vt"),
            "reconciliation_residual": snap.get("reconciliation_residual"),
            "research_risk_decision": snap.get("research_risk_decision"),
            "provenance": "REAL_AUTHORITATIVE_L2_DRY_RUN",
        }
    return result


def _load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _pending_candidate_snapshot(
    alpha_id: str,
    preview: Mapping[str, Any],
    *,
    observed_at: int | None,
    generated_at: int,
    aum: float,
) -> dict[str, Any]:
    """Expose an unfilled challenger without putting it in the mark chain."""
    forward = preview.get("forward_observation")
    forward = forward if isinstance(forward, Mapping) else {}
    risk = preview.get("research_risk")
    risk = risk if isinstance(risk, Mapping) else {}
    bundle = preview.get("research_risk_bundle")
    bundle = bundle if isinstance(bundle, Mapping) else {}
    return {
        "schema_version": 1,
        "flow": "PAPER_REALTIME_CANDIDATE_STATUS",
        "alpha_id": alpha_id,
        "observed_at": int(observed_at) if isinstance(observed_at, int) else None,
        "generated_at": int(generated_at),
        "provider": "BINANCE_PUBLIC_MARK_PRICE",
        "status": "PAPER_PENDING",
        "candidate_stage": "FORWARD_PENDING",
        "forward_observation": dict(forward),
        "n_fills": 0,
        "positions": {},
        "cash": float(aum),
        "market_value": 0.0,
        "equity": float(aum),
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "funding_pnl": 0.0,
        "funding_events": 0,
        "funding_data_complete": False,
        "funding_missing_instruments": [],
        "funding_missing_events": [],
        "gross_pnl": 0.0,
        "costs": 0.0,
        "net_pnl": 0.0,
        "return_on_aum": 0.0,
        "turnover": 0.0,
        "reconciliation_residual": 0.0,
        "research_risk": dict(risk),
        "research_risk_bundle": dict(bundle),
        "paper_execution": "PAPER_BLOCKED",
        "persisted_realtime_snapshot": False,
        "execution_eligible": False,
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "broker_connectivity": False,
        "credentials_accessed": False,
    }


def _load_mark_history(path: str) -> dict[str, Any]:
    try:
        return load_mark_prices(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _existing_paper_ledger_is_markable(path: str) -> bool:
    """Return whether an existing paper ledger can be marked without new orders.

    A failed refresh or blocked forward gate must not erase the ability to
    observe positions that were already paper-filled.  This check is strictly
    read-only and only admits a complete set of immutable ``PAPER_FILLED``
    rows for the three registered Alphas.  It never creates a store and never
    authorizes a new paper decision.
    """
    store: PaperExecutionStore | None = None
    try:
        store = PaperExecutionStore(path, create=False)
        rows = store.rows()
    except (OSError, ValueError, RuntimeError):
        return False
    finally:
        if store is not None:
            store.close()
    by_alpha: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        alpha_id = str(row.get("alpha_id", ""))
        if alpha_id in paper_candidate_ids():
            by_alpha[alpha_id].append(row)
    if not all(
        by_alpha.get(alpha_id)
        and all(row.get("paper_execution") == "PAPER_FILLED" for row in by_alpha[alpha_id])
        for alpha_id in ALPHA_IDS
    ):
        return False
    # Once a challenger has a real paper fill, it becomes part of the
    # mark-to-market set automatically.  A blocked/immature challenger has no
    # position and therefore is not required to make replay available.
    return all(
        all(row.get("paper_execution") == "PAPER_FILLED" for row in rows_for_alpha)
        for alpha_id, rows_for_alpha in by_alpha.items()
        if alpha_id not in ALPHA_IDS
    )


def run_cycle(
    *,
    operator: Mapping[str, Any] | None = None,
    operator_runner: Callable[[], Mapping[str, Any]] | None = None,
    mark_provider: Callable[[Sequence[str]], Mapping[str, Any]] = fetch_public_mark_prices,
    paper_store_path: str = DEFAULT_PAPER_STORE,
    realtime_store_path: str = DEFAULT_STORE,
    output_path: str = DEFAULT_OUTPUT,
    aum: float = DEFAULT_AUM,
    funding_rates_path: str = DEFAULT_FUNDING_RATES,
    mark_history_path: str = DEFAULT_MARK_HISTORY,
    funding_provider: Callable[[Sequence[str]], Mapping[str, Any]] | None = None,
    generated_at_ms: int | None = None,
) -> dict[str, Any]:
    """Run one observable paper valuation cycle and atomically publish its receipt."""
    generated_at = int(generated_at_ms if generated_at_ms is not None else time.time() * 1000)
    candidate_ids = paper_candidate_ids()
    if operator is None:
        operator = dict(
            operator_runner() if operator_runner is not None else run_operator(advance_forward=False)
        )
    else:
        operator = dict(operator)
    operator_verdict = operator.get("verdict") if isinstance(operator, Mapping) else {}
    operator_ready = bool(
        isinstance(operator_verdict, Mapping)
        and operator_verdict.get("operator_completed") is True
        and operator_verdict.get("paper_preview_ready") is True
    )
    operator_execution_ready = operator_ready
    mark_only_replay = False
    if not operator_ready and _existing_paper_ledger_is_markable(paper_store_path):
        # Existing paper fills remain observable when a *new* decision is
        # blocked by data admission.  This is deliberately a mark-only mode:
        # no order preview is created and no new paper order can be admitted.
        operator_ready = True
        mark_only_replay = True
    errors: dict[str, str] = {}
    snapshots: dict[str, dict[str, Any]] = {}
    backtests: dict[str, dict[str, Any]] = {}
    execution_ids: tuple[str, ...] = tuple(ALPHA_IDS)
    inserted = 0
    provider_payload: dict[str, Any] = {}
    funding_refresh: dict[str, Any] = {
        "attempted": False,
        "provider": "LOCAL_CACHE",
        "observed_at": None,
        "error": None,
    }
    paper_ledger_rows = 0
    paper_ledger_decisions: set[int] = set()
    history: list[dict[str, Any]] = []
    funding_rates = _load_json(funding_rates_path)
    funding_marks = _load_mark_history(mark_history_path)
    if operator_ready:
        order_store = PaperExecutionStore(paper_store_path, create=True)
        try:
            order_rows = order_store.rows()
            paper_ledger_rows = len(order_rows)
            rows_by_alpha: dict[str, list[dict[str, Any]]] = defaultdict(list)
            instruments: set[str] = set()
            for row in order_rows:
                try:
                    decision_ts = int(row.get("decision_ts"))
                except (TypeError, ValueError):
                    decision_ts = None
                if decision_ts is not None and decision_ts >= 0:
                    paper_ledger_decisions.add(decision_ts)
                alpha_id = str(row.get("alpha_id", ""))
                if alpha_id not in candidate_ids:
                    continue
                rows_by_alpha[alpha_id].append(row)
                positions = row.get("paper_positions")
                if isinstance(positions, Mapping):
                    instruments.update(str(key).upper() for key in positions)
                for order in row.get("orders", []) if isinstance(row.get("orders"), list) else []:
                    if isinstance(order, Mapping) and order.get("instrument"):
                        instruments.add(str(order["instrument"]).upper())
            try:
                if not instruments:
                    raise PublicMarkDataError("paper order ledger has no positions")
                provider_payload = dict(mark_provider(tuple(sorted(instruments))))
                observed_at = int(provider_payload["observed_at"])
                marks = provider_payload.get("marks")
                if not isinstance(marks, Mapping):
                    raise PublicMarkDataError("public mark provider returned no marks")
                source_times = provider_payload.get("source_times")
                if isinstance(source_times, Mapping):
                    stale = sorted(
                        str(instrument)
                        for instrument, source_time in source_times.items()
                        if generated_at - int(source_time) > MAX_PUBLIC_MARK_AGE_MS
                    )
                    if stale:
                        raise PublicMarkDataError(
                            f"public mark data is stale for {stale}"
                        )
                # The local funding cache is intentionally only a bootstrap.
                # With the real public mark provider, refresh it from Binance
                # on every cycle; custom providers used by tests/operators can
                # inject their own funding provider without network access.
                if funding_provider is None and mark_provider is fetch_public_mark_prices:
                    funding_provider = fetch_public_funding_history
                if funding_provider is not None:
                    funding_refresh["attempted"] = True
                    funding_refresh["provider"] = "BINANCE_PUBLIC_FUNDING_RATE"
                    try:
                        fresh_funding = dict(funding_provider(tuple(sorted(instruments))))
                        funding_rates = _merge_time_series(
                            funding_rates,
                            fresh_funding.get("rates"),
                        )
                        funding_marks = _merge_time_series(
                            funding_marks,
                            fresh_funding.get("marks"),
                        )
                        funding_refresh["observed_at"] = fresh_funding.get("observed_at")
                    except Exception as exc:
                        # A funding endpoint outage must not erase the paper
                        # mark.  The freshness gate below makes the resulting
                        # funding component visibly incomplete instead.
                        funding_refresh["error"] = (
                            f"{type(exc).__name__}: {str(exc)[:240]}"
                        )
                execution_ids = tuple(
                    ALPHA_IDS
                    + tuple(
                        sorted(
                            alpha_id
                            for alpha_id, rows_for_alpha in rows_by_alpha.items()
                            if alpha_id not in ALPHA_IDS
                            and any(
                                row.get("paper_execution") == "PAPER_FILLED"
                                for row in rows_for_alpha
                            )
                        )
                    )
                )
                for alpha_id in execution_ids:
                    if not rows_by_alpha.get(alpha_id):
                        raise PublicMarkDataError(f"paper order ledger has no row for {alpha_id}")
                    snapshots[alpha_id] = build_mark_snapshot(
                        alpha_id,
                        rows_by_alpha[alpha_id],
                        marks=marks,
                        observed_at=observed_at,
                        source_times=source_times if isinstance(source_times, Mapping) else {},
                        aum=aum,
                        funding_rates=funding_rates,
                        funding_marks=funding_marks,
                    )
                realtime_store = PaperRealtimeStore(realtime_store_path, create=True)
                try:
                    for snapshot in snapshots.values():
                        if realtime_store.append_snapshot(snapshot):
                            inserted += 1
                    chain_valid = realtime_store.chain_valid()
                    for row in realtime_store.recent_rows(REALTIME_HISTORY_LIMIT):
                        try:
                            history.append(
                                {
                                    "alpha_id": str(row["alpha_id"]),
                                    "observed_at": int(row["observed_at"]),
                                    "equity": row.get("equity"),
                                    "net_pnl": row.get("net_pnl"),
                                }
                            )
                        except (KeyError, TypeError, ValueError):
                            raise PublicMarkDataError("paper mark history row is malformed")
                finally:
                    realtime_store.close()
                flow = (
                    (operator.get("stages") or {}).get("three_alpha_paper_flow", {}).get("flow", {})
                    if isinstance(operator.get("stages"), Mapping)
                    else {}
                )
                backtests = _backtest_summary(flow if isinstance(flow, Mapping) else {})
                preview_stage = (
                    (operator.get("stages") or {}).get("paper_order_preview", {})
                    if isinstance(operator.get("stages"), Mapping)
                    else {}
                )
                preview = (
                    preview_stage.get("preview", {})
                    if isinstance(preview_stage, Mapping)
                    else {}
                )
                preview_rows = (
                    preview.get("alphas", {})
                    if isinstance(preview, Mapping)
                    else {}
                )
                for alpha_id in execution_ids:
                    if alpha_id in backtests:
                        continue
                    row = preview_rows.get(alpha_id) if isinstance(preview_rows, Mapping) else None
                    if not isinstance(row, Mapping):
                        continue
                    risk = row.get("research_risk")
                    risk = risk if isinstance(risk, Mapping) else {}
                    returns = risk.get("alpha_returns")
                    backtests[alpha_id] = {
                        "run_id": row.get("run_id"),
                        "n_periods": len(returns.get(alpha_id, ()))
                        if isinstance(returns, Mapping)
                        and isinstance(returns.get(alpha_id), (list, tuple))
                        else None,
                        "total_return": None,
                        "sharpe": None,
                        "sharpe_vt": None,
                        "max_drawdown": None,
                        "max_drawdown_vt": None,
                        "reconciliation_residual": row.get("reconciliation_residual"),
                        "research_risk_decision": risk.get("decision"),
                        "provenance": "REAL_PAPER_CANDIDATE_RISK_INPUTS",
                    }
                # Keep every discovered challenger visible in the realtime
                # artifact.  These rows are intentionally not appended to the
                # immutable mark chain and can never count as execution rows.
                if isinstance(preview_rows, Mapping):
                    for alpha_id in candidate_ids:
                        if alpha_id in snapshots or alpha_id in ALPHA_IDS:
                            continue
                        row = preview_rows.get(alpha_id)
                        if isinstance(row, Mapping):
                            snapshots[alpha_id] = _pending_candidate_snapshot(
                                alpha_id,
                                row,
                                observed_at=(
                                    int(provider_payload["observed_at"])
                                    if isinstance(provider_payload.get("observed_at"), int)
                                    else None
                                ),
                                generated_at=generated_at,
                                aum=aum,
                            )
            except Exception as exc:
                errors["mark_cycle"] = f"{type(exc).__name__}: {str(exc)[:240]}"
                chain_valid = False
        finally:
            order_store.close()
    else:
        errors["operator"] = "L2 paper operator did not produce a ready paper preview"
        chain_valid = False

    paper_ready = bool(
        operator_ready
        and not errors
        and all(
            snapshots.get(alpha_id, {}).get("status") == "PAPER_MARKED"
            for alpha_id in ALPHA_IDS
        )
        and chain_valid
    )
    backtest_ready = all(alpha_id in backtests for alpha_id in execution_ids)
    cycle_completed = paper_ready and backtest_ready
    if paper_ready and mark_only_replay:
        readiness_class = "PAPER_MARKS_ONLY_REPLAY"
    elif paper_ready and operator_execution_ready:
        readiness_class = "PAPER_MARKS_AND_NEW_ORDERS"
    elif paper_ready:
        readiness_class = "PAPER_MARKS_READY_PENDING_OPERATOR"
    else:
        readiness_class = "PAPER_MARKS_BLOCKED"
    report = {
        "schema_version": 1,
        "flow": "PAPER_REALTIME_CYCLE",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": generated_at,
        "observed_at": provider_payload.get("observed_at"),
        "provider": provider_payload.get("provider", "BINANCE_PUBLIC_MARK_PRICE"),
        "funding_refresh": funding_refresh,
        "alphas": snapshots,
        "candidate_universe": {
            "count": len(candidate_ids),
            "execution_count": len(execution_ids),
            "pending": sorted(
                alpha_id
                for alpha_id, row in snapshots.items()
                if alpha_id not in execution_ids
                and row.get("status") == "PAPER_PENDING"
            ),
        },
        "backtests": backtests,
        "persistence": {
            "paper_order_store": paper_store_path,
            "realtime_store": realtime_store_path,
            "new_snapshots": inserted,
            "chain_valid": bool(chain_valid),
        },
        "paper_ledger": {
            "n_rows": paper_ledger_rows,
            "n_decisions": len(paper_ledger_decisions),
            "latest_decision_ts": max(paper_ledger_decisions)
            if paper_ledger_decisions
            else None,
        },
        "history": history,
        "errors": errors,
        "operator_mode": (
            "PAPER_OPERATOR_READY"
            if operator_execution_ready
            else MARK_ONLY_REPLAY_REASON
            if mark_only_replay
            else "PAPER_OPERATOR_BLOCKED"
        ),
        "readiness_class": readiness_class,
        "verdict": {
            "cycle_completed": cycle_completed,
            "paper_mark_ready": paper_ready,
            "backtest_reference_present_for_all_three": backtest_ready,
            "backtest_reference_present_for_all_execution_candidates": backtest_ready,
            "candidate_universe_count": len(candidate_ids),
            "execution_paper_candidate_count": len(execution_ids),
            "new_paper_orders_allowed": operator_execution_ready,
            "live_allowed": False,
            "order_submission_allowed": False,
            "human_review_required": True,
        },
        "safety": {
            "paper_only": True,
            "public_market_data_only": True,
            "private_binance_access": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
            "broker_order_submission": False,
        },
    }
    atomic_write_json(output_path, report, indent=2)
    return report


def _print_summary(
    report: Mapping[str, Any],
    *,
    health_report: Mapping[str, Any] | None = None,
) -> None:
    verdict = report.get("verdict") if isinstance(report.get("verdict"), Mapping) else {}
    alpha_rows = report.get("alphas") if isinstance(report.get("alphas"), Mapping) else {}
    alpha_summary: dict[str, dict[str, Any]] = {}
    for alpha_id in ALPHA_IDS:
        row = alpha_rows.get(alpha_id)
        if isinstance(row, Mapping):
            alpha_summary[alpha_id] = {
                "status": row.get("status"),
                "equity": row.get("equity"),
                "net_pnl": row.get("net_pnl"),
                "funding_pnl": row.get("funding_pnl"),
                "funding_events": row.get("funding_events"),
                "funding_data_complete": row.get("funding_data_complete"),
                "reconciliation_residual": row.get("reconciliation_residual"),
            }
    backtest_rows = report.get("backtests") if isinstance(report.get("backtests"), Mapping) else {}
    backtest_summary: dict[str, dict[str, Any]] = {}
    for alpha_id in ALPHA_IDS:
        row = backtest_rows.get(alpha_id)
        if isinstance(row, Mapping):
            backtest_summary[alpha_id] = {
                "run_id": row.get("run_id"),
                "sharpe": row.get("sharpe"),
                "sharpe_vt": row.get("sharpe_vt"),
                "max_drawdown": row.get("max_drawdown"),
            }
    print(
        json.dumps(
            {
                "cycle_completed": verdict.get("cycle_completed", False),
                "paper_mark_ready": verdict.get("paper_mark_ready", False),
                "backtest_reference_present_for_all_three": verdict.get(
                    "backtest_reference_present_for_all_three", False
                ),
                "observed_at": report.get("observed_at"),
                "new_snapshots": (report.get("persistence") or {}).get("new_snapshots"),
                "paper_ledger": report.get("paper_ledger", {}),
                "history_points": len(report.get("history", []))
                if isinstance(report.get("history"), list)
                else 0,
                "errors": report.get("errors", {}),
                "alphas": alpha_summary,
                "backtests": backtest_summary,
                "live_allowed": False,
                "order_submission_allowed": False,
            },
            ensure_ascii=False,
        )
    )
    if health_report is not None:
        health_verdict = health_report.get("verdict")
        health_verdict = (
            health_verdict if isinstance(health_verdict, Mapping) else {}
        )
        print(
            json.dumps(
                {
                    "paper_ready": health_verdict.get("paper_ready", False),
                    "blockers": health_report.get("blockers", []),
                    "live_allowed": False,
                    "order_submission_allowed": False,
                },
                ensure_ascii=False,
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=1, help="foreground cycles; ignored with --follow")
    parser.add_argument("--follow", action="store_true", help="keep polling in the foreground until Ctrl-C")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--refresh-paper", action="store_true", help="rerun the L2 paper operator every cycle")
    parser.add_argument("--no-bootstrap", action="store_true", help="reuse the existing operator receipt on the first cycle")
    parser.add_argument("--aum", type=float, default=DEFAULT_AUM, help="paper notional only")
    parser.add_argument("--paper-store", default=DEFAULT_PAPER_STORE)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--operator-receipt", default=DEFAULT_OPERATOR_RECEIPT)
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="run the fail-closed paper health check after every cycle",
    )
    parser.add_argument("--health-output", default=DEFAULT_HEALTH_OUTPUT)
    parser.add_argument("--funding-rates", default=DEFAULT_FUNDING_RATES)
    parser.add_argument("--mark-history", default=DEFAULT_MARK_HISTORY)
    args = parser.parse_args(argv)
    if args.cycles < 1 and not args.follow:
        parser.error("--cycles must be positive unless --follow is used")
    if not math.isfinite(args.interval_seconds) or args.interval_seconds < 1.0:
        parser.error("--interval-seconds must be at least 1")
    if not math.isfinite(args.aum) or args.aum <= 0:
        parser.error("--aum must be positive")
    total = 0
    first = True
    custom_profile = (
        not math.isclose(args.aum, DEFAULT_AUM, rel_tol=0.0, abs_tol=1e-9)
        or os.path.normcase(args.paper_store)
        != os.path.normcase(DEFAULT_PAPER_STORE)
    )
    profile_preview_output = (
        os.path.join(
            os.path.dirname(args.operator_receipt) or ".",
            f"{os.path.splitext(os.path.basename(args.operator_receipt))[0]}"
            f"_paper_order_preview_{args.aum:g}.json",
        )
        if custom_profile
        else "data/cache/paper_order_preview.json"
    )
    try:
        while args.follow or total < args.cycles:
            should_bootstrap = first and not args.no_bootstrap
            should_refresh = args.refresh_paper or should_bootstrap
            if should_refresh:
                def operator_runner() -> Mapping[str, Any]:
                    return run_operator(
                        advance_forward=False,
                        output_path=args.operator_receipt,
                        preview_runner=lambda: run_preview(
                            aum=args.aum,
                            paper_store_path=args.paper_store,
                            output_path=profile_preview_output,
                        ),
                    )
            else:
                def operator_runner() -> Mapping[str, Any]:
                    return _load_json(args.operator_receipt)
            report = run_cycle(
                operator_runner=operator_runner,
                paper_store_path=args.paper_store,
                realtime_store_path=args.store,
                output_path=args.output,
                aum=args.aum,
                funding_rates_path=args.funding_rates,
                mark_history_path=args.mark_history,
            )
            health_report: Mapping[str, Any] | None = None
            if args.health_check:
                health_report = build_report(
                    realtime_status_path=args.output,
                    order_preview_path=profile_preview_output,
                    output_path=args.health_output,
                )
            _print_summary(report, health_report=health_report)
            total += 1
            first = False
            if not args.follow and total >= args.cycles:
                break
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        print("paper_realtime: stopped by operator")
        return SCHEDULE_EXIT_SUCCESS
    except Exception as exc:
        print(f"paper_realtime: {type(exc).__name__}: {str(exc)[:240]}")
        return SCHEDULE_EXIT_FAILURE
    cycle_ok = bool(report.get("verdict", {}).get("cycle_completed"))
    if args.health_check:
        health_ok = bool(
            isinstance(health_report, Mapping)
            and isinstance(health_report.get("verdict"), Mapping)
            and health_report["verdict"].get("paper_ready") is True
        )
    else:
        health_ok = True
    if not cycle_ok:
        return SCHEDULE_EXIT_FAILURE
    return SCHEDULE_EXIT_SUCCESS if health_ok else SCHEDULE_EXIT_SAFE_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ALPHA_IDS",
    "DEFAULT_HEALTH_OUTPUT",
    "DEFAULT_OUTPUT",
    "DEFAULT_STORE",
    "MARK_ONLY_REPLAY_REASON",
    "PublicMarkDataError",
    "build_mark_snapshot",
    "fetch_public_funding_history",
    "fetch_public_mark_prices",
    "run_cycle",
]
