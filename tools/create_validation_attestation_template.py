"""生成 `key/live_validation_attestations.json.template`(审计 P1-1)。

验证证明门(live/governance/validation_attestation.py)要求每个将上真钱的 strategy_id 在案有 PASS
验证或**可审计人工 override**。当前旧回测/纸面盈利制品均为
`INVALIDATED_PERP_LEDGER_V1`，且没有通过独立信任根准入的
`authoritative_backtest_record`；文件存在、非空或有哈希都不能成为签署理由。
override 必须由**真人签署**——这个工具只生成一个**模板**,所有需要真人填写的字段都放不可用的占位符;
`attested_at` 故意填成**非整数字符串**,使得万一有人把模板原样复制成真实文件(去掉 .template),
`load_attestations` 会 raise → 驱动器 fail-closed 全部 BLOCK,绝不因误用而放行。

用法:
  python -m tools.create_validation_attestation_template
然后把 key/live_validation_attestations.json.template 复制成 key/live_validation_attestations.json,
把占位符换成你的真实名字 / epoch 毫秒 / 理由。agent 不代填这些——那是人工授权(见模块 docstring 的
威胁模型注脚:真身份认证是 Phase 14)。
"""
from __future__ import annotations

import json
import os

_TEMPLATE_PATH = os.path.join("key", "live_validation_attestations.json.template")
_BLOCKED_KIND = "blocked_missing_authoritative_backtest_record"
_AUTHORITY_BLOCK = (
    "DO_NOT_ATTEST: authoritative_backtest_record is not independently admitted; "
    "INVALIDATED_PERP_LEDGER_V1 retained artifacts and hashes are audit history only"
)


def build_template() -> dict:
    return {
        "crowd_contra": {
            "kind": _BLOCKED_KIND,
            # 真人 actor_id —— 换成你的真实名字/标识
            "attested_by": "REPLACE_WITH_YOUR_REAL_NAME",
            # epoch 毫秒(整数)—— 换成签署时刻;此处故意是非整数字符串,原样复制会 fail-closed
            "attested_at": "REPLACE_WITH_EPOCH_MS_INTEGER",
            # 为什么允许这个策略动真钱(会进审计留痕)—— 换成你的真实理由
            "reason": f"{_AUTHORITY_BLOCK}: crowd_contra",
        },
        "carry_oi_majors": {
            "kind": _BLOCKED_KIND,
            "attested_by": "REPLACE_WITH_YOUR_REAL_NAME",
            "attested_at": "REPLACE_WITH_EPOCH_MS_INTEGER",
            "reason": f"{_AUTHORITY_BLOCK}: carry_oi_majors",
        },
        "top_contra": {
            "kind": _BLOCKED_KIND,
            "attested_by": "REPLACE_WITH_YOUR_REAL_NAME",
            "attested_at": "REPLACE_WITH_EPOCH_MS_INTEGER",
            "reason": f"{_AUTHORITY_BLOCK}: top_contra",
        },
    }


def main() -> None:
    os.makedirs("key", exist_ok=True)
    with open(_TEMPLATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(build_template(), fh, indent=2, sort_keys=True)
    print(f"wrote {_TEMPLATE_PATH}", flush=True)
    print("下一步(真人操作,agent 不代做):", flush=True)
    print(f"  1) 复制 -> key/live_validation_attestations.json", flush=True)
    print("  2) 当前保持 DO_NOT_ATTEST；旧盈利制品不能作为理由", flush=True)
    print("  3) 仅在 authoritative_backtest_record 经独立权威准入后再进行人工审查", flush=True)


if __name__ == "__main__":
    main()
