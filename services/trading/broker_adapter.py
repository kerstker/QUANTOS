"""Broker adapter 接缝(轨道3 子项目1)—— L2_DRY_RUN_ONLY,一寸不让。

把"实盘 = 换适配器 + 人工批准"里的「换适配器」变成一个正式接缝:BrokerAdapter Protocol 定义执行适配器
的契约,测过的干跑引擎经 DryRunBrokerAdapter 薄包装 conform(零改动引擎),LiveBrokerAdapter 是接缝的
live 槽位——但它在 L2 下**结构上无法构造**(live_trading_armed 恒 False → 构造即 raise),且**没有任何真
实现**(即便强凑 armed 配置也第二次 raise)。绝不 import 任何券商/网络 SDK。assert_dry_run_adapter 是 fail-
closed 守卫,接进干跑流水线让接缝载荷承重(运行时只放行证明为干跑的适配器)。

诚实边界:守卫是运行时防误用的 chokepoint,NOT 对受信内进程代码的信任边界——那个 build-time 面(受信代码
可绕过)是轨道3 子项目3(进程隔离)的范畴。真券商适配器实现 = post-L2、人工门控 = 显式非目标。
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from quantos.contracts.config import SafetyConfig, SafetyViolation
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.trading.dry_run_adapters.domain.models import (DryRunAdapterRun, DryRunCommand,
                                                             NormalizedReport)


@runtime_checkable
class BrokerAdapter(Protocol):
    """执行适配器接缝。`DryRun*` 前缀是历史名——它们就是当前流经流水线的规范订单/回报契约,真适配器
    消费同一套(重命名它们是本子项目非目标)。"""
    is_dry_run: bool

    def submit_order(self, cmd: DryRunCommand) -> DryRunAdapterRun: ...

    def fetch_fill(self, run_id: str) -> Optional[NormalizedReport]: ...


class DryRunBrokerAdapter:
    """薄包装现有 DryRunAdapterEngine(零改动它),满足 BrokerAdapter。绝不真发。"""
    is_dry_run = True

    def __init__(self, engine: Optional[DryRunAdapterEngine] = None) -> None:
        self._engine = engine if engine is not None else DryRunAdapterEngine()

    def submit_order(self, cmd: DryRunCommand) -> DryRunAdapterRun:
        return self._engine.process(cmd)

    def fetch_fill(self, run_id: str) -> Optional[NormalizedReport]:
        return self._engine.simulate_fill(run_id)


class LiveBrokerAdapter:
    """接缝的 live 槽位 —— **结构性拒绝的空壳,无真实现**。L2 下 live_trading_armed 恒 False → 构造即 raise;
    即便强凑 armed 配置,也因无真实现再次 raise。绝不 import 券商/网络 SDK,无任何真发单/真凭证/真连接。
    真实现 = post-L2、人工门控 = 显式非目标。"""
    is_dry_run = False

    def __init__(self, safety_config: SafetyConfig) -> None:
        if not safety_config.live_trading_armed:
            raise SafetyViolation(
                "LiveBrokerAdapter cannot be constructed at L2_DRY_RUN_ONLY "
                "(live_trading_armed is False) — intentional seam refusal.")
        raise SafetyViolation(
            "LiveBrokerAdapter is a non-functional seam placeholder — a real broker "
            "implementation is post-L2, human-gated, and out of scope.")

    def submit_order(self, cmd: DryRunCommand) -> DryRunAdapterRun:
        raise SafetyViolation("live order submission is disabled (L2_DRY_RUN_ONLY)")

    def fetch_fill(self, run_id: str) -> Optional[NormalizedReport]:
        raise SafetyViolation("live fill fetch is disabled (L2_DRY_RUN_ONLY)")


def assert_dry_run_adapter(adapter) -> None:
    """fail-closed 守卫:只放行证明为干跑的适配器,未知一律拒。运行时防误用的 chokepoint(非受信代码信任边界)。"""
    if isinstance(adapter, LiveBrokerAdapter):        # 实际到不了(构造已 raise),防御性
        raise SafetyViolation("live adapter is not permitted in the dry-run pipeline (L2)")
    if isinstance(adapter, (DryRunAdapterEngine, DryRunBrokerAdapter)):
        return
    if getattr(adapter, "is_dry_run", False) is True:
        return
    raise SafetyViolation(
        f"unknown adapter {type(adapter).__name__} is not a proven dry-run adapter (fail-closed)")
