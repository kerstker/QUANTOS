"""生成 mandate 文件模板(Stage 1.5,通往实盘任务清单 #34)。

Mandate 的真实数字(限额/白名单/签署人)必须由用户自己创建、自己签署——本工具只产出一个带
详细说明的模板骨架,所有风险数字都故意填成无法被 float() 解析的占位字符串。这样如果有人不
小心把模板原样复制成 key/live_mandate.json,`load_mandate` 会 fail-closed 报错,而不是悄悄
产出一个"看起来能用"、实际上是 AI 瞎编数字的 mandate——呼应 live/governance/mandate_store.py
既有的诚实边界(真实数字非本工具/非 AI 代填)。

`venue` 字段是例外——它不是风险数字,是结构性协议匹配字段,必须等于驱动器实际连接的
venue.venue_mode。当前 tools/live_track_advance.py 恒连 Binance 测试网,预填正确值只是省
一次用户排查"数字填对了为什么还是 NEEDS_HUMAN_REVIEW"的机会,不代表任何风险容忍度判断。
"""
from __future__ import annotations

import json
import os
import sys

_TEMPLATE_PATH = os.path.join("key", "live_mandate.json.template")

_PLACEHOLDER = "REPLACE_ME"
_PLACEHOLDER_NUMBER = "REPLACE_ME_WITH_A_REAL_NUMBER"


def build_template() -> dict:
    return {
        "_instructions": [
            "这是模板,不是真实 mandate——复制成 key/live_mandate.json 后自己填真实数字,"
            "再删掉这个 _instructions 字段(可选,留着也不影响加载)。",
            "所有 REPLACE_ME* 占位符必须被替换成真实值,否则 load_mandate 会 fail-closed 报错"
            "(这是故意的,防止模板被误当真实 mandate 加载)。",
            "venue 字段已预填 'testnet'(mandate.venue 必须等于 venue.venue_mode)、exchange"
            " 字段已预填 'binance'(mandate.exchange 必须等于适配器的 venue_name——两家交易所"
            "的 testnet 都叫 'testnet',不签交易所域会被另一家误认)。这两个是结构性协议匹配"
            "字段、非风险数字;驱动器用 --venue bybit 时要把 exchange 改成 'bybit'。",
            "allowed_instruments 用裸标的名(如 'BTC'),不是交易所合约符号 'BTCUSDT'——"
            "流水线内部统一裸名,适配器提交时才拼 USDT 后缀;填合约符号会导致全部订单被"
            " instrument_not_allowed 拒绝(fail-closed,但不是你想要的)。",
            "notional 都是名义额(quantity * price 的美元值),不是币的数量。",
            "issued_at/expires_at 是 epoch 毫秒;连续 supervisor 要求 expires_at 明确存在,"
            "不接受永久 mandate。",
            "risk_supervisor_policy 的每个 REPLACE_ME* 阈值也必须由真人签署;模板不提供"
            "任何业务默认值。policy_id/policy_version 会进入 supervisor 状态和审计哈希。",
            "approved_by 留真实签署人的名字/id,是审计留痕,不是任何权限校验的一部分。",
        ],
        "mandate_id": f"{_PLACEHOLDER}_e.g._mandate_2026_07_stage1",
        "venue": "testnet",
        "exchange": "binance",
        "allowed_instruments": [f"{_PLACEHOLDER}_e.g._BTC"],
        "max_single_order_notional": _PLACEHOLDER_NUMBER,
        "max_gross_notional": _PLACEHOLDER_NUMBER,
        "max_net_notional": _PLACEHOLDER_NUMBER,
        "per_instrument_max_notional": {
            f"{_PLACEHOLDER}_e.g._BTC": _PLACEHOLDER_NUMBER,
        },
        "approved_by": [f"{_PLACEHOLDER}_your_name_or_id"],
        "issued_at": f"{_PLACEHOLDER_NUMBER}_EPOCH_MS",
        "expires_at": f"{_PLACEHOLDER_NUMBER}_EPOCH_MS",
        "risk_supervisor_policy": {
            "schema_version": 1,
            "policy_id": f"{_PLACEHOLDER}_risk_policy_id",
            "policy_version": f"{_PLACEHOLDER}_risk_policy_version",
            "max_market_age_ms": _PLACEHOLDER_NUMBER,
            "max_scheduler_age_ms": _PLACEHOLDER_NUMBER,
            "max_drawdown_pct": _PLACEHOLDER_NUMBER,
            "min_margin_ratio": _PLACEHOLDER_NUMBER,
            "max_single_name_fraction": _PLACEHOLDER_NUMBER,
            "max_participation_rate": _PLACEHOLDER_NUMBER,
            "reconciliation_tolerance": _PLACEHOLDER_NUMBER,
        },
    }


def write_template(path: str, *, force: bool = False) -> bool:
    """写模板到 path。已存在且非 force 时跳过(不覆盖用户可能已经在编辑的文件),返回是否实际写入。"""
    if os.path.exists(path) and not force:
        return False
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(build_template(), fh, indent=2, ensure_ascii=False)
    return True


def main() -> None:
    force = "--force" in sys.argv
    written = write_template(_TEMPLATE_PATH, force=force)
    if not written:
        print(f"already exists, not overwriting: {_TEMPLATE_PATH} (use --force to regenerate)")
        return
    print(f"wrote template -> {_TEMPLATE_PATH}")
    print("next: copy it to key/live_mandate.json, fill in REAL numbers yourself "
         "(this tool will never do that for you), then re-run tools.live_track_advance --submit.")


if __name__ == "__main__":
    main()
