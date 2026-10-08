"""Pre-registered Binance USD-M perpetual/quarterly basis pressure test.

The network edge uses only public mark-price history.  Exact perpetual funding
and decision marks come from the already-hash-bound public funding artifact.
This L2 research tool has no account, credential, adapter, intent, promotion,
or order capability.
"""
from __future__ import annotations

import argparse
import calendar
import math
import re
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quantos.contracts.cost import CostModel
from quantos.contracts.hashing import stable_hash
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE
from services.research.calendar_basis import (
    CalendarBasisPeriod,
    QuarterlyMark,
    build_calendar_basis_periods,
    run_calendar_basis_backtest,
)
from services.research.funding_settlement import (
    HOUR_MS,
    MONDAY_UTC_OFFSET_MS,
    WEEK_MS,
)
from tools.binance_funding_settlement_fetch import (
    _default_http_get,
    fetch_mark_hours,
)
from tools.cache_io import atomic_write_json
from tools.funding_settlement_evaluation import load_history


DEFAULT_SOURCE = Path("data/cache/binance_funding_settlement_history.json")
DEFAULT_OUTPUT = Path("data/cache/calendar_basis_research.json")
UNDERLYINGS = ("BTCUSDT", "ETHUSDT")
MODES = ("basis_convergence", "basis_divergence_control")
BASE_COST = CostModel(fee_bps=4.0, slippage_bps=2.0, impact_coef=0.0)
DOUBLE_COST = CostModel(fee_bps=8.0, slippage_bps=4.0, impact_coef=0.0)
PERIODS_PER_YEAR = 52
MIN_PERIODS = 104
MIN_PRESSURE_SHARPE = 2.70
_COMMIT = re.compile(r"[0-9a-f]{40}")


def _last_friday(year: int, month: int) -> int:
    day = calendar.monthrange(year, month)[1]
    while datetime(year, month, day, tzinfo=timezone.utc).weekday() != 4:
        day -= 1
    return day


def quarter_delivery_times(start_ms: int, end_ms: int) -> tuple[int, ...]:
    """Return quarter deliveries after start through the first one after end."""
    if (
        isinstance(start_ms, bool)
        or isinstance(end_ms, bool)
        or not isinstance(start_ms, int)
        or not isinstance(end_ms, int)
        or start_ms < 0
        or end_ms <= start_ms
    ):
        raise ValueError("start_ms/end_ms are invalid")
    start_year = datetime.fromtimestamp(start_ms / 1000, timezone.utc).year
    end_year = datetime.fromtimestamp(end_ms / 1000, timezone.utc).year
    result: list[int] = []
    found_after_end = False
    for year in range(start_year, end_year + 2):
        for month in (3, 6, 9, 12):
            delivery = int(
                datetime(
                    year,
                    month,
                    _last_friday(year, month),
                    8,
                    tzinfo=timezone.utc,
                ).timestamp()
                * 1000
            )
            if delivery <= start_ms:
                continue
            result.append(delivery)
            if delivery > end_ms:
                found_after_end = True
                break
        if found_after_end:
            break
    return tuple(result)


def _contract_id(underlying: str, delivery_time_ms: int) -> str:
    suffix = datetime.fromtimestamp(
        delivery_time_ms / 1000, timezone.utc
    ).strftime("%y%m%d")
    return f"{underlying}_{suffix}"


