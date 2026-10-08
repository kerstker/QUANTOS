"""全维度加密 Alpha 研究重测：时间框架 × 组合 × 杠杆 × 成本压力。

本驱动器只消费已经落盘的日频 PIT 价格、资金费率和 OI/持仓比面板，不访问
私有 API、不读取凭证、不提交订单。它把三条冻结 Alpha 在 1d/3d/7d/14d
决策节奏下重建为区间级经济收益流，再统一扫描：

* 单 Alpha、两两等权、三 Alpha 等权、PIT 逆波动组合；
* 0.25x--2.0x 固定杠杆与 vol-target 杠杆上限；
* 0/2/4/6/8/10 bps 手续费（4 bps 为 Binance taker 基准）；
* 5/10/20 bps 冲击、5%/10%/20% 参与率、1/3/5 天执行铺开。

这是研究诊断矩阵，不替代完整九引擎研究风险门、历史盘口/交易所过滤器和
CPU 整手成交复核。1H/1m 如果缺少同覆盖 PIT OI/资金费输入，必须记录为
DATA_UNAVAILABLE，绝不用前向填充或合成默认值伪造结果。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import time
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research import alpha_expression as ax
from services.research.robust_optimization import vol_target_returns
from tools.crypto_oi_research import CARRY, _legs, build_panel


DAY_MS = 86_400_000
DEFAULT_AUM = 1_000_000.0
QUANTILE = 0.30
TIMEFRAMES = OrderedDict((('1d', 1), ('3d', 3), ('7d', 7), ('14d', 14)))
LEVERAGES = (0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00)
FEES_BPS = (0.0, 2.0, 4.0, 6.0, 8.0, 10.0)
IMPACT_BPS = (5.0, 10.0, 20.0)
PARTICIPATION = (0.05, 0.10, 0.20)
EXECUTION_DAYS = (1, 3, 5)
DD_THRESHOLDS = (0.08, 0.10, 0.12, 0.15, 0.20)
BASELINE_CELL = {
    "fee_bps": 4.0,
    "impact_bps": 10.0,
    "participation_rate": 0.10,
    "execution_days": 1,
}

ALPHA_EXPRESSIONS = OrderedDict(
    (
        ("carry_oi_majors", CARRY),
        ("crowd_contra", ("cs_rank", ("neg", ("field", "global_ls")))),
        ("top_contra", ("cs_zscore", ("neg", ("field", "top_ls")))),
    )
)
ALPHA_IDS = tuple(ALPHA_EXPRESSIONS)
PORTFOLIO_MODES = OrderedDict(
    (
        ("carry_only", (1.0, 0.0, 0.0)),
        ("crowd_only", (0.0, 1.0, 0.0)),
        ("top_only", (0.0, 0.0, 1.0)),
        ("carry_crowd_equal", (0.5, 0.5, 0.0)),
        ("carry_top_equal", (0.5, 0.0, 0.5)),
        ("crowd_top_equal", (0.0, 0.5, 0.5)),
        ("all_three_equal", (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)),
        ("inverse_vol_pit", None),
    )
)
DEFAULT_REPORT = "evidence/crypto_multidim_retest_20260725.json"
DEFAULT_CELLS = "data/cache/crypto_multidim_retest_20260725_cells.jsonl"
DEFAULT_MARKDOWN = "docs/reports/crypto-multidim-retest-2026-07-25.md"


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _round(value: float | None, digits: int = 8) -> float | None:
    return None if value is None else round(float(value), digits)


def _day_string(ts: int) -> str:
    return datetime.fromtimestamp(int(ts) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _cadence_rows(event_times: Sequence[int], cadence_days: int) -> list[int]:
    step = int(cadence_days) * DAY_MS
    rows: list[int] = []
    last: int | None = None
    for index, timestamp in enumerate(event_times):
        if last is None or int(timestamp) - last >= step:
            rows.append(index)
            last = int(timestamp)
    return rows


def _ppy(cadence_days: int) -> float:
    return 365.25 / float(cadence_days)


def _stats(returns: Sequence[float], ppy: float) -> dict[str, float | int | None]:
    values = [float(x) for x in returns if _finite(x)]
    if not values:
        return {
            "n_periods": 0,
            "total_return": None,
            "annualized_return": None,
            "sharpe": None,
            "max_drawdown": None,
            "calmar": None,
            "positive_period_fraction": None,
        }
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0.0:
            max_dd = max(max_dd, 1.0 - equity / peak)
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values) if len(values) > 1 else 0.0
    sharpe = mean / sd * math.sqrt(ppy) if sd > 0.0 else None
    annual = equity ** (ppy / len(values)) - 1.0 if equity > 0.0 else -1.0
    return {
        "n_periods": len(values),
        "total_return": equity - 1.0,
        "annualized_return": annual,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": annual / max_dd if max_dd > 0.0 else None,
        "positive_period_fraction": sum(1 for x in values if x > 0.0) / len(values),
    }


def _legs_by_name(signal_row: Sequence[float | None], instruments: Sequence[str]) -> dict[str, float]:
    return {instruments[index]: float(weight) for index, weight in _legs(signal_row, QUANTILE).items()}


def _combine_targets(targets: Sequence[Mapping[str, float]], weights: Sequence[float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for sleeve, sleeve_weight in zip(targets, weights):
        for name, value in sleeve.items():
            out[name] = out.get(name, 0.0) + float(sleeve_weight) * float(value)
    return {name: value for name, value in out.items() if abs(value) > 1e-15}


def _inverse_vol_weights(history: Sequence[Sequence[float]], lookback: int = 8) -> tuple[float, float, float]:
    if len(history) < 2:
        return (1.0 / 3.0,) * 3
    window = history[-int(lookback):]
    vols: list[float] = []
    for index in range(3):
        values = [float(row[index]) for row in window if _finite(row[index])]
        sd = statistics.pstdev(values) if len(values) > 1 else 0.0
        vols.append(sd if sd > 1e-12 else 1e-12)
    inv = [1.0 / value for value in vols]
    total = sum(inv)
    return tuple(value / total for value in inv)  # type: ignore[return-value]


def _economic_return(target: Mapping[str, float], *, close: Sequence[Sequence[float | None]], funding: Sequence[Sequence[float | None]],
                     r0: int, r1: int, index_by_name: Mapping[str, int]) -> tuple[float, float, float]:
    price = 0.0
    funding_pnl = 0.0
    for name, weight in target.items():
        column = index_by_name[name]
        p0 = close[r0][column]
        p1 = close[r1][column]
        if not _finite(p0) or not _finite(p1) or float(p0) <= 0.0:
            raise ValueError(f"missing price for {name} at {r0}->{r1}")
        price += float(weight) * (float(p1) / float(p0) - 1.0)
        for row in range(r0 + 1, r1 + 1):
            rate = funding[row][column]
            if not _finite(rate):
                raise ValueError(f"missing funding for {name} at row {row}")
            # Positive funding is paid by longs; a signed long/short weight
            # therefore receives -weight * rate.
            funding_pnl += -float(weight) * float(rate)
    return price, funding_pnl, price + funding_pnl


def _build_intervals(panel, signals: Mapping[str, Sequence[Sequence[float | None]]], cadence_days: int,
                     required_alpha_ids: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    event_times = panel.event_times
    rows = _cadence_rows(event_times, cadence_days)
    instruments = panel.instruments
    index_by_name = {name: index for index, name in enumerate(instruments)}
    close = panel.matrices["close"]
    funding = panel.matrices["funding"]
    intervals: list[dict[str, Any]] = []
    skipped: defaultdict[str, int] = defaultdict(int)
    for left, right in zip(rows, rows[1:]):
        targets: dict[str, dict[str, float]] = {}
        for alpha_id in required_alpha_ids:
            signal = signals[alpha_id]
            target = _legs_by_name(signal[left], instruments)
            if not target:
                skipped[f"{alpha_id}:thin_signal"] += 1
            targets[alpha_id] = target
        if any(not value for value in targets.values()):
            skipped["all_alpha_alignment"] += 1
            continue
        union = set().union(*(set(value) for value in targets.values()))
        try:
            alpha_returns: dict[str, dict[str, float]] = {}
            for alpha_id, target in targets.items():
                price, funding_pnl, economic = _economic_return(
                    target, close=close, funding=funding, r0=left, r1=right, index_by_name=index_by_name
                )
                alpha_returns[alpha_id] = {
                    "price": price,
                    "funding": funding_pnl,
                    "economic": economic,
                }
            # Verify the union once as well: combo books may net names from
            # different sleeves, but may never silently consume an incomplete input.
            for name in union:
                column = index_by_name[name]
                if not _finite(close[left][column]) or not _finite(close[right][column]):
                    raise ValueError(f"missing price for {name}")
                for row in range(left + 1, right + 1):
                    if not _finite(funding[row][column]):
                        raise ValueError(f"missing funding for {name}")
        except ValueError as exc:
            skipped["input_missing"] += 1
            skipped[str(exc)[:120]] += 1
            continue
        intervals.append(
            {
                "left": left,
                "right": right,
                "start": int(event_times[left]),
                "end": int(event_times[right]),
                "targets": targets,
                "alpha_returns": alpha_returns,
            }
        )
    return intervals, dict(skipped)


def _required_alpha_ids(mode: str) -> tuple[str, ...]:
    weights = PORTFOLIO_MODES[mode]
    if weights is None:
        return ALPHA_IDS
    return tuple(alpha_id for alpha_id, weight in zip(ALPHA_IDS, weights) if float(weight) > 0.0)


def _book_series(intervals: Sequence[Mapping[str, Any]], mode: str) -> dict[str, Any]:
    targets: list[dict[str, float]] = []
    gross: list[float] = []
    sleeve_history: list[tuple[float, float, float]] = []
    weights_history: list[tuple[float, float, float]] = []
    for interval in intervals:
        sleeve_targets = [interval["targets"].get(alpha_id, {}) for alpha_id in ALPHA_IDS]
        sleeve_returns = [float(interval["alpha_returns"].get(alpha_id, {}).get("economic", 0.0)) for alpha_id in ALPHA_IDS]
        if mode == "inverse_vol_pit":
            weights = _inverse_vol_weights(sleeve_history)
        else:
            weights = PORTFOLIO_MODES[mode]
        target = _combine_targets(sleeve_targets, weights)
        targets.append(target)
        gross.append(sum(float(w) * value for w, value in zip(weights, sleeve_returns)))
        weights_history.append(tuple(float(w) for w in weights))
        sleeve_history.append(tuple(sleeve_returns))
    turnover: list[float] = []
    previous: dict[str, float] = {}
    for target in targets:
        names = set(previous) | set(target)
        turnover.append(sum(abs(float(target.get(name, 0.0)) - float(previous.get(name, 0.0))) for name in names))
        previous = target
    if turnover and targets:
        # Include a mark-only final closeout cost in the last observation, as
        # the canonical Binance replay does.
        turnover[-1] += sum(abs(float(value)) for value in targets[-1].values())
    return {
        "targets": targets,
        "gross_returns": gross,
        "turnover": turnover,
        "weights": [list(row) for row in weights_history],
    }


def _cost_rate(fee_bps: float, impact_bps: float, participation_rate: float, execution_days: int) -> tuple[float, float]:
    impact_scale = math.sqrt(float(participation_rate) / 0.10) / math.sqrt(float(execution_days))
    return float(fee_bps) / 10_000.0, float(impact_bps) / 10_000.0 * impact_scale


def _scenario(book: Mapping[str, Any], *, cadence_days: int, leverage: float, fee_bps: float, impact_bps: float,
              participation_rate: float, execution_days: int) -> dict[str, Any]:
    gross = [float(value) for value in book["gross_returns"]]
    turnover = [float(value) for value in book["turnover"]]
    fee_rate, impact_rate = _cost_rate(fee_bps, impact_bps, participation_rate, execution_days)
    unlevered_net = [value - traded * (fee_rate + impact_rate) for value, traded in zip(gross, turnover)]
    fixed = [float(leverage) * value for value in unlevered_net]
    ppy = _ppy(cadence_days)
    vt = list(
        vol_target_returns(
            unlevered_net,
            target_ann=0.10,
            lookback=min(8, max(1, len(unlevered_net) - 1)),
            leverage_cap=float(leverage),
            ppy=ppy,
        )
    )
    return {
        "fixed": _stats(fixed, ppy),
        "vol_target": _stats(vt, ppy),
        "fee_drag": sum(float(leverage) * traded * fee_rate for traded in turnover),
        "impact_drag": sum(float(leverage) * traded * impact_rate for traded in turnover),
        "turnover": sum(turnover),
        "avg_turnover": statistics.fmean(turnover) if turnover else None,
        "returns": fixed,
        "vt_returns": vt,
    }


def _cell_key(frame: str, mode: str, leverage: float, fee: float, impact: float, participation: float, days: int) -> str:
    return f"{frame}|{mode}|L{leverage:g}|F{fee:g}|I{impact:g}|P{participation:g}|H{days}"


def _is_baseline(row: Mapping[str, Any]) -> bool:
    return all(row[key] == value for key, value in BASELINE_CELL.items())


def _load_canonical_weekly() -> dict[str, dict[str, Any]]:
    paths = {
        "carry_oi_majors": "data/cache/carry_oi_majors_dry_run.json",
        "crowd_contra": "data/cache/crowd_contra_dry_run.json",
        "top_contra": "data/cache/top_contra_dry_run.json",
    }
    result: dict[str, dict[str, Any]] = {}
    for alpha_id, path in paths.items():
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            pnl = payload.get("pnl") if isinstance(payload, dict) else None
            if not isinstance(pnl, dict):
                continue
            result[alpha_id] = {
                "run_id": payload.get("run_id"),
                "n_periods": pnl.get("n_periods"),
                "total_return": pnl.get("total_return"),
                "sharpe": pnl.get("sharpe"),
                "max_drawdown": pnl.get("max_drawdown"),
                "sharpe_vt": pnl.get("sharpe_vt"),
                "max_drawdown_vt": pnl.get("max_drawdown_vt"),
                "total_rejected": pnl.get("total_rejected"),
                "residual": pnl.get("residual"),
            }
        except (OSError, ValueError, TypeError):
            continue
    return result


def _load_usdt_refresh() -> dict[str, Any]:
    path = "data/cache/binance_usdt_multidim_refresh_20260725.json"
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        simulations = payload.get("simulations") if isinstance(payload, dict) else None
        if not isinstance(simulations, dict):
            return {}
        result = {
            "report_type": payload.get("report_type"),
            "quote_asset": payload.get("quote_asset"),
            "data_window": payload.get("data_window"),
            "execution_assumptions": payload.get("execution_assumptions"),
            "risk_gate": payload.get("risk_gate"),
            "deployment_gate": payload.get("deployment_gate"),
            "paper_verdict": payload.get("paper_verdict"),
            "simulations": {},
        }
        for strategy_id, value in simulations.items():
            if not isinstance(value, dict):
                continue
            stats = value.get("stats") if isinstance(value.get("stats"), dict) else {}
            result["simulations"][strategy_id] = {
                "net_pnl": value.get("net_pnl"),
                "net_return": stats.get("total_return"),
                "sharpe": stats.get("sharpe"),
                "max_drawdown": stats.get("max_drawdown"),
                "fee_cost": value.get("fee_cost"),
                "spread_cost": value.get("spread_cost"),
                "impact_cost": value.get("impact_cost"),
                "funding_pnl": value.get("funding_pnl"),
                "n_rebalances": value.get("n_rebalances"),
                "n_trades": value.get("n_trades"),
                "reconciliation_residual": value.get("reconciliation_residual"),
            }
        return result
    except (OSError, ValueError, TypeError):
        return {}


def _write_markdown(path: str, report: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = report["data"]
    lines: list[str] = []
    lines.append("# QUANTOS 多时间框架 / 杠杆 / 组合全维度重测报告")
    lines.append("")
    lines.append(f"> 运行时间：{report['generated_at_utc']}；证据等级：`L2_DRY_RUN_ONLY`；本报告不构成实盘授权或收益承诺。")
    lines.append("")
    lines.append("## 结论摘要")
    lines.append("")
    lines.append(f"- PIT 日频数据：{data['universe']['n_instruments']} 个大盘永续候选、{data['universe']['n_rows']} 个原始日点，完整可比区间由各时间框架单独计算。")
    lines.append(f"- 已重测时间框架：`1d`、`3d`、`7d`、`14d`；`1h/1m` 因缺少同覆盖 OI/资金费 PIT 输入被硬标为 `DATA_UNAVAILABLE`。")
    lines.append(f"- 组合模式：{len(PORTFOLIO_MODES)} 个；杠杆：{len(LEVERAGES)} 档；压力单元：{report['matrix']['cell_count']:,} 个。")
    lines.append("- 4 bps 是 Binance taker 主基准；vol-target 结果表示“动态杠杆上限”，固定杠杆结果表示“整本按该倍数放大”。")
    lines.append("- 本轮是统一研究矩阵和经济摩擦重放，不替代完整九引擎门、历史盘口过滤器、真实整数手数 CPU 复核或 8 周前向证据。")
    lines.append("")
    lines.append("## 数据与方法")
    lines.append("")
    lines.append(f"- 数据版本哈希：`{data['dataset']['hash']}`；范围：`{data['dataset']['start']}` → `{data['dataset']['end']}`。")
    lines.append("- 信号：`carry_oi_majors`、`crowd_contra`、`top_contra` 三条公式冻结，不在本轮改写 Alpha。")
    lines.append("- 每个区间只消费区间起点的信号、终点价格和区间内已落盘资金费；缺失输入直接跳过并计数，不前向填充。单 Alpha、两两组合和三 Alpha 组合按各自所需输入分别建区间，因此有效区间数可能不同。")
    lines.append("- 成本：手续费 + 冲击；冲击按参与率和执行铺开天数缩放。最后一期计入纸面平仓摩擦。")
    lines.append("")
    lines.append("## 各时间框架的 4 bps + 10 bps 冲击基准对比")
    lines.append("")
    lines.append("| 时间框架 | 组合 | 固定杠杆 1x 净收益 | 固定杠杆 Sharpe | 固定杠杆最大回撤 | VT 上限 1x Sharpe | VT 最大回撤 | 区间数 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for row in data["baseline_rows"]:
        fixed = row["fixed"]
        vt = row["vol_target"]
        lines.append(
            f"| {row['timeframe']} | {row['portfolio']} | {_fmt_pct(fixed.get('total_return'))} | {_fmt(fixed.get('sharpe'))} | {_fmt_pct(fixed.get('max_drawdown'))} | {_fmt(vt.get('sharpe'))} | {_fmt_pct(vt.get('max_drawdown'))} | {fixed.get('n_periods')} |"
        )
    lines.append("")
    lines.append("## 官方周度 CPU 纸面基准（用于校准，不与本诊断混合）")
    lines.append("")
    lines.append("官方周度干跑使用真实预交易门、干跑适配器、资金费、部分冲击模型和审计链；本轮矩阵是统一的区间级经济流压力扫描，二者用途不同。")
    lines.append("")
    lines.append("| Alpha | Run ID | 区间数 | 净收益 | Raw Sharpe | Raw 最大回撤 | VT Sharpe | VT 最大回撤 | 拒单数 | 对账残差 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for alpha_id, row in data.get("canonical_weekly", {}).items():
        lines.append(f"| {alpha_id} | `{row.get('run_id')}` | {row.get('n_periods')} | {_fmt_pct(row.get('total_return'))} | {_fmt(row.get('sharpe'))} | {_fmt_pct(row.get('max_drawdown'))} | {_fmt(row.get('sharpe_vt'))} | {_fmt_pct(row.get('max_drawdown_vt'))} | {row.get('total_rejected')} | {row.get('residual')} |")
    lines.append("")
    usdt = data.get("usdt_refresh", {})
    if usdt:
        lines.append("## Binance USDT 永续真实过滤/连续纸面刷新")
        lines.append("")
        window = usdt.get("data_window") or {}
        lines.append(f"本次刷新窗口：{window.get('start')} → {window.get('end')}；4 bps taker；真实整数数量/步长/最小名义价值；当前盘口价差校准；Almgren–Chriss 冲击。部署门：`{(usdt.get('deployment_gate') or {}).get('state')}`；纸面结论：`{usdt.get('paper_verdict')}`。")
        lines.append("")
        lines.append("| 策略 | 净收益 | Sharpe | 最大回撤 | 手续费 | 价差 | 冲击 | 资金费 PnL | 调仓数 | 成交数 | 对账残差 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for strategy_id, row in (usdt.get("simulations") or {}).items():
            lines.append(f"| {strategy_id} | {_fmt_pct(row.get('net_return'))} | {_fmt(row.get('sharpe'))} | {_fmt_pct(row.get('max_drawdown'))} | {row.get('fee_cost')} | {row.get('spread_cost')} | {row.get('impact_cost')} | {row.get('funding_pnl')} | {row.get('n_rebalances')} | {row.get('n_trades')} | {row.get('reconciliation_residual')} |")
        lines.append("")
    lines.append("## 各时间框架的杠杆敏感度（4 bps / 10 bps 冲击 / 10%参与率 / 1天）")
    lines.append("")
    lines.append("下表只展示每个时间框架和组合的固定杠杆净 Sharpe；完整压力矩阵在 JSONL 制品中。")
    lines.append("")
    lines.append("| 时间框架 | 组合 | " + " | ".join(f"{lev:g}x" for lev in LEVERAGES) + " |")
    lines.append("|---|---|" + "---:|" * len(LEVERAGES))
    for row in data["leverage_rows"]:
        lines.append("| " + row["timeframe"] + " | " + row["portfolio"] + " | " + " | ".join(_fmt(x) for x in row["sharpe_by_leverage"]) + " |")
    lines.append("")
    lines.append("固定杠杆的 Sharpe 在线性放大收益和成本时不会变化，实际风险变化体现在收益和回撤：")
    lines.append("")
    lines.append("| 时间框架 | 组合 | " + " | ".join(f"{lev:g}x 收益 / 回撤" for lev in LEVERAGES) + " |")
    lines.append("|---|---|" + "---:|" * len(LEVERAGES))
    for row in data["leverage_rows"]:
        pairs = [f"{_fmt_pct(ret)} / {_fmt_pct(dd)}" for ret, dd in zip(row["return_by_leverage"], row["dd_by_leverage"])]
        lines.append("| " + row["timeframe"] + " | " + row["portfolio"] + " | " + " | ".join(pairs) + " |")
    lines.append("")
    lines.append("## 稳健性筛选")
    lines.append("")
    lines.append("`stress_robust` 只表示在本矩阵内的压力统计通过（最差 VT Sharpe ≥ 0.8、压力单元正收益比例 ≥ 75%、最大回撤不超过阈值）；它不是完整部署门结果。")
    lines.append("")
    lines.append("| 时间框架 | 组合 | 杠杆 | 最差 VT Sharpe | 最大 VT 回撤 | 压力正收益比例 | 通过的回撤阈值 |")
    lines.append("|---|---|---:|---:|---:|---:|---|")
    for row in data["robust_rows"][:40]:
        lines.append(f"| {row['timeframe']} | {row['portfolio']} | {row['leverage']}x | {_fmt(row['worst_vt_sharpe'])} | {_fmt_pct(row['worst_vt_drawdown'])} | {_fmt_pct(row['positive_fraction'])} | {', '.join(str(int(x*100))+'%' for x in row['dd_thresholds_passed']) or '无'} |")
    lines.append("")
    lines.append("## 当前系统状态与边界")
    lines.append("")
    lines.append("- 所有结果仍为研究/纸面证据；真实下单旗标保持关闭。")
    lines.append("- 1H/1m 不给出三 Alpha 结果：目前 1m 仅有 BTC 价格，缺少 24 币同覆盖 OI 与资金费；1H 数据库也未形成同覆盖输入。")
    lines.append("- 需要投资人审阅的最终准入仍包括：完整九引擎风险输入、CPU 整手成交、前向至少 8 个有效周期、以及人工/外部治理证据。")
    lines.append("")
    lines.append("## 证据文件")
    lines.append("")
    lines.append(f"- 主报告：`{report['report_path']}`")
    lines.append(f"- 完整单元：`{report['cells_path']}`")
    lines.append("- 既有官方周度纸面：`data/cache/dry_run.db`、`data/cache/paper_realtime_status.json`。")
    lines.append("")
    lines.append("## 免责声明")
    lines.append("")
    lines.append("历史回测和纸面标记不等于未来收益；成本、流动性、资金费、数据缺口和市场状态变化都可能使结果恶化。任何真实资金部署必须经过独立第三方审查、法律/合规审查和多方人工批准。")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(value):.3f}"


def _fmt_pct(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def run(*, report_path: str = DEFAULT_REPORT, cells_path: str = DEFAULT_CELLS, markdown_path: str = DEFAULT_MARKDOWN,
        aum: float = DEFAULT_AUM) -> dict[str, Any]:
    if not _finite(aum) or float(aum) <= 0.0:
        raise ValueError("aum must be positive and finite")
    started = int(time.time() * 1000)
    panel, instruments = build_panel()
    signals = OrderedDict((alpha_id, ax.evaluate(expr, panel)) for alpha_id, expr in ALPHA_EXPRESSIONS.items())
    dataset = {
        "source": "data/cache/crypto_binance.db + crypto_funding_rates.json + crypto_oi_metrics.json",
        "pit": True,
        "synthetic_defaults": False,
        "n_instruments": len(instruments),
        "n_rows": len(panel.event_times),
        "start": _day_string(panel.event_times[0]),
        "end": _day_string(panel.event_times[-1]),
        "hash": _stable_hash({"instruments": list(panel.instruments), "event_times": list(panel.event_times), "matrices": panel.matrices}),
    }
    os.makedirs(os.path.dirname(cells_path), exist_ok=True)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    cell_count = 0
    baseline_rows: list[dict[str, Any]] = []
    leverage_rows: list[dict[str, Any]] = []
    robust_acc: dict[tuple[str, str, float], dict[str, Any]] = {}
    availability = {
        "1h": {"status": "DATA_UNAVAILABLE", "reason": "no same-coverage PIT 1h OI + funding panel"},
        "1m": {"status": "DATA_UNAVAILABLE", "reason": "only BTC 1m prices; no 24-coin PIT OI + funding panel"},
    }
    with open(cells_path, "w", encoding="utf-8", newline="\n") as cells:
        for timeframe, cadence_days in TIMEFRAMES.items():
            intervals_by_mode = {
                mode: _build_intervals(panel, signals, cadence_days, _required_alpha_ids(mode))
                for mode in PORTFOLIO_MODES
            }
            books = {mode: _book_series(intervals_by_mode[mode][0], mode) for mode in PORTFOLIO_MODES}
            for mode, book in books.items():
                intervals, skipped = intervals_by_mode[mode]
                baseline_scenario = _scenario(book, cadence_days=cadence_days, leverage=1.0, **BASELINE_CELL)
                baseline_rows.append({
                    "timeframe": timeframe,
                    "portfolio": mode,
                    "fixed": {key: _round(value) if isinstance(value, float) else value for key, value in baseline_scenario["fixed"].items()},
                    "vol_target": {key: _round(value) if isinstance(value, float) else value for key, value in baseline_scenario["vol_target"].items()},
                    "intervals": len(intervals),
                    "skipped": skipped,
                })
                sharpe_by_leverage: list[float | None] = []
                return_by_leverage: list[float | None] = []
                dd_by_leverage: list[float | None] = []
                for leverage in LEVERAGES:
                    lev_scenario = _scenario(book, cadence_days=cadence_days, leverage=leverage, **BASELINE_CELL)
                    sharpe_by_leverage.append(_round(lev_scenario["fixed"].get("sharpe")))
                    return_by_leverage.append(_round(lev_scenario["fixed"].get("total_return")))
                    dd_by_leverage.append(_round(lev_scenario["fixed"].get("max_drawdown")))
                leverage_rows.append({
                    "timeframe": timeframe,
                    "portfolio": mode,
                    "sharpe_by_leverage": sharpe_by_leverage,
                    "return_by_leverage": return_by_leverage,
                    "dd_by_leverage": dd_by_leverage,
                })
                key = (timeframe, mode, 1.0)
                robust_acc[key] = {
                    "timeframe": timeframe,
                    "portfolio": mode,
                    "leverage": 1.0,
                    "worst_vt_sharpe": math.inf,
                    "worst_vt_drawdown": 0.0,
                    "positive_count": 0,
                    "cell_count": 0,
                    "dd_thresholds_passed": [],
                }
                # Full pressure grid. A JSONL cell is the audit unit; no cell
                # contains the full return vector, keeping the artifact bounded.
                for leverage in LEVERAGES:
                    for fee in FEES_BPS:
                        for impact in IMPACT_BPS:
                            for participation in PARTICIPATION:
                                for execution_days in EXECUTION_DAYS:
                                    scenario = _scenario(
                                        book,
                                        cadence_days=cadence_days,
                                        leverage=leverage,
                                        fee_bps=fee,
                                        impact_bps=impact,
                                        participation_rate=participation,
                                        execution_days=execution_days,
                                    )
                                    fixed = scenario["fixed"]
                                    vt = scenario["vol_target"]
                                    row = {
                                        "timeframe": timeframe,
                                        "cadence_days": cadence_days,
                                        "portfolio": mode,
                                        "leverage": leverage,
                                        "fee_bps": fee,
                                        "impact_bps": impact,
                                        "participation_rate": participation,
                                        "execution_days": execution_days,
                                        "fixed": {key: _round(value) if isinstance(value, float) else value for key, value in fixed.items()},
                                        "vol_target": {key: _round(value) if isinstance(value, float) else value for key, value in vt.items()},
                                        "fee_drag": _round(scenario["fee_drag"]),
                                        "impact_drag": _round(scenario["impact_drag"]),
                                        "turnover": _round(scenario["turnover"]),
                                        "cell_hash": _stable_hash({"timeframe": timeframe, "portfolio": mode, "leverage": leverage, "fee_bps": fee, "impact_bps": impact, "participation_rate": participation, "execution_days": execution_days}),
                                        "paper_only": True,
                                    }
                                    cells.write(json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n")
                                    cell_count += 1
                                    acc_key = (timeframe, mode, float(leverage))
                                    acc = robust_acc.setdefault(
                                        acc_key,
                                        {
                                            "timeframe": timeframe,
                                            "portfolio": mode,
                                            "leverage": float(leverage),
                                            "worst_vt_sharpe": math.inf,
                                            "worst_vt_drawdown": 0.0,
                                            "positive_count": 0,
                                            "cell_count": 0,
                                            "dd_thresholds_passed": [],
                                        },
                                    )
                                    vt_sharpe = vt.get("sharpe")
                                    vt_dd = vt.get("max_drawdown")
                                    if vt_sharpe is not None:
                                        acc["worst_vt_sharpe"] = min(acc["worst_vt_sharpe"], float(vt_sharpe))
                                    if vt_dd is not None:
                                        acc["worst_vt_drawdown"] = max(acc["worst_vt_drawdown"], float(vt_dd))
                                    if float(vt.get("total_return") or 0.0) > 0.0:
                                        acc["positive_count"] += 1
                                    acc["cell_count"] += 1
    robust_rows: list[dict[str, Any]] = []
    for acc in robust_acc.values():
        positive_fraction = acc["positive_count"] / acc["cell_count"] if acc["cell_count"] else 0.0
        acc["positive_fraction"] = positive_fraction
        acc["worst_vt_sharpe"] = None if acc["worst_vt_sharpe"] == math.inf else acc["worst_vt_sharpe"]
        passed = []
        if acc["worst_vt_sharpe"] is not None and positive_fraction >= 0.75:
            passed = [threshold for threshold in DD_THRESHOLDS if acc["worst_vt_drawdown"] <= threshold and threshold <= 0.15]
        acc["dd_thresholds_passed"] = passed
        robust_rows.append({key: (_round(value) if isinstance(value, float) else value) for key, value in acc.items()})
    robust_rows.sort(key=lambda row: ((row["worst_vt_sharpe"] is None), -(row["worst_vt_sharpe"] or -999.0), row["worst_vt_drawdown"], row["timeframe"], row["portfolio"], row["leverage"]))
    report: dict[str, Any] = {
        "schema_version": "crypto_multidim_retest.v1",
        "generated_at": started,
        "generated_at_utc": datetime.fromtimestamp(started / 1000.0, tz=timezone.utc).isoformat(),
        "readiness": "L2_DRY_RUN_ONLY",
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "classification": "RESEARCH_DIAGNOSTIC_ONLY",
        "data": {
            "dataset": dataset,
            "universe": {"n_instruments": len(instruments), "instruments": list(instruments), "n_rows": len(panel.event_times)},
            "timeframes": {key: {"cadence_days": value} for key, value in TIMEFRAMES.items()} | availability,
            "alpha_ids": list(ALPHA_IDS),
            "portfolio_modes": {key: (None if value is None else list(value)) for key, value in PORTFOLIO_MODES.items()},
            "leverage_caps": list(LEVERAGES),
            "fees_bps": list(FEES_BPS),
            "impact_bps": list(IMPACT_BPS),
            "participation_rates": list(PARTICIPATION),
            "execution_days": list(EXECUTION_DAYS),
            "drawdown_thresholds": list(DD_THRESHOLDS),
            "baseline_cell": BASELINE_CELL,
            "baseline_rows": baseline_rows,
            "canonical_weekly": _load_canonical_weekly(),
            "usdt_refresh": _load_usdt_refresh(),
            "leverage_rows": leverage_rows,
            "robust_rows": robust_rows,
        },
        "matrix": {
            "cell_count": cell_count,
            "cells_path": cells_path,
            "timeframe_count": len(TIMEFRAMES),
            "portfolio_count": len(PORTFOLIO_MODES),
            "leverage_count": len(LEVERAGES),
            "stress_count_per_book": len(LEVERAGES) * len(FEES_BPS) * len(IMPACT_BPS) * len(PARTICIPATION) * len(EXECUTION_DAYS),
        },
        "methodology_boundaries": [
            "日频 OI/资金费/价格 PIT 输入；无合成默认值",
            "区间级 signal-at-start / PnL-after-start，无同区间前视",
            "成本为手续费 + 参与率/铺开天数缩放的冲击近似",
            "未使用历史盘口、历史 exchangeInfo 过滤器或真实整数手数",
            "未以本诊断结果替代完整九引擎研究风险门和前向 8 周门",
        ],
        "report_path": report_path,
        "cells_path": cells_path,
    }
    report["markdown_path"] = markdown_path
    report["report_hash"] = _stable_hash(report)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=True, sort_keys=True, indent=2)
    _write_markdown(markdown_path, report)
    print(json.dumps({
        "classification": report["classification"],
        "dataset": {key: dataset[key] for key in ("n_instruments", "n_rows", "start", "end", "hash")},
        "cell_count": cell_count,
        "baseline_rows": len(baseline_rows),
        "robust_rows": len(robust_rows),
        "report": report_path,
        "cells": cells_path,
        "markdown": markdown_path,
        "live_allowed": False,
        "order_submission_allowed": False,
    }, ensure_ascii=False, indent=2))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    quarantine_legacy_backtest("tools.crypto_multidim_retest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--cells", default=DEFAULT_CELLS)
    parser.add_argument("--markdown", default=DEFAULT_MARKDOWN)
    parser.add_argument("--aum", type=float, default=DEFAULT_AUM)
    args = parser.parse_args(argv)
    run(report_path=args.report, cells_path=args.cells, markdown_path=args.markdown, aum=args.aum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
