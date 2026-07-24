"""Thin adapter: feed equity_alpha signal matrices into services/research/ic_panel.py.

Reuses the shipped, tested IC machinery (compute_period_ic_series for 1-day,
compute_horizon_ic_series for the non-overlapping 5-day horizon,
aggregate_ic_series for mean/ICIR/t-stat/hit-rate). The only new logic here is
the TRAIN/TEST split (strict no-peek: train <= TRAIN_END, test >= TEST_START,
decided by the SIGNAL date of each IC observation) and the YYYYMMDD -> epoch-ms
conversion that aggregate_ic_series needs for its per_year breakdown (its
gmtime(ms/1000) convention assumes epoch ms; A-share event_times are YYYYMMDD ints).
"""
from __future__ import annotations

import calendar

from services.research import ic_panel

TRAIN_END = 20241231
TEST_START = 20250101


def yyyymmdd_to_epoch_ms(d: int) -> int:
    """Deterministic calendar conversion (no clock): YYYYMMDD int -> UTC epoch ms."""
    y, m, day = d // 10000, (d // 100) % 100, d % 100
    return calendar.timegm((y, m, day, 0, 0, 0)) * 1000


def _ic_series(signal_matrix, price_matrix, horizon: int):
    """(ics, sig_row_indices): IC series plus the SIGNAL row index of each observation."""
    if horizon == 1:
        ics = ic_panel.compute_period_ic_series(signal_matrix, price_matrix)
        return ics, list(range(len(ics)))
    ics, starts = ic_panel.compute_horizon_ic_series(signal_matrix, price_matrix, horizon)
    return ics, starts


def _aggregate_split(ics, sig_rows, event_times):
    """Aggregate one IC series separately on train / test (split by signal date)."""
    out = {}
    for label, lo, hi in (("train", None, TRAIN_END), ("test", TEST_START, None)):
        sel_v, sel_t = [], []
        for v, r in zip(ics, sig_rows):
            et = event_times[r]
            if (lo is None or et >= lo) and (hi is None or et <= hi):
                sel_v.append(v)
                sel_t.append(yyyymmdd_to_epoch_ms(et))
        out[label] = ic_panel.aggregate_ic_series(sel_v, sel_t)
    return out


def evaluate_signal(signal_matrix, price_matrix, event_times, horizons=(1, 5)) -> dict:
    """{f"h{h}": {"train": agg, "test": agg}} for one signal matrix vs forward close returns."""
    res = {}
    for h in horizons:
        ics, rows = _ic_series(signal_matrix, price_matrix, h)
        res[f"h{h}"] = _aggregate_split(ics, rows, event_times)
    return res


def evaluate_all(signal_matrices: dict, price_matrix, event_times, horizons=(1, 5)) -> dict:
    """{factor_name: evaluate_signal(...)} — one entry per factor."""
    return {name: evaluate_signal(m, price_matrix, event_times, horizons)
            for name, m in signal_matrices.items()}
