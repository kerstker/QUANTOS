"""研究/paper 干跑限额 profile 的唯一来源(P2-13)。

这是**研究/paper 专用**的宽松"占位"限额:上限刻意非物理(1e14 名义敞口、1e7 杠杆/
参与率、1e7 单名集中度),只用来让 dry-run 回放不被风控挡住,从而观察策略本身的行为。
它既衡量不了真实容量,也**绝不能进入 live/Mainnet 路径**:

* ``allowed_accounts`` 恒为 ``{"paper"}``(``account`` 参数只接受 ``"paper"``,其他值直接报错);
* live 路径的真实限额由 ``services.risk.pre_trade`` 的 gate_limits()/mandate 另行下发,
  与本模块无关;按 AGENTS.md 的 L2_DRY_RUN_ONLY 边界,本模块不得被 live 代码导入。

这些干跑会把 DryRunRun 行写进 data/cache/dry_run.db 并成为下游证据
(tools/common_risk_research.py 等按 strategy_id 读取),所以返回值必须与重构前的
内联字面量逐字节一致——连 int/float 类型都承重(``10 ** 13`` 是 int、``1e14`` 是
float,``limits_hash()`` 序列化后不同)。tests/tools/test_research_dry_run_profile.py
为每个调用方钉住 golden hash:改这里的默认值 = 改已归档证据。
"""
from __future__ import annotations

from collections.abc import Iterable

from services.risk.pre_trade.config import RiskLimits

# 研究/paper 干跑账户:本项目任何 dry-run 证据都不得来自 live 账户。
RESEARCH_DRY_RUN_ACCOUNT = "paper"

# 默认值 = 重构前 tools/dry_run_*_multi.py 内联字面量的多数派取值。
# 历史变体必须由调用方**显式传参**保留,不得归一化:
#   * dry_run_carry_multi.py / dry_run_carry_authoritative_multi.py:max_participation_rate=0.10
#     (Rank #11:真实参与率上限,不再是 1e7 占位)
#   * ashare_alpha_research.py:max_single_name_pct=0.10、max_participation_rate=0.10(A股风控口径)
#   * crypto_robust_optimization.py:leverage/participation/drawdown 由候选与压力场景驱动


def research_dry_run_limits(
    instruments: Iterable[str],
    *,
    max_participation_rate: float = 1e7,
    account: str = RESEARCH_DRY_RUN_ACCOUNT,
    max_leverage: float = 1e7,
    max_single_name_pct: float = 1e7,
    max_drawdown: float = 1.0,
) -> RiskLimits:
    """构造研究/paper 干跑占位限额;账户恒为 paper,live 账户一律拒绝。"""
    if account != RESEARCH_DRY_RUN_ACCOUNT:
        raise ValueError(f"research dry-run limits are paper-only, got account={account!r}")
    return RiskLimits(
        allowed_instruments=frozenset(instruments),
        allowed_accounts=frozenset({RESEARCH_DRY_RUN_ACCOUNT}),
        default_max_position_qty=10 ** 13,
        max_order_notional=1e14,
        fat_finger_qty=10 ** 13,
        max_gross_exposure=1e14,
        max_net_exposure=1e14,
        max_leverage=max_leverage,
        max_single_name_pct=max_single_name_pct,
        max_turnover=1e7,
        max_participation_rate=max_participation_rate,
        max_instrument_vol=5.0,
        daily_loss_limit=1e14,
        max_drawdown=max_drawdown,
    )