def fetch_quarterly_history(
    *,
    start_ms: int,
    end_ms: int,
    underlyings: Sequence[str] = UNDERLYINGS,
    delivery_times: Sequence[int] | None = None,
    http_get: Callable[[str], object] = _default_http_get,
    archive_root: str | Path | None = DEFAULT_RAW_ARCHIVE,
    observed_at_ms: int | None = None,
    max_workers: int = 4,
) -> dict[str, list[QuarterlyMark]]:
    """Fetch completed Monday 1h marks for deterministic quarterly symbols."""
    if end_ms <= start_ms:
        raise ValueError("end_ms must be after start_ms")
    observed_at = int(
        observed_at_ms
        if observed_at_ms is not None
        else time.time_ns() // 1_000_000
    )
    selected_underlyings = tuple(sorted({str(item).upper() for item in underlyings}))
    if not selected_underlyings or any(
        item not in UNDERLYINGS for item in selected_underlyings
    ):
        raise ValueError("only the pre-registered BTCUSDT/ETHUSDT universe is allowed")
    deliveries = tuple(
        int(item)
        for item in (
            delivery_times
            if delivery_times is not None
            else quarter_delivery_times(start_ms, end_ms)
        )
    )
    if not deliveries or any(
        current <= start_ms
        or (index > 0 and current <= deliveries[index - 1])
        for index, current in enumerate(deliveries)
    ):
        raise ValueError("delivery_times must be strictly increasing after start_ms")

    requests = tuple(
        (underlying, delivery, _contract_id(underlying, delivery))
        for delivery in deliveries
        for underlying in selected_underlyings
    )

    def one(item: tuple[str, int, str]) -> tuple[str, list[QuarterlyMark]]:
        underlying, delivery, contract = item
        fetch_end = min(int(end_ms), delivery - HOUR_MS)
        if fetch_end < start_ms:
            return contract, []
        marks = fetch_mark_hours(
            contract,
            int(start_ms),
            fetch_end,
            http_get=http_get,
            archive_root=archive_root,
            observed_at_ms=observed_at,
        )
        rows = [
            QuarterlyMark(
                contract_id=contract,
                underlying_id=underlying,
                delivery_time_ms=delivery,
                decision_time_ms=open_time + HOUR_MS,
                decision_mark_price=close_mark,
            )
            for open_time, (_close_time, close_mark) in sorted(marks.items())
            if (open_time - MONDAY_UTC_OFFSET_MS) % WEEK_MS == 0
        ]
        return contract, rows

    result: dict[str, list[QuarterlyMark]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), 4))) as pool:
        for contract, rows in pool.map(one, requests):
            if rows:
                result[contract] = rows
    return dict(sorted(result.items()))


