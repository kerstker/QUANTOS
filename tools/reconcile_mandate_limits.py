"""限额对账诊断工具(只读)——审计 docs/audit/2026-07-07-live-readiness-top-tier-gap-audit.md
§4.4 [P1-R1]。

## 问题

当前存在两套互不协调的仓位限额体系,并行生效于同一条下单路径:

1. **gate 侧**——`tools/live_track_advance.py` 把 `PreTradeRiskGate` 的实际生效上限算成
   aum 的乘数(见其 `gate_limits(...)` 调用点 + `evaluate_risk_state`/`flatten_target_for_breach`
   调用点):`gross = aum * 2`、`net = aum * 0.3`、`single_order = aum * 0.5`、
   `daily_loss = aum * 0.1`。这组数字在原文件里是工程判断(客观安全边界,防代码错误/异常输入
   造成的灾难性下单),不是业务风险容忍度,也没有任何"用户签"标注——见该文件模块 docstring
   "gate 限额 vs mandate 限额,两者不同层"一段。
2. **mandate 侧**——`key/live_mandate*.json` 是真正逐字段人工签署的独立限额体系
   (`max_gross_notional`/`max_net_notional`/`max_single_order_notional`,见
   `live/governance/mandate_store.py::load_mandate`),回答"系统可以不经人工自主执行多少"。

两层都真实生效(gate 先评,mandate 后评,见 `live_track_advance.main()`),但代码里从未有
一处把两组数字并排列出来,告诉人"给定当前 aum,哪一层真正收口"。这个工具只做这一件事。

## 硬约束(诊断工具本身,不是被诊断对象)

只读、不改变任何决策/门禁逻辑,不引入任何新的 BLOCK/PASS 判定——单纯把两套已经存在于代码里
的数字算出来、并排打印给人看。不修改 `tools/live_track_advance.py` 或
`live/governance/mandate.py` 的任何一行;`GATE_*_AUM_MULT` 常量是对 `live_track_advance.py`
调用点字面量的复制(该文件把这组数字写成调用处字面量、没有导出成命名常量),不是从那个文件
里 import 出来的——`tests/tools/test_reconcile_mandate_limits.py` 里的"源码字符串哨兵"测试
钉住这份复制不会与原文件悄悄漂移。

不发任何网络请求(只读本地 `key/live_mandate*.json` 文件 + 内存计算),因此不需要像
`tools/verify_key_permissions.py` 那样进 `tests/governance/test_crypto_data_boundary.py`
的 `_FETCHERS` 清单(那份清单的注释明确写着"every network-bearing out-of-gate fetcher")。
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import List, Optional

from live.governance.mandate_store import MandateLoadError, load_mandate

# --- gate 侧 aum 乘数(镜像 tools/live_track_advance.py 的调用点字面量;见模块 docstring) ---
GATE_GROSS_AUM_MULT = 2.0            # gate_limits(..., gross_cap=aum * 2, ...)
GATE_NET_AUM_MULT = 0.3              # gate_limits(..., net_cap=aum * 0.3, ...)
GATE_SINGLE_ORDER_AUM_MULT = 0.5     # gate_limits(..., single_order_cap=aum * 0.5, ...)
GATE_DAILY_LOSS_AUM_MULT = 0.1       # gate_limits(..., daily_loss_cap=aum * 0.1)
GATE_MAX_LEVERAGE = 3.0              # gate_limits() 内 RiskLimits(max_leverage=3.0, ...)——固定值,与 aum 无关

# 每个 venue 对应的 mandate 文件路径(镜像 tools/live_track_advance.py::_VENUES[*]["mandate_file"])。
VENUE_MANDATE_FILES = {
    "binance": os.path.join("key", "live_mandate.json"),
    "bybit": os.path.join("key", "live_mandate_bybit.json"),
    "bybit_demo": os.path.join("key", "live_mandate_bybit_demo.json"),
}


@dataclass(frozen=True)
class GateLimits:
    """给定 aum 后,`PreTradeRiskGate` 实际生效的客观安全边界(工程默认,非业务授权)。"""
    aum: float
    gross_notional: float
    net_notional: float
    single_order_notional: float
    daily_loss_notional: float
    max_leverage: float


@dataclass(frozen=True)
class MandateLimits:
    """`key/live_mandate*.json` 里真人签署的业务授权限额。`signed=False` 表示文件缺失/加载
    失败——诚实呈现"未签署",绝不拿 None/占位数字冒充已签署。"""
    signed: bool
    path: str
    gross_notional: Optional[float] = None
    net_notional: Optional[float] = None
    single_order_notional: Optional[float] = None
    mandate_id: Optional[str] = None
    venue: Optional[str] = None
    exchange: Optional[str] = None
    unsigned_reason: Optional[str] = None


@dataclass(frozen=True)
class LimitComparison:
    """某一维度上两层谁更紧(数字更小的一层才是真正约束下单能力的那层)。"""
    dimension: str
    unit: str                      # "usd" | "x"
    gate_value: float
    mandate_value: Optional[float]
    tighter: str                   # "gate" | "mandate" | "tie" |
                                   # "mandate_unsigned(gate_only)" | "mandate_has_no_field"


def compute_gate_limits(aum: float) -> GateLimits:
    """纯计算、无 IO——镜像 tools/live_track_advance.py 的 gate_limits()/evaluate_risk_state()/
    flatten_target_for_breach() 调用点(见模块顶部常量注释)。"""
    return GateLimits(
        aum=aum,
        gross_notional=aum * GATE_GROSS_AUM_MULT,
        net_notional=aum * GATE_NET_AUM_MULT,
        single_order_notional=aum * GATE_SINGLE_ORDER_AUM_MULT,
        daily_loss_notional=aum * GATE_DAILY_LOSS_AUM_MULT,
        max_leverage=GATE_MAX_LEVERAGE,
    )


def load_mandate_limits(path: str) -> MandateLimits:
    """只读加载 mandate 文件 -> `MandateLimits`。缺文件/JSON 损坏/必填字段缺失 -> `signed=False`
    + 诊断性 `unsigned_reason`(绝不抛异常崩掉调用方,复用 `load_mandate` 本身的 fail-closed
    错误信息,不重新发明校验逻辑)。"""
    try:
        m = load_mandate(path)
    except MandateLoadError as exc:
        return MandateLimits(signed=False, path=path, unsigned_reason=str(exc))
    return MandateLimits(
        signed=True, path=path,
        gross_notional=m.max_gross_notional,
        net_notional=m.max_net_notional,
        single_order_notional=m.max_single_order_notional,
        mandate_id=m.mandate_id, venue=m.venue, exchange=m.exchange,
    )


def _tighter(gate_value: float, mandate_value: Optional[float]) -> str:
    if mandate_value is None:
        return "mandate_unsigned(gate_only)"
    if mandate_value < gate_value:
        return "mandate"
    if gate_value < mandate_value:
        return "gate"
    return "tie"


def compare_limits(gate: GateLimits, mandate: MandateLimits) -> List[LimitComparison]:
    """并排比较两层的数字,不做任何 BLOCK/PASS 判定——只回答"哪个数字更小"(= 更紧、真正
    收口的那层)。`daily_loss_notional`/`max_leverage` 只在 gate 侧存在(mandate schema,见
    `live/governance/mandate.py::Mandate`,本身没有这两个字段)——这两行恒为
    `mandate_has_no_field`,与该 mandate 文件是否已签署无关,区别于 gross/net/single 在
    mandate 缺签署时的 `mandate_unsigned(gate_only)`。"""
    rows: List[LimitComparison] = []
    for name, gv, mv in (
        ("gross_notional", gate.gross_notional, mandate.gross_notional),
        ("net_notional", gate.net_notional, mandate.net_notional),
        ("single_order_notional", gate.single_order_notional, mandate.single_order_notional),
    ):
        rows.append(LimitComparison(name, "usd", gv, mv, _tighter(gv, mv)))
    rows.append(LimitComparison("daily_loss_notional", "usd", gate.daily_loss_notional, None,
                                "mandate_has_no_field"))
    rows.append(LimitComparison("max_leverage", "x", gate.max_leverage, None,
                                "mandate_has_no_field"))
    return rows


def _fmt_value(value: Optional[float], unit: str) -> str:
    if value is None:
        return "—"
    if unit == "usd":
        return f"${value:,.2f}"
    return f"{value:g}x"


def format_report(gate: GateLimits, mandate: MandateLimits,
                  comparisons: List[LimitComparison]) -> str:
    """人可读的对照表——Phase 3A 签字材料参考。"""
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("限额对账诊断(只读)—— gate 侧 aum 乘数 vs mandate 侧人工签署限额")
    lines.append("审计 docs/audit/2026-07-07-live-readiness-top-tier-gap-audit.md §4.4 [P1-R1]")
    lines.append("=" * 78)
    lines.append(f"给定 aum = ${gate.aum:,.2f}")
    lines.append("")
    lines.append(f"mandate 文件: {mandate.path}")
    if mandate.signed:
        lines.append(f"  状态: 已签署  mandate_id={mandate.mandate_id}  "
                     f"venue={mandate.venue}  exchange={mandate.exchange}")
    else:
        lines.append(f"  状态: 未签署 —— {mandate.unsigned_reason}")
    lines.append("")
    header = (f"{'维度 dimension':<26}{'gate 侧(工程默认)':>20}"
             f"{'mandate 侧(人工签署)':>22}  {'更紧的一层':>28}")
    lines.append(header)
    lines.append("-" * (len(header) + 10))
    for c in comparisons:
        gv = _fmt_value(c.gate_value, c.unit)
        mv = _fmt_value(c.mandate_value, c.unit)
        # 更紧一层的标签最长是 "mandate_unsigned(gate_only)"(28 字符)——固定 2 空格分隔符
        # + 28 宽右对齐,防止它与上一列的 "—" 连在一起变成不可读的 "—mandate_unsigned(...)"。
        lines.append(f"{c.dimension:<26}{gv:>20}{mv:>22}  {c.tighter:>28}")
    lines.append("")
    lines.append("说明: daily_loss_notional / max_leverage 只在 gate 侧存在——mandate schema "
                 "(live/governance/mandate.py::Mandate) 本身没有这两个字段,'mandate_has_no_field' "
                 "是诚实反映,不是遗漏,这两行的'更紧'比较没有意义。")
    lines.append("本工具只并排呈现两套已经存在于代码里的数字,不做任何 BLOCK/PASS 判定、"
                 "不改变任何门禁/决策逻辑。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI(人工手动调用:`python -m tools.reconcile_mandate_limits --aum 10000 --venue binance`)
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="只读限额对账诊断——并排打印 gate 侧(aum 乘数)与 mandate 侧(人工签署)的 "
                    "gross/net/single_order/daily_loss/leverage,标出当前 aum 下哪一层更紧。"
                    "不改变任何决策/门禁逻辑,不发网络请求。")
    parser.add_argument("--aum", type=float, required=True,
                        help="要对账的 aum(本工具不发网络请求获取真实账户余额，请手动传入)")
    parser.add_argument("--venue", choices=sorted(VENUE_MANDATE_FILES), default="binance",
                        help="按 venue 选取对应的 mandate 文件路径（默认 binance）")
    parser.add_argument("--mandate-file", default=None,
                        help="显式覆盖 mandate 文件路径（优先于 --venue 的默认映射）")
    args = parser.parse_args(argv)

    mandate_path = args.mandate_file or VENUE_MANDATE_FILES[args.venue]
    gate = compute_gate_limits(args.aum)
    mandate = load_mandate_limits(mandate_path)
    comparisons = compare_limits(gate, mandate)
    print(format_report(gate, mandate, comparisons))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
