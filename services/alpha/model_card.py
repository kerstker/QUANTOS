"""Model card gate for promotion — fail-closed verification that a strategy has documentation.

模型卡片是向 APPROVED 晋级的必需制品:强化人类负责 + 审查窗口 + 可审计记录(VALIDATION.
CHALLENGER.MODEL_RISK M1→M2)。promotion.py 在烧 grant 之前预检 model_card 存在;缺失即
拒绝,不消耗 grant。

结构决定(勿"改进"掉):
  - model_card 文件路径 = `model_cards/{strategy_id}.md`(repo 根目录,与 docs/、services/、
    tools/ 平级);
  - 策略白名单 = 从 model_cards/ 目录读取现有 .md 文件反算(不维护重复名单);
  - 缺 model_card 即拒 → 必须先人工写卡、提交、通过评审,再启动晋级流程;
  - 在 promotion consume 前预检,不烧 grant(失败可修正重试)。

无墙钟、无随机、不 import 网络/券商 SDK。
"""
from __future__ import annotations

import os
from pathlib import Path


class ModelCardMissing(RuntimeError):
    """Model card 不存在 → promotion 前置 guard 拒绝。"""

    def __init__(self, strategy_id: str, expected_path: str):
        super().__init__(
            f"Model card missing for strategy '{strategy_id}'. "
            f"Expected at: {expected_path}"
        )
        self.strategy_id = strategy_id
        self.expected_path = expected_path


def verify_model_card_exists(strategy_id: str, *, base_dir: str | None = None) -> str:
    """Fail-closed 预检:model_card 文件必须存在,返回绝对路径;缺失抛 ModelCardMissing。

    Args:
        strategy_id: 策略 ID(如 "crowd_contra")。
        base_dir: repo 根目录(None = 从 __file__ 反推;可注入测试路径)。

    Returns:
        model_card 的绝对路径(已验证存在)。

    Raises:
        ModelCardMissing: 文件不存在。
    """
    if base_dir is None:
        # services/alpha/model_card.py → 向上三级到 repo root
        base_dir = str(Path(__file__).resolve().parents[2])

    model_card_path = os.path.join(base_dir, "model_cards", f"{strategy_id}.md")
    if not os.path.isfile(model_card_path):
        raise ModelCardMissing(strategy_id, model_card_path)
    return model_card_path


def list_documented_strategies(*, base_dir: str | None = None) -> frozenset[str]:
    """返回所有已有 model_card 的策略 ID(从 model_cards/*.md 文件名反算白名单)。

    Args:
        base_dir: repo 根目录(None = 从 __file__ 反推)。

    Returns:
        frozenset 策略 ID(如 {"crowd_contra", "carry_oi_majors"})。
    """
    if base_dir is None:
        base_dir = str(Path(__file__).resolve().parents[2])
    model_cards_dir = os.path.join(base_dir, "model_cards")
    if not os.path.isdir(model_cards_dir):
        return frozenset()
    files = [
        f[:-3]
        for f in os.listdir(model_cards_dir)
        if f.endswith(".md") and os.path.isfile(os.path.join(model_cards_dir, f))
    ]
    return frozenset(files)
