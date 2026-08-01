"""Run the QUANTOS three-alpha robust optimisation study.

The command is intentionally an operator tool, not a service import.  It loads
the existing PIT alpha panels, requires a real CUDA device for the exhaustive
screen, records every candidate in the durable experiment registry, and only
then permits the existing CPU dry-run pipeline to replay a stable candidate.
There is no CPU fallback for the GPU stage and no live-order path.

Typical invocation::

    python -m tools.crypto_robust_optimization

The default output is ``evidence/crypto_robust_optimization.json`` plus a
newline-delimited complete matrix under ``data/cache``.  Both are L2 paper
evidence and remain ``LIVE_BLOCKED_L2``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from typing import Any

from services.research.robust_optimization import (
    ALPHA_IDS,
    CandidateSpec,
    DD_THRESHOLDS,
    MatrixMetrics,
    StressSpec,
    aggregate_stress,
    candidate_config_hash,
    iter_candidate_specs,
    iter_stress_specs,
    select_stable_regions,
    strict_backtest_gate,
)
from services.research.trial_provenance import trial_provenance_for_config


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DEFAULT_REPORT = os.path.join(ROOT, "evidence", "crypto_robust_optimization.json")
DEFAULT_CELLS = os.path.join(ROOT, "data", "cache", "crypto_robust_optimization_cells.jsonl")
DEFAULT_REGISTRY = os.path.join(ROOT, "data", "cache", "experiment_registry.db")
DEFAULT_AUM = 1_000_000.0


class GpuUnavailable(RuntimeError):
    """CUDA is not available; callers must not silently run a CPU screen."""


@dataclass
class AlphaSchedule:
    alpha_id: str
    periods: list[int]
    weights_by_period: dict[int, dict[str, float]]
    prices_by_period: dict[int, dict[str, float]]
    rate_by_period: dict[int, dict[str, float | None]]
    adv_by_period: dict[int, dict[str, float]]
    crowding: dict[str, Any]


def _json_load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _dataset_version() -> tuple[str, dict[str, str]]:
    paths = {
        "crypto_funding_db": os.path.join(ROOT, "data", "cache", "crypto_funding.db"),
        "crypto_funding_rates": os.path.join(ROOT, "data", "cache", "crypto_funding_rates.json"),
        "crypto_funding_snapshot": os.path.join(ROOT, "data", "cache", "crypto_funding_snapshot.json"),
        "crypto_oi_metrics": os.path.join(ROOT, "data", "cache", "crypto_oi_metrics.json"),
        "macro_fred_db": os.path.join(ROOT, "data", "cache", "macro_fred.db"),
    }
    hashes: dict[str, str] = {}
    for name, path in paths.items():
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        hashes[name] = _sha256_file(path)
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16], hashes


def _weekly_rows(event_times: list[int]) -> list[int]:
    week_ms = 7 * 24 * 3600 * 1000
    rows: list[int] = []
    last: int | None = None
    for row, timestamp in enumerate(event_times):
        if last is None or int(timestamp) - last >= week_ms:
            rows.append(row)
            last = int(timestamp)
    return rows


def _week_key(timestamp: int) -> tuple[int, int]:
    """Calendar ISO week key; venue/data-source sampling days may differ."""
    date = _dt.datetime.fromtimestamp(int(timestamp) / 1000, tz=_dt.timezone.utc).date()
    iso = date.isocalendar()
    return int(iso.year), int(iso.week)


def _prepare_carry() -> AlphaSchedule:
    from tools import dry_run_carry_authoritative_multi as module
    from services.data.caching_store import CachingPITStore
    from services.data.persistent_store import SqlitePITStore

    prepared = module._prepare()
    periods, weights, prices, rates, adv, _volume, _cost, _n_liquid = prepared
    # The legacy driver only stored current-leg prices.  The robust study needs
    # marks for previously held names as well; reload the same PIT store and
    # attach those real observations rather than carrying a synthetic mark.
    raw_store = SqlitePITStore(module._DB, create=False)
    store = CachingPITStore(raw_store)
    try:
        names = sorted({name for target in weights.values() for name in target})
        bars = {
            name: {int(bar.event_time): bar for bar in store.fetch_bars_pit(name, -10**18, 10**18)}
            for name in names
        }
        funding = {
            name: {int(timestamp): value for timestamp, value in values.items()}
            for name, values in json.load(open(module._FUND, encoding="utf-8")).items()
        }
        expanded_prices: dict[int, dict[str, float]] = {}
        expanded_rates: dict[int, dict[str, float | None]] = {}
        expanded_adv: dict[int, dict[str, float]] = {}
        for timestamp in periods:
            expanded_prices[timestamp] = {name: bars[name][timestamp].close for name in names if timestamp in bars[name] and bars[name][timestamp].close > 0}
            expanded_rates[timestamp] = {name: funding.get(name, {}).get(timestamp) for name in names}
            expanded_adv[timestamp] = {name: bars[name][timestamp].close * bars[name][timestamp].volume for name in names if timestamp in bars[name] and bars[name][timestamp].close > 0 and bars[name][timestamp].volume > 0}
        prices, rates, adv = expanded_prices, expanded_rates, expanded_adv
    finally:
        raw_store._conn.close()
    return AlphaSchedule("carry_oi_majors", periods, weights, prices, rates, adv, {})


def _prepare_oi_alpha(
    panel,
    alpha_id: str,
    expression,
    *,
    quantile: float | None = None,
) -> AlphaSchedule:
    """Build one positioning sleeve from the existing PIT OI panel."""
    from live.execution.crowding_score import crowding_zscore
    from services.portfolio.crowding_gate import evaluate_crowding_gate, summarize_gate_decisions
    from services.research import alpha_expression as ax
    from tools.crypto_oi_research import QUANTILE, _legs

    selected_quantile = QUANTILE if quantile is None else float(quantile)
    if not math.isfinite(selected_quantile) or not 0.0 < selected_quantile <= 0.5:
        raise ValueError("quantile must be finite and in (0, 0.5]")

    instruments = panel.instruments
    close = panel.matrices["close"]
    volume = panel.matrices["volume"]
    funding = panel.matrices["funding"]
    event_times = panel.event_times
    signal = ax.evaluate(expression, panel)
    global_ls = panel.matrices.get("global_ls")
    top_ls = panel.matrices.get("top_ls")
    rows = _weekly_rows(event_times)
    periods: list[int] = []
    weights_by_period: dict[int, dict[str, float]] = {}
    prices_by_period: dict[int, dict[str, float]] = {}
    rate_by_period: dict[int, dict[str, float | None]] = {}
    adv_by_period: dict[int, dict[str, float]] = {}
    gate_decisions = []
    previous: dict[str, float] | None = None
    for row in rows:
        legs = _legs(signal[row], selected_quantile)
        if not legs:
            continue
        timestamp = int(event_times[row])
        target = {instruments[index]: weight for index, weight in legs.items()}
        blocked = False
        if alpha_id == "crowd_contra" and global_ls is not None:
            crowd = {}
            for index in legs:
                values = [global_ls[rr][index] for rr in range(max(0, row - 3), row + 1) if global_ls[rr][index] is not None]
                if values:
                    crowd[instruments[index]] = sum(values) / len(values)
            ordered = sorted(crowd, key=crowd.get)
            rank = {name: (i / (len(ordered) - 1) if len(ordered) > 1 else 0.5) for i, name in enumerate(ordered)}
            z_map = {instruments[index]: value for index in legs if (value := crowding_zscore(global_ls, row, index)) is not None}
            decision = evaluate_crowding_gate(target, rank, average_warn_only=True, crowding_z_by_symbol=z_map, max_crowding_z=3.0)
            gate_decisions.append(decision)
            blocked = getattr(decision, "status", "") == "BLOCK"
        elif alpha_id == "top_contra" and top_ls is not None:
            # Keep the gate explicit for the top-trader sleeve too.  The
            # positioning panel is real; a missing top_ls row is a data block.
            crowd = {instruments[index]: top_ls[row][index] for index in legs if top_ls[row][index] is not None}
            if crowd:
                ordered = sorted(crowd, key=crowd.get)
                rank = {name: (i / (len(ordered) - 1) if len(ordered) > 1 else 0.5) for i, name in enumerate(ordered)}
                decision = evaluate_crowding_gate(target, rank, average_warn_only=True)
                gate_decisions.append(decision)
                blocked = getattr(decision, "status", "") == "BLOCK"
        if blocked and previous is not None:
            target = dict(previous)
        previous = dict(target)
        names = set(target)
        if blocked and previous:
            names |= set(previous)
        prices = {instruments[index]: close[row][index] for index in range(len(instruments)) if close[row][index] and close[row][index] > 0}
        rates = {instruments[index]: funding[row][index] for index in range(len(instruments))}
        adv = {instruments[index]: close[row][index] * volume[row][index] for index in range(len(instruments)) if close[row][index] and volume[row][index] and close[row][index] > 0 and volume[row][index] > 0}
        periods.append(timestamp)
        weights_by_period[timestamp] = target
        prices_by_period[timestamp] = prices
        rate_by_period[timestamp] = rates
        adv_by_period[timestamp] = adv
    return AlphaSchedule(alpha_id, periods, weights_by_period, prices_by_period, rate_by_period, adv_by_period, summarize_gate_decisions(gate_decisions) if gate_decisions else {})


def _prepare_top_from_existing(panel) -> AlphaSchedule:
    """Use the authoritative top-contra selector, then attach real ADV rows."""
    from services.portfolio.crowding_gate import summarize_gate_decisions
    from tools.top_contra_dry_run_diagnostic import _prepare

    periods, weights, prices, rates, gate_decisions = _prepare(panel)
    rows = {int(timestamp): row for row, timestamp in enumerate(panel.event_times)}
    adv: dict[int, dict[str, float]] = {}
    for timestamp in periods:
        row = rows.get(int(timestamp))
        if row is None:
            continue
        adv[timestamp] = {
            instrument: panel.matrices["close"][row][index] * panel.matrices["volume"][row][index]
            for index, instrument in enumerate(panel.instruments)
            if panel.matrices["close"][row][index]
            and panel.matrices["volume"][row][index]
            and panel.matrices["close"][row][index] > 0
            and panel.matrices["volume"][row][index] > 0
        }
        prices[timestamp] = {
            instrument: panel.matrices["close"][row][index]
            for index, instrument in enumerate(panel.instruments)
            if panel.matrices["close"][row][index] and panel.matrices["close"][row][index] > 0
        }
        rates[timestamp] = {
            instrument: panel.matrices["funding"][row][index]
            for index, instrument in enumerate(panel.instruments)
        }
    return AlphaSchedule("top_contra", periods, weights, prices, rates, adv, summarize_gate_decisions(gate_decisions))


def load_alpha_schedules() -> tuple[dict[str, AlphaSchedule], dict[str, Any]]:
    from tools.crypto_oi_research import build_panel
    from tools.crypto_oi_verify import CROWD_CONTRA

    carry = _prepare_carry()
    panel, _instruments = build_panel()
    crowd = _prepare_oi_alpha(panel, "crowd_contra", CROWD_CONTRA)
    top = _prepare_top_from_existing(panel)
    schedules = {item.alpha_id: item for item in (carry, crowd, top)}
    common = set.intersection(*({ _week_key(timestamp) for timestamp in item.periods } for item in schedules.values()))
    if len(common) < 16:
        raise ValueError(f"fewer than 16 common PIT weeks: {len(common)}")
    return schedules, {"common_periods": len(common), "period_start": min(common), "period_end": max(common), "alignment": "ISO_WEEK_PIT"}


def _schedule_returns(schedule: AlphaSchedule, periods: list[int]) -> tuple[float, ...]:
    result: list[float] = []
    for current, following in zip(periods, periods[1:]):
        weights = schedule.weights_by_period[current]
        p0 = schedule.prices_by_period[current]
        p1 = schedule.prices_by_period[following]
        rates = schedule.rate_by_period[following]
        value = 0.0
        for instrument, weight in weights.items():
            if instrument not in p0 or instrument not in p1 or instrument not in rates:
                raise ValueError(f"{schedule.alpha_id} missing PIT price/funding at {current}->{following}:{instrument}")
            if rates[instrument] is None:
                raise ValueError(f"{schedule.alpha_id} missing funding at {following}:{instrument}")
            value += float(weight) * (float(p1[instrument]) / float(p0[instrument]) - 1.0)
            value -= float(weight) * float(rates[instrument])
        result.append(value)
    return tuple(result)


def _schedule_return_map(schedule: AlphaSchedule, periods: list[int]) -> tuple[dict[tuple[int, int], float], tuple[tuple[int, int], ...]]:
    """Return only fully observed PIT intervals, preserving missing-input evidence."""
    result: dict[tuple[int, int], float] = {}
    missing: list[tuple[int, int]] = []
    for current, following in zip(periods, periods[1:]):
        key = _week_key(current)
        weights = schedule.weights_by_period[current]
        p0 = schedule.prices_by_period[current]
        p1 = schedule.prices_by_period[following]
        rates = schedule.rate_by_period[following]
        value = 0.0
        complete = True
        for instrument, weight in weights.items():
            if instrument not in p0 or instrument not in p1 or instrument not in rates or rates[instrument] is None:
                complete = False
                break
            value += float(weight) * (float(p1[instrument]) / float(p0[instrument]) - 1.0)
            value -= float(weight) * float(rates[instrument])
        if complete:
            result[key] = value
        else:
            missing.append(key)
    return result, tuple(missing)


def _build_bundle(schedules: dict[str, AlphaSchedule]) -> dict[str, Any]:
    by_week: dict[str, dict[tuple[int, int], int]] = {
        alpha: {_week_key(timestamp): int(timestamp) for timestamp in schedule.periods}
        for alpha, schedule in schedules.items()
    }
    common_weeks = sorted(set.intersection(*(set(index) for index in by_week.values())))
    if len(common_weeks) < 16:
        raise ValueError("not enough common PIT periods")
    alpha_timestamps = {alpha: [by_week[alpha][week] for week in common_weeks] for alpha in ALPHA_IDS}
    # Each sleeve keeps its own PIT observation timestamp.  The resulting
    # return vectors are then aligned by ISO week, not by an artificial exact
    # timestamp intersection (carry and OI snapshots are one day apart).
    return_maps: dict[str, dict[tuple[int, int], float]] = {}
    missing_intervals: dict[str, list[tuple[int, int]]] = {}
    for alpha in ALPHA_IDS:
        return_maps[alpha], missing = _schedule_return_map(schedules[alpha], alpha_timestamps[alpha])
        missing_intervals[alpha] = list(missing)
    valid_weeks = sorted(set.intersection(*(set(return_maps[alpha]) for alpha in ALPHA_IDS)))
    if len(valid_weeks) < 16:
        raise ValueError("not enough complete PIT return intervals")
    common_weeks = valid_weeks
    alpha_timestamps = {alpha: [by_week[alpha][week] for week in common_weeks] for alpha in ALPHA_IDS}
    alpha_returns = {alpha: tuple(return_maps[alpha][week] for week in common_weeks) for alpha in ALPHA_IDS}
    common = [max(alpha_timestamps[alpha][index] for alpha in ALPHA_IDS) for index in range(len(common_weeks))]
    turnover: list[float] = []
    previous: dict[str, float] = {}
    for index, week in enumerate(common_weeks):
        current: dict[str, float] = {}
        for alpha in ALPHA_IDS:
            timestamp = alpha_timestamps[alpha][index]
            for instrument, weight in schedules[alpha].weights_by_period[timestamp].items():
                current[instrument] = current.get(instrument, 0.0) + float(weight) / 3.0
        turnover.append(sum(abs(current.get(name, 0.0) - previous.get(name, 0.0)) for name in set(current) | set(previous)))
        previous = current
    # Return-map keys are the observed current rebalances whose following mark
    # is present.  Keep one turnover observation per such interval; the final
    # weekly point is not a synthetic extra observation.
    if any(not math.isfinite(value) for series in alpha_returns.values() for value in series):
        raise ValueError("non-finite alpha return")
    prices_by_period: dict[int, dict[str, float]] = {}
    weights_by_period: dict[int, dict[str, float]] = {}
    rates_by_period: dict[int, dict[str, float | None]] = {}
    adv_by_period: dict[int, dict[str, float]] = {}
    sleeve_weights_by_period: dict[int, dict[str, dict[str, float]]] = {}
    for index, week in enumerate(common_weeks):
        logical_timestamp = common[index]
        prices: dict[str, float] = {}
        weights: dict[str, float] = {}
        rates: dict[str, float | None] = {}
        adv: dict[str, float] = {}
        sleeve_weights_by_period[logical_timestamp] = {}
        for alpha in ALPHA_IDS:
            timestamp = alpha_timestamps[alpha][index]
            sleeve_weights_by_period[logical_timestamp][alpha] = dict(schedules[alpha].weights_by_period[timestamp])
            for instrument, value in schedules[alpha].weights_by_period[timestamp].items():
                weights[instrument] = weights.get(instrument, 0.0) + float(value) / 3.0
            prices.update(schedules[alpha].prices_by_period[timestamp])
            rates.update(schedules[alpha].rate_by_period[timestamp])
            adv.update(schedules[alpha].adv_by_period.get(timestamp, {}))
        prices_by_period[logical_timestamp] = prices
        weights_by_period[logical_timestamp] = weights
        rates_by_period[logical_timestamp] = rates
        adv_by_period[logical_timestamp] = adv
    state_labels = _load_pit_vix_labels(common)
    # Complete risk inputs are intentionally not fabricated.  The actual alpha
    # evaluation metadata is supplied by the promotion/evaluator artifact, not
    # inferred from a backtest return.
    full_research_risk = {
        "capacity_cny": {name: max(adv, 0.0) for values in adv_by_period.values() for name, adv in values.items()},
        "daily_volume_cny": max((max(values.values(), default=0.0) for values in adv_by_period.values()), default=0.0),
        "total_aum": DEFAULT_AUM,
        "alpha_returns": alpha_returns,
        "regime_labels": state_labels,
        "alpha_metadata": None,
    }
    return {
        "periods": common,
        "alpha_returns": alpha_returns,
        "turnover": turnover,
        "prices_by_period": prices_by_period,
        "weights_by_period": weights_by_period,
        "rates_by_period": rates_by_period,
        "adv_by_period": adv_by_period,
        "sleeve_weights_by_period": sleeve_weights_by_period,
        "state_labels": state_labels,
        "full_research_risk": full_research_risk,
        "missing_intervals": missing_intervals,
    }


def _load_pit_vix_labels(periods: list[int]) -> list[str | None]:
    """Load VIX labels using only observations knowable at each week."""
    import sqlite3
    from services.research.macro_regime import expanding_tercile_labels

    path = os.path.join(ROOT, "data", "cache", "macro_fred.db")
    if not os.path.isfile(path):
        return [None] * len(periods)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT feature_id,event_time,knowledge_time,value FROM fundamentals "
            "WHERE (feature_id LIKE '%VIX%' OR feature_id='VIX') ORDER BY event_time,knowledge_time"
        ).fetchall()
    finally:
        conn.close()
    observations = [(int(event), int(knowledge), float(value)) for _feature, event, knowledge, value in rows if value is not None and math.isfinite(float(value))]
    values: list[float | None] = []
    for timestamp in periods:
        date = int(_dt.datetime.fromtimestamp(int(timestamp) / 1000, tz=_dt.timezone.utc).strftime("%Y%m%d"))
        knowable = [(event, knowledge, value) for event, knowledge, value in observations if knowledge <= date and event <= date]
        values.append(max(knowable, key=lambda item: (item[0], item[1]))[2] if knowable else None)
    labels = expanding_tercile_labels(values, min_history=10)
    return [str(value) if value is not None else None for value in labels]


def _require_cuda():
    try:
        import cupy as cp
        count = int(cp.cuda.runtime.getDeviceCount())
        if count <= 0:
            raise GpuUnavailable("CUDA device count is zero")
        device = cp.cuda.Device()
        device.use()
        props = cp.cuda.runtime.getDeviceProperties(device.id)
        name = props.get("name", b"CUDA") if isinstance(props, dict) else "CUDA"
        if isinstance(name, bytes):
            name = name.decode(errors="replace")
        return cp, {"device_id": int(device.id), "device_name": str(name), "device_count": count, "cupy": getattr(cp, "__version__", "unknown")}
    except GpuUnavailable:
        raise
    except Exception as exc:
        raise GpuUnavailable(f"CUDA unavailable: {type(exc).__name__}: {exc}") from exc


def _gpu_screen(bundle: dict[str, Any], *, candidates=None, stresses=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cp, gpu = _require_cuda()
    alpha_returns = bundle["alpha_returns"]
    turnover = bundle["turnover"]
    states = bundle["state_labels"]
    if len(states) != len(turnover) or any(label is None for label in states):
        raise ValueError("PIT market-state labels are incomplete")
    states = [str(label) for label in states]
    candidates = tuple(candidates or iter_candidate_specs())
    stresses = tuple(stresses or iter_stress_specs())
    values = cp.asarray([alpha_returns[alpha] for alpha in ALPHA_IDS], dtype=cp.float64).T
    turnover_gpu = cp.asarray(turnover, dtype=cp.float64)
    weight_gpu = cp.asarray([candidate.weights for candidate in candidates], dtype=cp.float64)
    leverage_gpu = cp.asarray([candidate.leverage_cap for candidate in candidates], dtype=cp.float64)
    base = cp.dot(weight_gpu, values.T) * leverage_gpu[:, None]
    n = int(values.shape[0])
    target_per = 0.10 / math.sqrt(52.0)
    state_names = tuple(dict.fromkeys(states))
    state_id = cp.asarray([state_names.index(label) for label in states], dtype=cp.int32)
    cells: list[dict[str, Any]] = []
    for stress_index, stress in enumerate(stresses):
        impact_scale = math.sqrt(stress.participation_rate / 0.10) / math.sqrt(stress.execution_days)
        cost = turnover_gpu * (stress.fee_bps / 10_000.0 + stress.impact_bps / 10_000.0 * impact_scale)
        net = base - cost[None, :]
        vt = cp.empty_like(net)
        for index in range(n):
            if index < 8:
                vt[:, index] = net[:, index]
            else:
                sd = cp.std(net[:, index - 8:index], axis=1)
                scale = cp.minimum(leverage_gpu, target_per / cp.maximum(sd, 1e-9))
                vt[:, index] = scale * net[:, index]
        equity = cp.cumprod(1.0 + net, axis=1)
        vt_equity = cp.cumprod(1.0 + vt, axis=1)
        # CuPy builds in this environment do not implement ``ufunc.accumulate``
        # for maximum; a deterministic device-side recurrence is equivalent.
        peak = cp.empty_like(equity)
        vt_peak = cp.empty_like(vt_equity)
        peak[:, 0] = equity[:, 0]
        vt_peak[:, 0] = vt_equity[:, 0]
        for index in range(1, n):
            peak[:, index] = cp.maximum(peak[:, index - 1], equity[:, index])
            vt_peak[:, index] = cp.maximum(vt_peak[:, index - 1], vt_equity[:, index])
        dd = cp.max((peak - equity) / cp.maximum(peak, 1e-12), axis=1)
        vt_dd = cp.max((vt_peak - vt_equity) / cp.maximum(vt_peak, 1e-12), axis=1)
        mean = cp.mean(net, axis=1)
        sd = cp.std(net, axis=1)
        vt_mean = cp.mean(vt, axis=1)
        vt_sd = cp.std(vt, axis=1)
        sharpe = mean / cp.maximum(sd, 1e-12) * math.sqrt(52.0)
        vt_sharpe = vt_mean / cp.maximum(vt_sd, 1e-12) * math.sqrt(52.0)
        net_return = cp.prod(1.0 + net, axis=1) - 1.0
        vt_return = cp.prod(1.0 + vt, axis=1) - 1.0
        annualized_vt = cp.power(cp.maximum(1.0 + vt_return, 1e-12), 52.0 / max(n, 1)) - 1.0
        calmar = annualized_vt / cp.maximum(vt_dd, 1e-12)
        state_means = cp.stack(
            [cp.sum(net * (state_id == state)[None, :], axis=1) / max(int(cp.sum(state_id == state).item()), 1)
             for state in range(len(state_names))],
            axis=1,
        )
        worst_state_idx = cp.argmin(state_means, axis=1)
        state_positive = cp.mean(state_means > 0.0, axis=1)
        arrays = [cp.asnumpy(array) for array in (net_return, vt_return, sharpe, vt_sharpe, dd, vt_dd, calmar, state_positive, worst_state_idx)]
        fee_drag = sum(turnover) * stress.fee_bps / 10_000.0
        impact_drag = sum(turnover) * stress.impact_bps / 10_000.0 * impact_scale
        for candidate_index, (ret, vret, sr, vsr, maxdd, vdd, calmar_value, state_fraction, worst_index) in enumerate(zip(*arrays)):
            candidate = candidates[candidate_index]
            cells.append({
                "candidate_index": candidate_index,
                "candidate_hash": candidate_config_hash(candidate),
                "stress_index": stress_index,
                "stress": stress.as_dict(),
                "metrics": {
                    "net_return": float(ret), "vt_return": float(vret), "annualized_sharpe": float(sr),
                    "vt_sharpe": float(vsr), "max_drawdown": float(maxdd), "vt_max_drawdown": float(vdd),
                    "calmar": float(calmar_value),
                    "turnover": float(sum(turnover)), "fee_drag": float(fee_drag), "impact_drag": float(impact_drag),
                    "market_state_profitable_fraction": float(state_fraction),
                    "worst_state": state_names[int(worst_index)], "positive": bool(vret > 0.0), "n_periods": n,
                },
            })
    gpu["candidate_count"] = len(candidates)
    gpu["stress_count"] = len(stresses)
    gpu["cell_count"] = len(cells)
    gpu["kernel"] = "cupy_float64_vectorized_candidate_stress"
    return gpu, cells


def _metrics_from_cell(cell: dict[str, Any]) -> MatrixMetrics:
    metrics = cell["metrics"]
    return MatrixMetrics(
        net_return=metrics["net_return"], vt_return=metrics["vt_return"], annualized_sharpe=metrics["annualized_sharpe"],
        vt_sharpe=metrics["vt_sharpe"], max_drawdown=metrics["max_drawdown"], vt_max_drawdown=metrics["vt_max_drawdown"],
        calmar=metrics.get("calmar", 0.0), turnover=metrics["turnover"], fee_drag=metrics["fee_drag"], impact_drag=metrics["impact_drag"],
        worst_state=metrics.get("worst_state", ""), positive=metrics.get("positive", False), n_periods=metrics.get("n_periods", 0),
    )


def _aggregate_candidates(cells: list[dict[str, Any]], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = iter_candidate_specs()
    by_hash: dict[str, list[MatrixMetrics]] = {candidate_config_hash(candidate): [] for candidate in candidates}
    for cell in cells:
        by_hash[cell["candidate_hash"]].append(_metrics_from_cell(cell))
    # Existing artifacts do not contain the full evaluator-level DSR/PBO/CPCV
    # evidence for these frozen sleeves.  Do not infer it from Sharpe or treat
    # the old four-engine dry-run decision as a substitute.
    statistics_evidence = {
        "dsr": None, "pbo": None, "oos_vt_sharpe": None,
        "walk_forward_profitable_fraction": None, "market_state_profitable_fraction": None,
        "provenance": "MISSING_FROZEN_ALPHA_EVALUATION_BINDING",
    }
    full_inputs = bundle["full_research_risk"]
    full_available = bool(
        not any(bundle.get("missing_intervals", {}).values())
        and
        full_inputs.get("capacity_cny")
        and full_inputs.get("daily_volume_cny", 0.0) > 0
        and full_inputs.get("regime_labels")
        and all(label is not None for label in full_inputs["regime_labels"])
        and full_inputs.get("alpha_metadata")
    )
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        digest = candidate_config_hash(candidate)
        stress = aggregate_stress(by_hash[digest])
        market_fraction = statistics.fmean(
            cell["metrics"]["market_state_profitable_fraction"]
            for cell in cells
            if cell["candidate_hash"] == digest
        ) if cells else None
        gate = strict_backtest_gate(
            candidate=candidate, data_ok=full_available, dsr=statistics_evidence["dsr"],
            pbo=statistics_evidence["pbo"], oos_vt_sharpe=statistics_evidence["oos_vt_sharpe"],
            walk_forward_profitable_fraction=statistics_evidence["walk_forward_profitable_fraction"],
            market_state_profitable_fraction=market_fraction if market_fraction is not None else statistics_evidence["market_state_profitable_fraction"],
            stress=stress, full_research_risk=full_available,
        )
        records.append({
            "candidate": candidate.as_dict(), "candidate_hash": digest, "stress": stress,
            "gate": gate.as_dict(), "research_statistics": statistics_evidence,
            "full_research_risk_available": full_available,
        })
    return records


def _register_trials(
    candidates: list[CandidateSpec], dataset_version: str, registry_path: str
) -> dict[str, Any]:
    from services.research.experiment_registry.registry import ExperimentRegistry
    from services.research.experiment_registry.store import SqliteExperimentStore

    if not os.path.isfile(registry_path):
        raise FileNotFoundError(f"global experiment registry missing: {registry_path}")
    store = SqliteExperimentStore(registry_path, create=False)
    search_id = f"crypto-robust-{int(time.time() * 1000)}-{hashlib.sha256(dataset_version.encode()).hexdigest()[:8]}"
    started = int(time.time() * 1000)
    registry = ExperimentRegistry(store)
    try:
        before = store.trial_count()
        config_hashes = [candidate_config_hash(candidate) for candidate in candidates]
        registry.begin_search_attempt(search_id=search_id, search_kind="ROBUST_CRYPTO_GRID", source="tools.crypto_robust_optimization", dataset_version=dataset_version, recorded_at=started)
        inserted = registry.register_search_candidates(search_id, config_hashes, registered_at=started)
        for candidate in candidates:
            registry.record_trial_provenance(
                trial_provenance_for_config(
                    candidate_config_hash(candidate),
                    research_session_id=search_id,
                    actor_id="tools.crypto_robust_optimization",
                    dataset_version=dataset_version,
                    recorded_at=started,
                    factor_ids=ALPHA_IDS,
                    parameter_set={
                        "candidate": json.dumps(
                            candidate.as_dict(),
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=True,
                        )
                    },
                    model_name="deterministic-robust-grid",
                )
            )
        after = store.trial_count()
        registry.finish_search_attempt(search_id, status="COMPLETED", recorded_at=max(started, int(time.time() * 1000)))
        return {"status": "PASS", "search_id": search_id, "before": before, "registered": inserted, "after": after, "config_hashes": len(config_hashes), "persistent": True}
    except Exception:
        try:
            registry.finish_search_attempt(search_id, status="FAILED", recorded_at=max(started, int(time.time() * 1000)))
        except Exception:
            pass
        raise
    finally:
        store.close()


def _forward_status() -> dict[str, Any]:
    path = os.path.join(ROOT, "data", "cache", "forward_track_status.json")
    if not os.path.isfile(path):
        return {"status": "MISSING", "valid_periods": 0, "data_fresh": False, "positive_net": False, "ledger_consistent": False}
    snapshot = _json_load(path)
    alphas = snapshot.get("alphas") if isinstance(snapshot.get("alphas"), dict) else {}
    periods = [int((value or {}).get("n_forward_periods", 0)) for value in alphas.values() if isinstance(value, dict)]
    return {
        "status": "AVAILABLE", "valid_periods": min(periods) if periods else 0,
        "data_fresh": bool(snapshot.get("quality_evidence_current")),
        "positive_net": all((value or {}).get("cum_return") is not None and float((value or {}).get("cum_return")) > 0 for value in alphas.values() if isinstance(value, dict)) if alphas else False,
        "ledger_consistent": bool(snapshot.get("refresh_evidence_available")),
        "snapshot_path": path,
        "alphas": alphas,
    }


def _cpu_replay_candidate(candidate: CandidateSpec, stress: StressSpec, bundle: dict[str, Any], *, filters: dict[str, tuple[float, float, float]] | None = None) -> dict[str, Any]:
    """Replay one GPU-approved cell through the real integer-lot paper seam.

    The function is deliberately useful to tests and future operators even
    though the current local data snapshot has incomplete frozen-alpha
    evaluator metadata and exchange filters.  Either omission returns an
    explicit BLOCK before any fill is simulated.
    """
    full = bundle.get("full_research_risk") or {}
    required = ("capacity_cny", "daily_volume_cny", "total_aum", "alpha_returns", "regime_labels", "alpha_metadata")
    missing = [name for name in required if full.get(name) in (None, {}, [])]
    if missing:
        return {"status": "BLOCK", "reasons": ["full_research_risk_inputs_missing:" + ",".join(missing)], "candidate_hash": candidate_config_hash(candidate), "stress": stress.as_dict()}
    if filters is None:
        return {"status": "BLOCK", "reasons": ["exchange_filters_missing"], "candidate_hash": candidate_config_hash(candidate), "stress": stress.as_dict()}
    periods = list(bundle.get("periods") or [])
    if len(periods) < 2:
        return {"status": "BLOCK", "reasons": ["cpu_periods_missing"], "candidate_hash": candidate_config_hash(candidate), "stress": stress.as_dict()}
    from services.execution.dry_run_pipeline import run_multi_period_dry_run
    from services.execution.impact_calibration import default_crypto_impact_calibration, scenario_impact
    from services.risk.pre_trade.config import RiskLimits
    from services.risk.pre_trade.context import AccountRiskState, MarketContext, RiskContext, StrategyApproval
    from services.risk.pre_trade.gate import PreTradeRiskGate
    from services.risk.pre_trade.kill_switch import KillSwitch
    from services.risk.research_risk.dry_run_guard import DryRunResearchRiskInputs
    from services.risk.research_risk.domain.models import RiskConfig
    from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine

    weights_by_period: dict[int, dict[str, float]] = {}
    for timestamp in periods:
        target: dict[str, float] = {}
        for index, alpha in enumerate(ALPHA_IDS):
            for instrument, weight in (bundle["sleeve_weights_by_period"].get(timestamp, {}).get(alpha, {}) or {}).items():
                target[instrument] = target.get(instrument, 0.0) + candidate.weights[index] * float(weight)
        weights_by_period[timestamp] = target
    prices_by_period = bundle["prices_by_period"]
    rates_by_period = bundle["rates_by_period"]
    adv_by_period = bundle["adv_by_period"]
    calibration = default_crypto_impact_calibration()
    impact_scenarios = {5.0: calibration.aggressive, 10.0: calibration.base, 20.0: calibration.conservative}
    impact_model = scenario_impact(impact_scenarios.get(float(stress.impact_bps), calibration.base))
    # Spreading an order across N days increases the available ADV by N; the
    # impact model then charges the resulting participation rate.  The
    # pre-trade participation limit remains the independent hard gate.
    execution_adv_by_period = {
        timestamp: {name: float(value) * int(stress.execution_days) for name, value in values.items()}
        for timestamp, values in adv_by_period.items()
    }
    held_names = sorted({name for target in weights_by_period.values() for name in target})
    missing_inputs = []
    for timestamp, target in weights_by_period.items():
        for instrument in target:
            if instrument not in prices_by_period.get(timestamp, {}) or instrument not in rates_by_period.get(timestamp, {}) or rates_by_period.get(timestamp, {}).get(instrument) is None:
                missing_inputs.append(f"{timestamp}:{instrument}")
    if missing_inputs:
        return {"status": "BLOCK", "reasons": ["cpu_pit_marks_or_funding_missing"], "missing": missing_inputs[:20], "candidate_hash": candidate_config_hash(candidate), "stress": stress.as_dict()}

    kill_switch = KillSwitch()
    limits = RiskLimits(
        allowed_instruments=frozenset(held_names), allowed_accounts=frozenset({"paper"}),
        default_max_position_qty=10**13, max_order_notional=1e14, fat_finger_qty=10**13,
        max_gross_exposure=1e14, max_net_exposure=1e14,
        max_leverage=float(candidate.leverage_cap), max_single_name_pct=1.0,
        max_turnover=1e7, max_participation_rate=float(stress.participation_rate),
        max_instrument_vol=5.0, daily_loss_limit=1e14, max_drawdown=float(candidate.dd_threshold),
    )
    gate = PreTradeRiskGate(limits, kill_switch=kill_switch)

    def ctx_factory(intent, prices, period):
        reference = prices.get(intent.instrument_id)
        if reference is None or reference <= 0:
            raise ValueError("missing CPU reference price")
        return RiskContext(
            account=AccountRiskState(account_id="paper", cash=DEFAULT_AUM * 10, equity=DEFAULT_AUM * 10, day_pnl=0.0, peak_equity=DEFAULT_AUM * 10, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=True, reference_price=reference, market_open=True, price_as_of=period, est_daily_volume=adv_by_period.get(period, {}).get(intent.instrument_id, 0.0), instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id="crypto_robust_optimization", approved=True, approval_id="research-paper"), now=period, max_staleness=10**15,
        )

    def cash_flow_fn(held, prices, period):
        rates = rates_by_period.get(period, {})
        return sum(-float(rates.get(name, 0.0) or 0.0) * quantity * prices.get(name, 0.0) for name, quantity in held.items())

    research_inputs = DryRunResearchRiskInputs(
        capacity_cny=full["capacity_cny"], daily_volume_cny=full["daily_volume_cny"], total_aum=full["total_aum"],
        alpha_returns=full["alpha_returns"], regime_labels=full["regime_labels"], alpha_metadata=full["alpha_metadata"],
    )
    risk_config = RiskConfig(periods_per_year=52, max_drawdown=float(candidate.dd_threshold), max_leverage=float(candidate.leverage_cap), max_participation_rate=float(stress.participation_rate))
    try:
        run = run_multi_period_dry_run(
            periods=periods, weights_by_period=weights_by_period, prices_by_period=prices_by_period,
            gate=gate, ctx_factory=ctx_factory, adapter=DryRunAdapterEngine(), strategy_id="crypto_robust_optimization",
            aum=DEFAULT_AUM, cost_bps=stress.fee_bps, cash_flow_fn=cash_flow_fn,
            vol_target={"target_ann": 0.10, "lookback": 8, "lev_cap": candidate.leverage_cap, "ppy": 52},
            impact=impact_model, filters=filters, adv_by_period=execution_adv_by_period, research_risk_inputs=research_inputs,
            research_risk_config=risk_config, require_full_research_risk=True, publish_completion_event=False,
        )
    except Exception as exc:
        return {"status": "BLOCK", "reasons": [f"cpu_pipeline_error:{type(exc).__name__}"], "error": str(exc)[:240], "candidate_hash": candidate_config_hash(candidate), "stress": stress.as_dict()}
    risk = run.detail.get("research_risk") if isinstance(run.detail, dict) else {}
    residual = float(run.pnl.get("residual", math.inf))
    reasons: list[str] = []
    if abs(residual) > 1e-6:
        reasons.append("cpu_reconciliation_residual")
    if int(run.pnl.get("total_rejected", 0)) > 0:
        reasons.append("cpu_unexplained_rejections")
    if not isinstance(risk, dict) or risk.get("gates_run") != 9 or risk.get("decision") != "RISK_APPROVED":
        reasons.append("full_research_risk_not_approved")
    return {
        "status": "PASS" if not reasons else "BLOCK", "candidate_hash": candidate_config_hash(candidate), "stress": stress.as_dict(),
        "run_id": run.run_id, "residual": residual, "rejected": int(run.pnl.get("total_rejected", 0)),
        "kill_switch_anomaly": False, "audit_root": run.audit_root, "risk": risk, "reasons": reasons,
        "complete": not reasons,
    }


def _write_jsonl(path: str, rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def _write_report(path: str, report: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=True, sort_keys=True, indent=2)
    os.replace(temporary, path)


def run(*, report_path: str = DEFAULT_REPORT, cells_path: str = DEFAULT_CELLS, registry_path: str = DEFAULT_REGISTRY, cpu_replay: bool = True) -> dict[str, Any]:
    started = int(time.time() * 1000)
    report: dict[str, Any] = {
        "schema_version": "crypto_robust_optimization.v1",
        "started_at": started, "readiness": "L2_DRY_RUN_ONLY", "live_allowed": False,
        "order_submission_allowed": False, "alpha_ids": list(ALPHA_IDS),
        "grid": {"candidate_count": len(iter_candidate_specs()), "stress_count": len(iter_stress_specs()), "dd_thresholds": list(DD_THRESHOLDS)},
        "gpu": {"status": "NOT_RUN"}, "matrix": {"status": "NOT_RUN", "cells_path": cells_path},
        "stable_regions": [], "cpu_replay": [], "forward": _forward_status(),
        "final_classification": "REJECTED", "execution_state": "LIVE_BLOCKED_L2",
    }
    try:
        dataset_version, file_hashes = _dataset_version()
        report["dataset"] = {"version": dataset_version, "files_sha256": file_hashes, "pit": True, "synthetic_defaults": False, "cadence": "weekly", "ppy": 52}
        schedules, schedule_meta = load_alpha_schedules()
        bundle = _build_bundle(schedules)
        report["schedule"] = schedule_meta | {
            "periods": len(bundle["periods"]),
            "state_labels_complete": all(label is not None for label in bundle["state_labels"]),
            "missing_intervals": {alpha: len(values) for alpha, values in bundle.get("missing_intervals", {}).items()},
        }
        report["research_risk_input_status"] = {
            "full_nine_engine": False,
            "missing": ["alpha_evaluation_metadata", "complete_pit_price_funding_intervals", "exchange_filters"],
            "provenance": "REAL_INPUTS_ONLY_FAIL_CLOSED",
        }
        registry = _register_trials(list(iter_candidate_specs()), dataset_version, registry_path)
        report["experiment_registry"] = registry
        gpu, cells = _gpu_screen(bundle)
        report["gpu"] = {"status": "PASS", **gpu}
        cell_provenance = {
            "gpu_device": gpu.get("device_name"),
            "gpu_device_id": gpu.get("device_id"),
            "dataset_version": dataset_version,
            "experiment_search_id": registry.get("search_id"),
            "global_trial_count": registry.get("after"),
        }
        for cell in cells:
            cell["provenance"] = cell_provenance
        _write_jsonl(cells_path, cells)
        report["matrix"] = {
            "status": "PASS", "cells_path": cells_path, "cell_count": len(cells),
            "bytes": os.path.getsize(cells_path), "sha256": _sha256_file(cells_path),
        }
        records = _aggregate_candidates(cells, bundle)
        stable = select_stable_regions(records)
        report["candidates"] = records
        report["stable_regions"] = [region.as_dict() for region in stable]
        # No candidate reaches CPU unless every strict historical gate passes.
        # When it does, replay every stable-region member and every pressure
        # cell; this is deliberately not a top-k shortcut.
        if cpu_replay and stable:
            records_by_hash = {row["candidate_hash"]: row for row in records}
            for region in stable:
                for digest in region.candidate_hashes:
                    row = records_by_hash[digest]
                    value = row["candidate"]
                    candidate = CandidateSpec(tuple(value["weights"]), value["leverage_cap"], value["dd_threshold"])
                    for stress in iter_stress_specs():
                        report["cpu_replay"].append(_cpu_replay_candidate(candidate, stress, bundle))
        cpu_pass = bool(report["cpu_replay"]) and all(item.get("status") == "PASS" for item in report["cpu_replay"])
        if cpu_pass:
            # The forward ledger is an independent evidence stream.  The
            # current snapshot has zero valid weeks, so this remains pending.
            forward = report["forward"]
            report["final_classification"] = "PAPER_READY_FOR_REVIEW" if int(forward.get("valid_periods", 0)) >= 8 else "PAPER_ONLY_PENDING_FORWARD"
        else:
            report["final_classification"] = "REJECTED"
    except GpuUnavailable as exc:
        report["gpu"] = {"status": "GPU_UNAVAILABLE", "error": str(exc), "cpu_fallback": False}
        report["final_classification"] = "REJECTED"
    except Exception as exc:
        report["status"] = "ERROR"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
        report["final_classification"] = "REJECTED"
    report["finished_at"] = int(time.time() * 1000)
    report["report_hash"] = hashlib.sha256(json.dumps(report, sort_keys=True, default=str).encode()).hexdigest()
    _write_report(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--cells", default=DEFAULT_CELLS)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--no-cpu", action="store_true", help="do not enter CPU replay even for a stable region")
    args = parser.parse_args(argv)
    report = run(report_path=args.report, cells_path=args.cells, registry_path=args.registry, cpu_replay=not args.no_cpu)
    print(json.dumps({"final_classification": report.get("final_classification"), "gpu": report.get("gpu"), "matrix": report.get("matrix"), "report": args.report}, ensure_ascii=False))
    return 0 if report.get("gpu", {}).get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
