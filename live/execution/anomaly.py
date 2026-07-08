"""盘中权益异常检测(Phase 5 item 2,dim10)。

哨兵(小时级)已监控 STATIC 阈值越限(回撤/敞口,`risk_monitor.evaluate_risk_state`)。本模块补的是
**时序异常**——两轮之间的权益跳变(暴涨/暴跌),即便**仍在** 20% 回撤 / 敞口绝对上限之内也值得人看
一眼(注入的合成尖峰/崩塌正是验收要抓的)。substrate = 哨兵已持久化的 `live_equity{suffix}.jsonl` 曲线。

设计约束(与既有安全哲学一致):
- **默认惰性**:`max_jump_pct=None`(默认)-> 永不告警。阈值是**用户签的风险数字**,签之前生产路径
  字节等价。开启只在用户签一个数字后。
- **只加告警**:异常**绝不** arm kill-switch、绝不触发减仓、绝不碰任何 order 路径——异常常常是良性但
  值得人看一眼,自动 halt 一个统计毛刺会引入新的不受控动作。自动 de-risk 维持原样(仅静态越限 + 独立
  reduce-only 紧急通道 + full-flatten 人工确认)。
- **fail-closed**(开启后):缺/坏历史基线 -> anomalous=True 'insufficient_history'(数据不全当可疑,
  镜像 evaluate_risk_state 的 fail-closed 姿态——但只加告警,不交易)。

纯函数、无 IO/网络/墙钟/RNG。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class AnomalyReport:
    anomalous: bool
    reasons: Tuple[str, ...]
    jump_pct: float


def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def detect_equity_anomaly(*, prior_equity: Optional[float], current_equity: Optional[float],
                          max_jump_pct: Optional[float] = None) -> AnomalyReport:
    """两轮权益跳变检测。max_jump_pct None(默认)-> 惰性(生产字节等价)。开启后:缺/坏历史 ->
    fail-closed 'insufficient_history';current 非有限 -> 'invalid_current_equity';|跳变| > 阈值 ->
    'equity_jump'。jump_pct = (current-prior)/prior(带符号,无法算时 0.0)。"""
    if max_jump_pct is None:
        return AnomalyReport(anomalous=False, reasons=(), jump_pct=0.0)   # 惰性:签数字前零告警

    if not _finite(prior_equity) or float(prior_equity) <= 0:
        return AnomalyReport(anomalous=True, reasons=("insufficient_history",), jump_pct=0.0)
    if not _finite(current_equity):
        return AnomalyReport(anomalous=True, reasons=("invalid_current_equity",), jump_pct=0.0)

    jump = (float(current_equity) - float(prior_equity)) / float(prior_equity)
    if abs(jump) > float(max_jump_pct):
        return AnomalyReport(anomalous=True,
                             reasons=(f"equity_jump:{jump:.4f}>{float(max_jump_pct)}",),
                             jump_pct=jump)
    return AnomalyReport(anomalous=False, reasons=(), jump_pct=jump)