def _sharpe(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    sigma = statistics.pstdev(values)
    return (
        0.0
        if sigma == 0.0
        else statistics.fmean(values) / sigma * math.sqrt(PERIODS_PER_YEAR)
    )


def _cumulative(values: Sequence[float]) -> float:
    return math.prod(1.0 + value for value in values) - 1.0


def _fold_returns(values: Sequence[float], folds: int = 4) -> tuple[float, ...]:
    return tuple(
        _cumulative(
            values[
                (index * len(values)) // folds : ((index + 1) * len(values)) // folds
            ]
        )
        for index in range(folds)
    )


def _metrics(periods: Sequence[CalendarBasisPeriod], cost: CostModel) -> dict[str, Any]:
    replay = run_calendar_basis_backtest(periods, cost=cost)
    return {
        "n_periods": len(replay.period_returns),
        "net_sharpe": _sharpe(replay.period_returns),
        "net_return": _cumulative(replay.period_returns),
        "fold_returns": list(_fold_returns(replay.period_returns)),
        "mean_turnover": (
            statistics.fmean(replay.turnovers) if replay.turnovers else 0.0
        ),
        "total_cost": sum(replay.costs),
        "reconciliation_residual": replay.reconciliation_residual(),
    }


def evaluate_pressure(
    periods_by_mode: Mapping[str, Sequence[CalendarBasisPeriod]],
) -> dict[str, Any]:
    if tuple(sorted(periods_by_mode)) != tuple(sorted(MODES)):
        raise ValueError("pressure population must be the frozen two-member family")
    pressure: dict[str, dict[str, Any]] = {}
    for mode in MODES:
        periods = tuple(periods_by_mode[mode])
        pressure[mode] = {
            "1.0x": _metrics(periods, BASE_COST),
            "2.0x": _metrics(periods, DOUBLE_COST),
        }
    candidate = pressure["basis_convergence"]
    control = pressure["basis_divergence_control"]
    hurdles = {
        "minimum_104_periods": candidate["1.0x"]["n_periods"] >= MIN_PERIODS,
        "base_sharpe_above_2_70": candidate["1.0x"]["net_sharpe"] > MIN_PRESSURE_SHARPE,
        "twice_cost_return_positive": candidate["2.0x"]["net_return"] > 0.0,
        "four_folds_positive": all(
            value > 0.0 for value in candidate["1.0x"]["fold_returns"]
        ),
        "beats_divergence_control": (
            candidate["1.0x"]["net_return"] > control["1.0x"]["net_return"]
        ),
    }
    return {
        "passes_all": all(hurdles.values()),
        "hurdles": hurdles,
        "pressure": pressure,
    }


def _quarterly_payload(
    history: Mapping[str, Sequence[QuarterlyMark]],
) -> dict[str, list[dict[str, int | float | str]]]:
    return {
        contract: [
            {
                "contract_id": row.contract_id,
                "underlying_id": row.underlying_id,
                "delivery_time_ms": row.delivery_time_ms,
                "decision_time_ms": row.decision_time_ms,
                "decision_mark_price": row.decision_mark_price,
            }
            for row in rows
        ]
        for contract, rows in sorted(history.items())
    }


def run(
    *,
    source_snapshot: str | Path = DEFAULT_SOURCE,
    output: str | Path = DEFAULT_OUTPUT,
    source_commit: str,
    recorded_at_ms: int | None = None,
    archive_root: str | Path | None = DEFAULT_RAW_ARCHIVE,
    http_get: Callable[[str], object] = _default_http_get,
    max_workers: int = 4,
) -> dict[str, Any]:
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character git commit")
    recorded_at = int(
        recorded_at_ms
        if recorded_at_ms is not None
        else time.time_ns() // 1_000_000
    )
    history, funding_metadata = load_history(source_snapshot)
    perpetual = {underlying: history[underlying] for underlying in UNDERLYINGS}
    start_ms = max(rows[0].settlement_time_ms for rows in perpetual.values())
    end_ms = min(rows[-1].settlement_time_ms for rows in perpetual.values())
    end_ms = min(end_ms, recorded_at - HOUR_MS)
    quarterly = fetch_quarterly_history(
        start_ms=start_ms,
        end_ms=end_ms,
        underlyings=UNDERLYINGS,
        http_get=http_get,
        archive_root=archive_root,
        observed_at_ms=recorded_at,
        max_workers=max_workers,
    )
    periods_by_mode = {
        mode: build_calendar_basis_periods(
            perpetual,
            quarterly,
            mode=mode,
            min_underlyings=len(UNDERLYINGS),
        )
        for mode in MODES
    }
    pressure = evaluate_pressure(periods_by_mode)
    quarterly_payload = _quarterly_payload(quarterly)
    dataset_hash = stable_hash(
        {
            "funding_dataset_hash": funding_metadata["dataset_hash"],
            "quarterly_marks": quarterly_payload,
        }
    )
    payload = {
        "schema_version": "calendar_basis_research.v1",
        "provenance": "REAL_BINANCE_PUBLIC_USDM_MARK_AND_FUNDING_HISTORY",
        "recorded_at_ms": recorded_at,
        "source_commit": source_commit,
        "status": (
            "PRESSURE_PASS_FORMAL_EVALUATOR_REQUIRED"
            if pressure["passes_all"]
            else "FALSIFIED"
        ),
        "eligible_for_formal_evaluator": pressure["passes_all"],
        "formally_admitted": False,
        "testnet_intent_allowed": False,
        "family": list(MODES),
        "universe": list(UNDERLYINGS),
        "funding_source": funding_metadata,
        "quarterly_contract_count": len(quarterly),
        "quarterly_rows_by_contract": {
            contract: len(rows) for contract, rows in quarterly.items()
        },
        "dataset_hash": dataset_hash,
        **pressure,
        "safety": {
            "l2_research_only": True,
            "public_market_data_only": True,
            "credentials": False,
            "account_access": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
            "testnet_execution_started": False,
        },
    }
    atomic_write_json(output, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--archive-root", default=str(DEFAULT_RAW_ARCHIVE))
    args = parser.parse_args()
    payload = run(
        source_snapshot=args.source,
        output=args.output,
        source_commit=args.source_commit,
        archive_root=args.archive_root,
    )
    print(payload["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "evaluate_pressure",
    "fetch_quarterly_history",
    "quarter_delivery_times",
    "run",
]
