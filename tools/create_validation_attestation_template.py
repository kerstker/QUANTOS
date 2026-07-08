"""生成 `key/live_validation_attestations.json.template`(审计 P1-1)。

验证证明门(live/governance/validation_attestation.py)要求每个将上真钱的 strategy_id 在案有 PASS
验证或**可审计人工 override**。override 必须由**真人签署**——这个工具只生成一个**模板**,所有需要真人
填写的字段都放不可用的占位符;`attested_at` 故意填成**非整数字符串**,使得万一有人把模板原样复制成真实
文件(去掉 .template),`load_attestations` 会 raise → 驱动器 fail-closed 全部 BLOCK,绝不因误用而放行。

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


def build_template() -> dict:
    return {
        "crowd_contra": {
            "kind": "human_override",
            # 真人 actor_id —— 换成你的真实名字/标识
            "attested_by": "REPLACE_WITH_YOUR_REAL_NAME",
            # epoch 毫秒(整数)—— 换成签署时刻;此处故意是非整数字符串,原样复制会 fail-closed
            "attested_at": "REPLACE_WITH_EPOCH_MS_INTEGER",
            # 为什么允许这个策略动真钱(会进审计留痕)—— 换成你的真实理由
            "reason": "REPLACE_WITH_WHY (e.g. crowd_contra: IC panel t=4.32, dry-run VT sharpe 1.38 / "
                      "maxdd 12%, passed 4/5 falsification checks, >=8wk forward evidence + human review)",
        },
    }


def main() -> None:
    os.makedirs("key", exist_ok=True)
    with open(_TEMPLATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(build_template(), fh, indent=2, sort_keys=True)
    print(f"wrote {_TEMPLATE_PATH}", flush=True)
    print("下一步(真人操作,agent 不代做):", flush=True)
    print(f"  1) 复制 -> key/live_validation_attestations.json", flush=True)
    print("  2) 把 REPLACE_WITH_* 占位符换成你的真实名字 / epoch 毫秒整数 / 理由", flush=True)
    print("  3) 之前 --submit 会被 fail-closed BLOCK(no_validation_attestation);填好后才放行", flush=True)


if __name__ == "__main__":
    main()
