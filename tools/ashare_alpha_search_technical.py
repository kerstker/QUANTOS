#!/usr/bin/env python3
"""Strict, read-only A-share Alpha search with next-open execution semantics.

This is deliberately a research diagnostic, not a promotion or paper-trading
entrypoint.  It reads an immutable SQLite authority copy, observes signals at the
close of t, executes at the open of t+1, uses non-overlapping holding periods, and
keeps the 2025+ confirmation split out of candidate selection.  Every artifact is
labelled TECHNICAL_VALIDATION_ONLY because independently signed source/result
authority is not integrated yet.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import math
import sqlite3
import statistics
import warnings
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from scipy import stats


TECHNICAL_STATUS = "TECHNICAL_VALIDATION_ONLY"
SCHEMA_VERSION = 2
DISCOVERY_END = 20231231
SELECTION_START = 20240101
SELECTION_END = 20241231
CONFIRMATION_START = 20250101


@dataclasses.dataclass(frozen=True)
class CandidateSpec:
    name: str
    family: str
    window: int
    skip: int
    hold_days: int

    @property
    def factor_key(self) -> tuple[str, int, int]:
        return self.family, self.window, self.skip


@dataclasses.dataclass(frozen=True)
class RebalancePeriod:
    signal_row: int
    entry_row: int
    exit_row: int
    signal_date: int
    entry_date: int
    exit_date: int


@dataclasses.dataclass
class SimulationResult:
    period_returns: list[float]
    turnovers: list[float]
    selected_names: list[list[int]]
    unpriced_legs: int


@dataclasses.dataclass(frozen=True)
class MarketPanel:
    dates: np.ndarray
    instruments: tuple[str, ...]
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    amounts: np.ndarray
    base_eligible: np.ndarray
    trailing_adv20: np.ndarray
    bad_bars: np.ndarray


def make_rebalance_periods(
    dates: np.ndarray, hold_days: int, phase: int = 0
) -> list[RebalancePeriod]:
    """Map close-t signals to open-(t+1) entry and next-rebalance-open exit."""
    if hold_days <= 0:
        raise ValueError("hold_days must be positive")
    if phase < 0 or phase >= hold_days:
        raise ValueError("phase must satisfy 0 <= phase < hold_days")
    out: list[RebalancePeriod] = []
    for signal_row in range(phase, len(dates), hold_days):
        entry_row = signal_row + 1
        exit_row = signal_row + hold_days + 1
        if exit_row >= len(dates):
            break
        out.append(
            RebalancePeriod(
                signal_row=signal_row,
                entry_row=entry_row,
                exit_row=exit_row,
                signal_date=int(dates[signal_row]),
                entry_date=int(dates[entry_row]),
                exit_date=int(dates[exit_row]),
            )
        )
    return out


def _valid_pair_ratio(numer: np.ndarray, denom: np.ndarray) -> np.ndarray:
    out = np.full(numer.shape, np.nan, dtype=float)
    valid = np.isfinite(numer) & np.isfinite(denom) & (denom > 0)
    out[valid] = numer[valid] / denom[valid] - 1.0
    return out


def _daily_returns(close: np.ndarray) -> np.ndarray:
    out = np.full(close.shape, np.nan, dtype=float)
    out[1:] = _valid_pair_ratio(close[1:], close[:-1])
    return out


def _rolling_stat(
    values: np.ndarray,
    window: int,
    operation: str,
    *,
    min_fraction: float = 0.8,
) -> np.ndarray:
    out = np.full(values.shape, np.nan, dtype=float)
    min_n = max(2, math.ceil(window * min_fraction))
    for row in range(window - 1, values.shape[0]):
        block = values[row - window + 1 : row + 1]
        counts = np.isfinite(block).sum(axis=0)
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore", category=RuntimeWarning)
            if operation == "mean":
                result = np.nanmean(block, axis=0)
            elif operation == "std":
                result = np.nanstd(block, axis=0, ddof=1)
            elif operation == "max":
                result = np.nanmax(block, axis=0)
            elif operation == "min":
                result = np.nanmin(block, axis=0)
            else:
                raise ValueError(f"unknown rolling operation: {operation}")
        result[counts < min_n] = np.nan
        out[row] = result
    return out


def build_factor_matrix(
    spec: CandidateSpec,
    close: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    open_prices: np.ndarray | None = None,
) -> np.ndarray:
    """Build one close-t factor from rows no later than t."""
    family, window, skip = spec.family, spec.window, spec.skip
    if window <= 0 or skip < 0 or skip >= window:
        raise ValueError("factor window must be positive and exceed skip")
    returns = _daily_returns(close)

    if family in {"momentum", "reversal"}:
        out = np.full(close.shape, np.nan, dtype=float)
        numer_end = close.shape[0] - skip if skip else close.shape[0]
        numer = close[window - skip : numer_end]
        denom = close[: close.shape[0] - window]
        raw = _valid_pair_ratio(numer, denom)
        out[window:] = raw
        return out * -1.0 if family == "reversal" else out

    if family == "low_vol":
        return -_rolling_stat(returns, window, "std")
    if family == "low_downside_vol":
        downside = np.where(returns < 0, returns, 0.0)
        downside[~np.isfinite(returns)] = np.nan
        return -_rolling_stat(downside, window, "std")
    if family == "avoid_max_return":
        return -_rolling_stat(returns, window, "max")

    if family in {"price_position_reversal", "price_position_momentum"}:
        lo = _rolling_stat(close, window, "min")
        hi = _rolling_stat(close, window, "max")
        span = hi - lo
        position = np.full(close.shape, np.nan, dtype=float)
        valid = np.isfinite(close) & np.isfinite(lo) & np.isfinite(span) & (span > 0)
        position[valid] = (close[valid] - lo[valid]) / span[valid]
        return -position if family.endswith("reversal") else position

    if family in {"volume_ratio_reversal", "volume_ratio_momentum"}:
        short_mean = _rolling_stat(volume, skip, "mean")
        long_mean = _rolling_stat(volume, window, "mean")
        ratio = np.full(close.shape, np.nan, dtype=float)
        valid = np.isfinite(short_mean) & np.isfinite(long_mean) & (long_mean > 0)
        ratio[valid] = short_mean[valid] / long_mean[valid]
        return -ratio if family.endswith("reversal") else ratio

    if family in {"liquid", "illiquid"}:
        illiq = np.full(close.shape, np.nan, dtype=float)
        valid = np.isfinite(returns) & np.isfinite(amount) & (amount > 0)
        illiq[valid] = np.abs(returns[valid]) / amount[valid]
        rolled = _rolling_stat(illiq, window, "mean")
        return -rolled if family == "liquid" else rolled

    if family in {"low_turnover", "high_turnover"}:
        rolled = _rolling_stat(amount, window, "mean")
        # log is monotonic and keeps numerical scales manageable for diagnostics.
        logged = np.full(close.shape, np.nan, dtype=float)
        valid = np.isfinite(rolled) & (rolled > 0)
        logged[valid] = np.log(rolled[valid])
        return -logged if family == "low_turnover" else logged

    if family in {"acceleration_reversal", "acceleration_momentum"}:
        recent = np.full(close.shape, np.nan, dtype=float)
        recent[skip:] = _valid_pair_ratio(close[skip:], close[:-skip])
        long = np.full(close.shape, np.nan, dtype=float)
        long[window:] = _valid_pair_ratio(close[window:], close[:-window])
        acceleration = recent - long
        return -acceleration if family.endswith("reversal") else acceleration

    if family in {"signed_amount_flow", "signed_amount_flow_reversal"}:
        signed_amount = np.full(close.shape, np.nan, dtype=float)
        valid = np.isfinite(returns) & np.isfinite(amount) & (amount > 0)
        signed_amount[valid] = np.sign(returns[valid]) * amount[valid]
        numerator = _rolling_stat(signed_amount, window, "mean")
        denominator = _rolling_stat(amount, window, "mean")
        flow = np.full(close.shape, np.nan, dtype=float)
        valid_ratio = (
            np.isfinite(numerator) & np.isfinite(denominator) & (denominator > 0)
        )
        flow[valid_ratio] = numerator[valid_ratio] / denominator[valid_ratio]
        return -flow if family.endswith("reversal") else flow

    if family in {"residual_momentum", "residual_reversal"}:
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore", category=RuntimeWarning)
            market_return = np.nanmean(returns, axis=1, keepdims=True)
        residual = returns - market_return
        rolled = _rolling_stat(residual, window, "mean")
        return -rolled if family == "residual_reversal" else rolled

    if family in {
        "overnight_gap_momentum",
        "overnight_gap_reversal",
        "intraday_momentum",
        "intraday_reversal",
    }:
        if open_prices is None:
            raise ValueError(f"{family} requires open prices")
        raw = np.full(close.shape, np.nan, dtype=float)
        if family.startswith("overnight_gap"):
            raw[1:] = _valid_pair_ratio(open_prices[1:], close[:-1])
        else:
            raw = _valid_pair_ratio(close, open_prices)
        rolled = _rolling_stat(raw, window, "mean")
        return -rolled if family.endswith("reversal") else rolled

    raise ValueError(f"unknown factor family: {family}")


def _target_weights(
    score_row: np.ndarray, eligible_row: np.ndarray, basket_size: int
) -> tuple[dict[int, float], list[int]]:
    indices = np.flatnonzero(eligible_row & np.isfinite(score_row))
    if indices.size == 0:
        return {}, []
    count = min(basket_size, int(indices.size))
    if count == indices.size:
        chosen = indices.tolist()
    else:
        values = score_row[indices]
        local = np.argpartition(-values, count - 1)[:count]
        selected = indices[local]
        chosen = sorted(selected.tolist(), key=lambda i: (-float(score_row[i]), i))
    weight = 1.0 / len(chosen)
    return {i: weight for i in chosen}, chosen


def simulate_long_only(
    scores: np.ndarray,
    opens: np.ndarray,
    eligible: np.ndarray,
    periods: Sequence[RebalancePeriod],
    *,
    basket_size: int,
    cost_bps: float,
    bad_bars: np.ndarray | None = None,
) -> SimulationResult:
    """Run non-overlapping long-only periods with target-to-drift turnover costs."""
    cost = float(cost_bps) / 10_000.0
    previous_end_weights: dict[int, float] = {}
    returns: list[float] = []
    turnovers: list[float] = []
    selections: list[list[int]] = []
    unpriced_legs = 0

    for period_index, period in enumerate(periods):
        target, selected = _target_weights(
            scores[period.signal_row], eligible[period.signal_row], basket_size
        )
        names = set(target) | set(previous_end_weights)
        entry_turnover = sum(
            abs(target.get(i, 0.0) - previous_end_weights.get(i, 0.0)) for i in names
        )

        asset_returns: dict[int, float] = {}
        for instrument_index, weight in target.items():
            entry = opens[period.entry_row, instrument_index]
            exit_price = opens[period.exit_row, instrument_index]
            crosses_bad_bar = bool(
                bad_bars is not None
                and np.any(
                    bad_bars[
                        period.entry_row : period.exit_row + 1, instrument_index
                    ]
                )
            )
            if (
                not crosses_bad_bar
                and np.isfinite(entry)
                and entry > 0
                and np.isfinite(exit_price)
                and exit_price > 0
            ):
                asset_returns[instrument_index] = float(exit_price / entry - 1.0)
            else:
                # A missing execution leg remains cash/frozen at zero return; never replace
                # the selected name using future availability information.
                asset_returns[instrument_index] = 0.0
                unpriced_legs += 1

        gross_return = sum(target[i] * asset_returns[i] for i in target)
        gross_equity = 1.0 + gross_return
        if gross_equity <= 0:
            end_weights = {}
        else:
            end_weights = {
                i: target[i] * (1.0 + asset_returns[i]) / gross_equity for i in target
            }

        terminal_turnover = sum(end_weights.values()) if period_index == len(periods) - 1 else 0.0
        total_turnover = entry_turnover + terminal_turnover
        returns.append(float(gross_return - total_turnover * cost))
        turnovers.append(float(total_turnover))
        selections.append(selected)
        previous_end_weights = {} if terminal_turnover else end_weights

    return SimulationResult(returns, turnovers, selections, unpriced_legs)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _board_limit_band(instrument: str) -> float:
    code = instrument.split(".")[0]
    if code.startswith(("300", "301", "688")):
        return 0.205
    if instrument.endswith(".BJ"):
        return 0.305
    return 0.105


def _detect_bad_bars(closes: np.ndarray, instruments: Sequence[str]) -> np.ndarray:
    """Flag close jumps beyond the board band; the raw series remains untouched."""
    mask = np.zeros(closes.shape, dtype=bool)
    for column, instrument in enumerate(instruments):
        previous = math.nan
        band = _board_limit_band(instrument)
        for row in range(closes.shape[0]):
            value = closes[row, column]
            if not np.isfinite(value) or value <= 0:
                continue
            if np.isfinite(previous) and abs(value / previous - 1.0) > band:
                mask[row, column] = True
            # Always advance the reference so one permanent scale break does not
            # cascade into false positives on every following bar.
            previous = value
    return mask


def _bad_bar_cooldown(bad_bars: np.ndarray, window: int = 250) -> np.ndarray:
    cooldown = np.zeros(bad_bars.shape, dtype=bool)
    for column in range(bad_bars.shape[1]):
        latest_bad = -window - 1
        for row in range(bad_bars.shape[0]):
            if bad_bars[row, column]:
                latest_bad = row
            if row - latest_bad < window:
                cooldown[row, column] = True
    return cooldown


def _load_panel_cache(
    db_path: Path, cache_path: Path, *, min_adv_cny: float
) -> tuple[MarketPanel, dict] | None:
    if not cache_path.is_file():
        return None
    expected_db_sha256 = _sha256(db_path)
    with np.load(cache_path, allow_pickle=False) as cached:
        if str(cached["db_sha256"].item()) != expected_db_sha256:
            raise RuntimeError(f"panel cache database hash mismatch: {cache_path}")
        if float(cached["min_adv_cny"].item()) != float(min_adv_cny):
            raise RuntimeError(f"panel cache ADV threshold mismatch: {cache_path}")
        panel = MarketPanel(
            dates=cached["dates"],
            instruments=tuple(str(value) for value in cached["instruments"]),
            opens=cached["opens"],
            highs=cached["highs"],
            lows=cached["lows"],
            closes=cached["closes"],
            volumes=cached["volumes"],
            amounts=cached["amounts"],
            base_eligible=cached["base_eligible"],
            trailing_adv20=cached["trailing_adv20"],
            bad_bars=cached["bad_bars"],
        )
        metadata = json.loads(str(cached["metadata_json"].item()))
    metadata["panel_cache"] = {
        "status": "BOUND_CACHE_HIT",
        "path": str(cache_path.resolve()),
        "sha256": _sha256(cache_path),
    }
    return panel, metadata


def _write_panel_cache(
    db_path: Path,
    cache_path: Path,
    panel: MarketPanel,
    metadata: dict,
    *,
    min_adv_cny: float,
) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_name(f".{cache_path.name}.tmp.npz")
    np.savez_compressed(
        temporary,
        db_sha256=np.array(_sha256(db_path)),
        min_adv_cny=np.array(float(min_adv_cny)),
        metadata_json=np.array(json.dumps(metadata, ensure_ascii=False, sort_keys=True)),
        dates=panel.dates,
        instruments=np.asarray(panel.instruments),
        opens=panel.opens,
        highs=panel.highs,
        lows=panel.lows,
        closes=panel.closes,
        volumes=panel.volumes,
        amounts=panel.amounts,
        base_eligible=panel.base_eligible,
        trailing_adv20=panel.trailing_adv20,
        bad_bars=panel.bad_bars,
    )
    temporary.replace(cache_path)


def _load_panel(
    db_path: Path,
    *,
    min_adv_cny: float,
    panel_cache: Path | None = None,
) -> tuple[MarketPanel, dict]:
    if panel_cache is not None:
        cached = _load_panel_cache(db_path, panel_cache, min_adv_cny=min_adv_cny)
        if cached is not None:
            return cached
    uri = f"file:{db_path.resolve()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"SQLite quick_check failed: {quick_check}")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        compact = "daily_prices" in tables
        if not compact and "bars" not in tables:
            raise RuntimeError("unsupported A-share SQLite schema")
        if compact:
            future_rows = connection.execute(
                "SELECT COUNT(*) FROM daily_prices WHERE knowledge_time > trade_date"
            ).fetchone()[0]
        else:
            future_rows = connection.execute(
                "SELECT COUNT(*) FROM bars WHERE knowledge_time > event_time"
            ).fetchone()[0]
        if future_rows:
            raise RuntimeError(f"future-known rows present: {future_rows}")
        instrument_sql = (
            "SELECT DISTINCT instrument FROM daily_prices ORDER BY instrument"
            if compact
            else "SELECT DISTINCT key FROM bars ORDER BY key"
        )
        date_sql = (
            "SELECT DISTINCT trade_date FROM daily_prices ORDER BY trade_date"
            if compact
            else "SELECT DISTINCT event_time FROM bars ORDER BY event_time"
        )
        instruments = tuple(row[0] for row in connection.execute(instrument_sql))
        dates = np.array([row[0] for row in connection.execute(date_sql)], dtype=np.int64)
        inst_index = {name: index for index, name in enumerate(instruments)}
        date_index = {int(date): index for index, date in enumerate(dates)}
        shape = (len(dates), len(instruments))
        opens = np.full(shape, np.nan, dtype=float)
        highs = np.full(shape, np.nan, dtype=float)
        lows = np.full(shape, np.nan, dtype=float)
        closes = np.full(shape, np.nan, dtype=float)
        volumes = np.full(shape, np.nan, dtype=float)
        source_contract: dict[str, object] = {}
        if compact:
            amounts = np.full(shape, np.nan, dtype=float)
            for values in connection.execute(
                "SELECT instrument,trade_date,open,high,low,close,volume,amount "
                "FROM daily_prices WHERE knowledge_time <= trade_date "
                "ORDER BY trade_date,instrument"
            ):
                key, event_time, open_, high, low, close, volume, amount = values
                row, col = date_index[int(event_time)], inst_index[key]
                opens[row, col] = float(open_)
                highs[row, col] = float(high)
                lows[row, col] = float(low)
                closes[row, col] = float(close)
                volumes[row, col] = float(volume)
                amounts[row, col] = float(amount)
            row_count = connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
            if "metadata" in tables:
                source_contract = {
                    key: json.loads(value)
                    for key, value in connection.execute(
                        "SELECT key,value_json FROM metadata"
                    )
                }
        else:
            for key, event_time, knowledge_time, payload_json in connection.execute(
                "SELECT key,event_time,knowledge_time,payload_json FROM bars "
                "WHERE knowledge_time <= event_time ORDER BY event_time,key,knowledge_time"
            ):
                payload = json.loads(payload_json)
                row, col = date_index[int(event_time)], inst_index[key]
                opens[row, col] = float(payload["open"])
                highs[row, col] = float(payload["high"])
                lows[row, col] = float(payload["low"])
                closes[row, col] = float(payload["close"])
                volumes[row, col] = float(payload["volume"])
            row_count = connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
            amounts = volumes * (opens + highs + lows + closes) / 4.0
    finally:
        connection.close()

    bad_bars = _detect_bad_bars(closes, instruments)
    bad_bar_cooldown = _bad_bar_cooldown(bad_bars, 250)
    trailing_adv20 = _rolling_stat(amounts, 20, "mean", min_fraction=0.75)
    observed = np.isfinite(closes) & (closes > 0)
    listed_days = np.cumsum(observed, axis=0)
    base_eligible = (
        observed
        & np.isfinite(volumes)
        & (volumes > 0)
        & np.isfinite(trailing_adv20)
        & (trailing_adv20 >= min_adv_cny)
        & (listed_days >= 60)
        & ~bad_bar_cooldown
    )
    panel = MarketPanel(
        dates,
        instruments,
        opens,
        highs,
        lows,
        closes,
        volumes,
        amounts,
        base_eligible,
        trailing_adv20,
        bad_bars,
    )
    metadata = {
        "sqlite_quick_check": quick_check,
        "future_known_rows": int(future_rows),
        "rows": int(row_count),
        "instruments": len(instruments),
        "dates": len(dates),
        "first_date": int(dates[0]),
        "last_date": int(dates[-1]),
        "sha256": _sha256(db_path),
        "bad_bar_count": int(bad_bars.sum()),
        "bad_bar_instruments": int(np.any(bad_bars, axis=0).sum()),
        "bad_bar_factor_cooldown_days": 250,
        "storage_contract": "COMPACT_DAILY_PRICES" if compact else "LEGACY_JSON_BARS",
        "amount_semantics": "SOURCE_REPORTED" if compact else "OHLC_VOLUME_PROXY",
    }
    if source_contract:
        metadata["intake_contract"] = source_contract
    if panel_cache is not None:
        _write_panel_cache(
            db_path,
            panel_cache,
            panel,
            metadata,
            min_adv_cny=min_adv_cny,
        )
        metadata["panel_cache"] = {
            "status": "BOUND_CACHE_CREATED",
            "path": str(panel_cache.resolve()),
            "sha256": _sha256(panel_cache),
        }
    return panel, metadata


def _metric(returns: Sequence[float], periods_per_year: float) -> dict:
    clean = np.asarray([value for value in returns if math.isfinite(value)], dtype=float)
    if clean.size == 0:
        return {
            "n_periods": 0,
            "total_return": None,
            "annualized_return": None,
            "annualized_sharpe": None,
            "max_drawdown": None,
            "mean_return": None,
            "t_stat": None,
            "one_sided_p": None,
        }
    equity = np.cumprod(1.0 + clean)
    total = float(equity[-1] - 1.0)
    annualized = (
        float((1.0 + total) ** (periods_per_year / clean.size) - 1.0)
        if total > -1.0
        else -1.0
    )
    sample_std = float(np.std(clean, ddof=1)) if clean.size >= 2 else 0.0
    sharpe = (
        float(np.mean(clean) / sample_std * math.sqrt(periods_per_year))
        if sample_std > 0
        else None
    )
    peaks = np.maximum.accumulate(np.concatenate(([1.0], equity)))
    curve = np.concatenate(([1.0], equity))
    max_drawdown = float(np.max(1.0 - curve / peaks))
    if sample_std > 0:
        t_stat = float(np.mean(clean) / (sample_std / math.sqrt(clean.size)))
        one_sided_p = float(stats.t.sf(t_stat, clean.size - 1))
    else:
        t_stat = None
        one_sided_p = None
    return {
        "n_periods": int(clean.size),
        "total_return": total,
        "annualized_return": annualized,
        "annualized_sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "mean_return": float(np.mean(clean)),
        "t_stat": t_stat,
        "one_sided_p": one_sided_p,
    }


def _candidate_specs(holds: Iterable[int]) -> list[CandidateSpec]:
    definitions: list[tuple[str, str, int, int]] = []
    definitions.extend((f"reversal_{w}", "reversal", w, 0) for w in (3, 5, 10, 20, 40))
    definitions.extend(
        [
            ("momentum_20_skip5", "momentum", 20, 5),
            ("momentum_60_skip5", "momentum", 60, 5),
            ("momentum_120_skip20", "momentum", 120, 20),
            ("momentum_250_skip20", "momentum", 250, 20),
        ]
    )
    definitions.extend((f"low_vol_{w}", "low_vol", w, 0) for w in (10, 20, 40, 60, 120))
    definitions.extend(
        (f"low_downside_vol_{w}", "low_downside_vol", w, 0) for w in (20, 60)
    )
    definitions.extend(
        (f"avoid_max_return_{w}", "avoid_max_return", w, 0) for w in (10, 20, 40, 60)
    )
    definitions.extend(
        (f"price_position_reversal_{w}", "price_position_reversal", w, 0)
        for w in (60, 120, 250)
    )
    definitions.extend(
        (f"price_position_momentum_{w}", "price_position_momentum", w, 0)
        for w in (120, 250)
    )
    definitions.extend(
        [
            ("volume_ratio_reversal_5_20", "volume_ratio_reversal", 20, 5),
            ("volume_ratio_reversal_10_60", "volume_ratio_reversal", 60, 10),
            ("volume_ratio_momentum_5_20", "volume_ratio_momentum", 20, 5),
            ("volume_ratio_momentum_10_60", "volume_ratio_momentum", 60, 10),
        ]
    )
    definitions.extend((f"liquid_{w}", "liquid", w, 0) for w in (20, 60))
    definitions.extend((f"illiquid_{w}", "illiquid", w, 0) for w in (20, 60))
    definitions.extend((f"low_turnover_{w}", "low_turnover", w, 0) for w in (20, 60))
    definitions.extend((f"high_turnover_{w}", "high_turnover", w, 0) for w in (20, 60))
    definitions.extend(
        [
            ("acceleration_reversal_5_60", "acceleration_reversal", 60, 5),
            ("acceleration_momentum_5_60", "acceleration_momentum", 60, 5),
        ]
    )
    definitions.extend(
        (f"signed_amount_flow_{w}", "signed_amount_flow", w, 0)
        for w in (5, 10, 20, 60)
    )
    definitions.extend(
        (f"signed_amount_flow_reversal_{w}", "signed_amount_flow_reversal", w, 0)
        for w in (5, 10, 20)
    )
    definitions.extend(
        (f"residual_momentum_{w}", "residual_momentum", w, 0)
        for w in (20, 60, 120)
    )
    definitions.extend(
        (f"residual_reversal_{w}", "residual_reversal", w, 0)
        for w in (5, 10, 20)
    )
    definitions.extend(
        (f"overnight_gap_reversal_{w}", "overnight_gap_reversal", w, 0)
        for w in (5, 20)
    )
    definitions.extend(
        (f"overnight_gap_momentum_{w}", "overnight_gap_momentum", w, 0)
        for w in (5, 20)
    )
    definitions.extend(
        (f"intraday_reversal_{w}", "intraday_reversal", w, 0)
        for w in (5, 20)
    )
    definitions.extend(
        (f"intraday_momentum_{w}", "intraday_momentum", w, 0)
        for w in (5, 20)
    )
    return [
        CandidateSpec(f"{name}_h{hold}", family, window, skip, hold)
        for name, family, window, skip in definitions
        for hold in holds
    ]


def _institutional_surface(family: str) -> str:
    if family in {"low_vol", "low_downside_vol", "avoid_max_return"}:
        return "risk_defensive"
    if family in {
        "liquid", "illiquid", "low_turnover", "high_turnover",
        "volume_ratio_reversal", "volume_ratio_momentum",
    }:
        return "liquidity_turnover"
    if family in {"signed_amount_flow", "signed_amount_flow_reversal"}:
        return "capital_flow_proxy"
    if family.startswith(("overnight_gap", "intraday")):
        return "event_price_proxy"
    return "price_trend_residual"


def _requested_surface_coverage() -> dict[str, dict[str, str]]:
    return {
        "value": {
            "status": "BLOCKED_MISSING_HISTORICAL_PIT",
            "reason": "base.dbf is a 2026 current snapshot; no historical valuation knowledge times",
        },
        "quality": {
            "status": "BLOCKED_MISSING_HISTORICAL_PIT",
            "reason": "no versioned PIT balance-sheet/cash-flow/profitability history",
        },
        "earnings_improvement": {
            "status": "BLOCKED_MISSING_HISTORICAL_PIT",
            "reason": "no report period, announcement time, revision lineage, or forecast history",
        },
        "event_driven": {
            "status": "PARTIAL_PRICE_PROXY_ONLY",
            "reason": "overnight/intraday price events are searchable; corporate-event knowledge time is not admitted",
        },
        "industry_neutral": {
            "status": "BLOCKED_MISSING_EFFECTIVE_DATE_HISTORY",
            "reason": "current industry configuration has no historical effective_from/effective_to ledger",
        },
        "capital_flow": {
            "status": "PARTIAL_SIGNED_AMOUNT_PROXY_ONLY",
            "reason": "daily amount is present; investor class, order size, northbound, and margin flows are absent",
        },
        "price_trend_residual": {"status": "SEARCHABLE", "reason": "close-t OHLCV only"},
        "risk_defensive": {"status": "SEARCHABLE", "reason": "close-t OHLCV only"},
        "liquidity_turnover": {"status": "SEARCHABLE", "reason": "source-reported daily amount and volume"},
    }


def _split_periods(periods: Sequence[RebalancePeriod], split: str) -> list[RebalancePeriod]:
    if split == "discovery":
        return [period for period in periods if period.signal_date <= DISCOVERY_END]
    if split == "selection":
        return [
            period
            for period in periods
            if SELECTION_START <= period.signal_date <= SELECTION_END
        ]
    if split == "confirmation":
        return [period for period in periods if period.signal_date >= CONFIRMATION_START]
    raise ValueError(f"unknown split: {split}")


def _phase_evaluation(
    panel: MarketPanel,
    scores: np.ndarray,
    spec: CandidateSpec,
    *,
    split: str,
    basket_size: int,
    cost_bps: float,
    benchmark_scores: np.ndarray,
    benchmark_cache: dict[tuple[int, str, int], SimulationResult],
) -> dict:
    phase_rows: list[dict] = []
    score_eligible = panel.base_eligible & np.isfinite(scores)
    for phase in range(spec.hold_days):
        periods = _split_periods(
            make_rebalance_periods(panel.dates, spec.hold_days, phase), split
        )
        strategy = simulate_long_only(
            scores,
            panel.opens,
            score_eligible,
            periods,
            basket_size=basket_size,
            cost_bps=cost_bps,
            bad_bars=panel.bad_bars,
        )
        benchmark_key = (spec.hold_days, split, phase)
        benchmark = benchmark_cache.get(benchmark_key)
        if benchmark is None:
            benchmark = simulate_long_only(
                benchmark_scores,
                panel.opens,
                panel.base_eligible,
                periods,
                basket_size=len(panel.instruments),
                cost_bps=0.0,
                bad_bars=panel.bad_bars,
            )
            benchmark_cache[benchmark_key] = benchmark
        excess = [
            strategy_return - benchmark_return
            for strategy_return, benchmark_return in zip(
                strategy.period_returns, benchmark.period_returns
            )
        ]
        phase_rows.append(
            {
                "phase": phase,
                "strategy": _metric(strategy.period_returns, 252.0 / spec.hold_days),
                "benchmark_gross": _metric(
                    benchmark.period_returns, 252.0 / spec.hold_days
                ),
                "excess": _metric(excess, 252.0 / spec.hold_days),
                "average_turnover": (
                    float(statistics.fmean(strategy.turnovers))
                    if strategy.turnovers
                    else None
                ),
                "unpriced_legs": strategy.unpriced_legs,
            }
        )
    sharpe_values = [
        row["excess"]["annualized_sharpe"]
        for row in phase_rows
        if row["excess"]["annualized_sharpe"] is not None
    ]
    return {
        "primary_phase": phase_rows[0] if phase_rows else None,
        "phase_robustness": {
            "n_phases": len(phase_rows),
            "median_excess_sharpe": (
                float(statistics.median(sharpe_values)) if sharpe_values else None
            ),
            "min_excess_sharpe": min(sharpe_values) if sharpe_values else None,
            "max_excess_sharpe": max(sharpe_values) if sharpe_values else None,
            "positive_phase_fraction": (
                sum(value > 0 for value in sharpe_values) / len(sharpe_values)
                if sharpe_values
                else None
            ),
        },
        "phases": phase_rows,
    }


def run_search(
    db_path: Path,
    *,
    basket_size: int = 20,
    selection_cost_bps: float = 30.0,
    min_adv_cny: float = 20_000_000.0,
    panel_cache: Path | None = None,
) -> dict:
    panel, source = _load_panel(
        db_path,
        min_adv_cny=min_adv_cny,
        panel_cache=panel_cache,
    )
    specs = _candidate_specs((5, 10, 20))
    factor_cache: dict[tuple[str, int, int], np.ndarray] = {}
    benchmark_scores = np.zeros(panel.closes.shape, dtype=np.int8)
    benchmark_cache: dict[tuple[int, str, int], SimulationResult] = {}
    rows: list[dict] = []
    for spec in specs:
        if spec.factor_key not in factor_cache:
            factor_cache[spec.factor_key] = build_factor_matrix(
                spec, panel.closes, panel.volumes, panel.amounts, panel.opens
            )
        scores = factor_cache[spec.factor_key]
        discovery = _phase_evaluation(
            panel,
            scores,
            spec,
            split="discovery",
            basket_size=basket_size,
            cost_bps=selection_cost_bps,
            benchmark_scores=benchmark_scores,
            benchmark_cache=benchmark_cache,
        )
        selection = _phase_evaluation(
            panel,
            scores,
            spec,
            split="selection",
            basket_size=basket_size,
            cost_bps=selection_cost_bps,
            benchmark_scores=benchmark_scores,
            benchmark_cache=benchmark_cache,
        )
        discovery_sharpe = discovery["phase_robustness"]["median_excess_sharpe"]
        selection_sharpe = selection["phase_robustness"]["median_excess_sharpe"]
        stable_positive = bool(
            discovery_sharpe is not None
            and selection_sharpe is not None
            and discovery_sharpe > 0
            and selection_sharpe > 0
        )
        rows.append(
            {
                "candidate": dataclasses.asdict(spec),
                "institutional_surface": _institutional_surface(spec.family),
                "stable_positive_before_confirmation": stable_positive,
                "discovery": discovery,
                "selection": selection,
            }
        )

    def selection_key(row: dict) -> tuple[float, float, str]:
        selection_sharpe = row["selection"]["phase_robustness"]["median_excess_sharpe"]
        discovery_sharpe = row["discovery"]["phase_robustness"]["median_excess_sharpe"]
        return (
            float(selection_sharpe) if selection_sharpe is not None else -math.inf,
            float(discovery_sharpe) if discovery_sharpe is not None else -math.inf,
            row["candidate"]["name"],
        )

    eligible_rows = [row for row in rows if row["stable_positive_before_confirmation"]]
    ranked = sorted(eligible_rows, key=selection_key, reverse=True)
    champion_row = ranked[0] if ranked else max(rows, key=selection_key)
    champion_spec = CandidateSpec(**champion_row["candidate"])
    champion_scores = factor_cache[champion_spec.factor_key]
    confirmation_by_cost = {
        str(int(cost_bps)): _phase_evaluation(
            panel,
            champion_scores,
            champion_spec,
            split="confirmation",
            basket_size=basket_size,
            cost_bps=cost_bps,
            benchmark_scores=benchmark_scores,
            benchmark_cache=benchmark_cache,
        )
        for cost_bps in (15.0, 30.0, 50.0)
    }
    primary_excess = confirmation_by_cost["30"]["primary_phase"]["excess"]
    raw_p = primary_excess["one_sided_p"]
    adjusted_p = min(1.0, raw_p * len(specs)) if raw_p is not None else None
    positive_all_costs = all(
        result["phase_robustness"]["median_excess_sharpe"] is not None
        and result["phase_robustness"]["median_excess_sharpe"] > 0
        for result in confirmation_by_cost.values()
    )
    statistical_pass = bool(
        adjusted_p is not None
        and adjusted_p < 0.05
        and primary_excess["mean_return"] is not None
        and primary_excess["mean_return"] > 0
    )
    candidate_usefulness = (
        "CONTINUE_RESEARCH" if positive_all_costs and statistical_pass else "NOT_VALIDATED"
    )

    surface_confirmations: dict[str, dict] = {}
    for surface in sorted({row["institutional_surface"] for row in rows}):
        surface_rows = [row for row in rows if row["institutional_surface"] == surface]
        stable_rows = [row for row in surface_rows if row["stable_positive_before_confirmation"]]
        selected = max(stable_rows or surface_rows, key=selection_key)
        selected_spec = CandidateSpec(**selected["candidate"])
        if selected_spec == champion_spec:
            by_cost = confirmation_by_cost
        else:
            selected_scores = factor_cache[selected_spec.factor_key]
            by_cost = {
                str(int(cost_bps)): _phase_evaluation(
                    panel,
                    selected_scores,
                    selected_spec,
                    split="confirmation",
                    basket_size=basket_size,
                    cost_bps=cost_bps,
                    benchmark_scores=benchmark_scores,
                    benchmark_cache=benchmark_cache,
                )
                for cost_bps in (15.0, 30.0, 50.0)
            }
        surface_primary = by_cost["30"]["primary_phase"]["excess"]
        surface_raw_p = surface_primary["one_sided_p"]
        surface_adjusted_p = (
            min(1.0, surface_raw_p * len(specs)) if surface_raw_p is not None else None
        )
        surface_positive_costs = all(
            result["phase_robustness"]["median_excess_sharpe"] is not None
            and result["phase_robustness"]["median_excess_sharpe"] > 0
            for result in by_cost.values()
        )
        surface_pass = bool(
            surface_adjusted_p is not None
            and surface_adjusted_p < 0.05
            and surface_primary["mean_return"] is not None
            and surface_primary["mean_return"] > 0
        )
        surface_confirmations[surface] = {
            "candidate_selected_without_confirmation": selected["candidate"],
            "stable_positive_before_confirmation": selected[
                "stable_positive_before_confirmation"
            ],
            "confirmation_cost_sensitivity": by_cost,
            "bonferroni_adjusted_p": surface_adjusted_p,
            "candidate_usefulness": (
                "CONTINUE_RESEARCH"
                if surface_positive_costs and surface_pass
                else "NOT_VALIDATED"
            ),
        }

    top_preconfirmation = []
    for row in sorted(rows, key=selection_key, reverse=True)[:10]:
        top_preconfirmation.append(
            {
                "candidate": row["candidate"],
                "stable_positive_before_confirmation": row[
                    "stable_positive_before_confirmation"
                ],
                "discovery_phase_robustness": row["discovery"]["phase_robustness"],
                "selection_phase_robustness": row["selection"]["phase_robustness"],
                "selection_primary_phase": row["selection"]["primary_phase"],
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "ASHARE_ALPHA_SEARCH_INSTITUTIONAL_SURFACES_V2",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "evidence_status": TECHNICAL_STATUS,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "candidate_usefulness": candidate_usefulness,
        "source": source,
        "requested_surface_coverage": _requested_surface_coverage(),
        "research_contract": {
            "read_only_source": True,
            "signal_time": "close_t",
            "entry_time": "open_t_plus_1",
            "exit_time": "next_rebalance_open",
            "non_overlapping_periods": True,
            "splits": {
                "discovery": f"<= {DISCOVERY_END}",
                "selection": f"{SELECTION_START}..{SELECTION_END}",
                "confirmation": f">= {CONFIRMATION_START}",
            },
            "selection_uses_confirmation": False,
            "basket_size": basket_size,
            "selection_cost_bps_per_turnover_unit": selection_cost_bps,
            "minimum_trailing_adv20_cny": min_adv_cny,
            "bad_bar_rule": "board-band jump; 250-session eligibility cooldown",
            "benchmark": "gross equal-weight eligible survivor universe",
            "phase_rule": "rank on median excess Sharpe over every rebalance phase",
        },
        "trial_accounting": {
            "candidate_specs": len(specs),
            "factor_definitions": len(factor_cache),
            "holding_periods": [5, 10, 20],
            "multiple_testing_method": "Bonferroni",
            "prior_historical_trials_known_complete": False,
            "conservative_promotion_effect": "FAIL_CLOSED",
        },
        "champion_selected_without_confirmation": champion_row["candidate"],
        "champion_preconfirmation": {
            "stable_positive": champion_row["stable_positive_before_confirmation"],
            "discovery": champion_row["discovery"],
            "selection": champion_row["selection"],
        },
        "champion_confirmation_cost_sensitivity": confirmation_by_cost,
        "surface_confirmations": surface_confirmations,
        "confirmation_multiple_testing": {
            "primary_phase_cost_bps": 30,
            "raw_one_sided_p": raw_p,
            "bonferroni_trials": len(specs),
            "bonferroni_adjusted_p": adjusted_p,
            "passes_5pct": statistical_pass,
        },
        "top_preconfirmation_candidates": top_preconfirmation,
        "untouched_forward_observation": {
            "status": "ARMED_AWAITING_NEW_SESSIONS",
            "freeze_cutoff": int(panel.dates[-1]),
            "starts_after": int(panel.dates[-1]),
            "minimum_sessions": 60,
            "candidate_specs": [
                value["candidate_selected_without_confirmation"]
                for value in surface_confirmations.values()
            ],
            "selection_locked": True,
            "promotion_allowed": False,
            "refresh_dependency": "NEW_POST_CUTOFF_DAILY_BARS_WITH_IMMUTABLE_APPEND_AUDIT",
        },
        "limitations": [
            "The source is a current-master survivor cross-section; delisted and historical ST membership are not proven complete.",
            "TongDaXin daily prices are raw/unadjusted; corporate-action records lack admitted knowledge-time semantics, so board-band breaks trigger a 250-session quarantine.",
            "No point-in-time industry, free-float market-cap, ST, corporate-event, or limit-lock state is admitted.",
            "A missing entry or exit open is frozen at zero return and is not replaced using future information; this is conservative but not a full suspension ledger.",
            "The 2025+ split is retrospective confirmation and overlaps earlier research knowledge; only post-freeze observations can become newly untouched evidence.",
            "Historical trial count before this artifact is not provably complete, so promotion remains fail-closed even if this batch passes its local correction.",
            "This engine is a technical research diagnostic and is not an independently admitted authoritative backtest record.",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="immutable stocks.db path")
    parser.add_argument("--output", required=True, type=Path, help="JSON evidence output")
    parser.add_argument("--basket-size", type=int, default=20)
    parser.add_argument("--selection-cost-bps", type=float, default=30.0)
    parser.add_argument("--min-adv-cny", type=float, default=20_000_000.0)
    parser.add_argument(
        "--panel-cache",
        type=Path,
        help="optional hash-bound NPZ sidecar for repeatable fast panel loading",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.db.is_file():
        raise SystemExit(f"database not found: {args.db}")
    if args.basket_size <= 0:
        raise SystemExit("basket-size must be positive")
    report = run_search(
        args.db,
        basket_size=args.basket_size,
        selection_cost_bps=args.selection_cost_bps,
        min_adv_cny=args.min_adv_cny,
        panel_cache=args.panel_cache,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    confirmation = report["champion_confirmation_cost_sensitivity"]["30"]
    primary = confirmation["primary_phase"]
    print(
        "ASHARE_ALPHA_SEARCH_COMPLETE "
        f"status={report['evidence_status']} "
        f"candidate={report['champion_selected_without_confirmation']['name']} "
        f"trials={report['trial_accounting']['candidate_specs']} "
        f"excess_sharpe={primary['excess']['annualized_sharpe']} "
        f"adjusted_p={report['confirmation_multiple_testing']['bonferroni_adjusted_p']} "
        f"usefulness={report['candidate_usefulness']}"
    )
    print(f"evidence={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
