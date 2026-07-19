"""Live 部署前的验证证明门(审计 P1-1)。设计:docs/audit/RISK_REGISTER.md P1-1。

回答的问题:一个 strategy_id 凭什么可以进入会动真钱的 live 提交路径?它必须**在案**证明自己要么
(a) 通过了形式化验证门(一条 PASS ValidationResult,以其复现哈希引用),要么 (b) 有一条**可审计的
人工 override**(某个真人 attested_by + 理由 + 时间)。二者皆无 → fail-closed 拒绝真提交,即便
`--submit` + 已签 mandate。

这是 PreTradeRiskGate(风控**许可**)/ mandate(分级**授权**)之外、更靠前的一道**策略级准入门**:
风控门问"这笔订单安不安全",mandate 问"这批要不要人工在场",本门问"这个策略压根有没有资格上真钱"。
现部署的 crowd_contra 从未走过形式化验证门(靠 IC 面板/干跑/现实主义等诊断 + 人工判断选出),所以
它需要走 (b) 人工 override——由真人签署,不是代码/agent 能自签的。

诚实边界(与全库同一威胁模型注脚):override 的"人工"是自报 `attested_by` 标签(与 mandate
`approved_by`、promotion `X-Operator` 同性质),真身份认证/出进程签名是 Phase 14。本门结构性挡下的是
"没有任何在案凭证就上真钱";它挡不住受信进程内代码伪造一条 attestation——那是 Phase 14 进程隔离的范畴。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping, Optional

VALIDATION_PASS = "validation_pass"
HUMAN_OVERRIDE = "human_override"
_KINDS = (VALIDATION_PASS, HUMAN_OVERRIDE)
_REQUIRED_FIELDS = ("kind", "attested_by", "attested_at")


class ValidationAttestationError(ValueError):
    """加载证明文件时的致命错误(格式/字段问题),必须向上抛,不能静默吞成'没有凭证'——
    一条格式坏掉的凭证可能本该是承重的,静默丢弃会把'坏'误当成'无'。"""


@dataclass(frozen=True)
class ValidationAttestation:
    strategy_id: str
    kind: str                        # validation_pass | human_override
    attested_by: str                 # 签署的人工 actor_id(留痕;非身份认证,见模块 docstring)
    attested_at: int
    reason: str = ""                 # human_override 必填:为什么允许这个策略上真钱
    validation_result_hash: str = ""  # validation_pass 必填:引用的 ValidationResult 复现哈希


def require_validation_attestation(strategy_id: str,
                                   attestations: Optional[Mapping[str, ValidationAttestation]]
                                   ) -> Optional[str]:
    """fail-closed 准入判断。返回 None = 放行(该策略有合法在案凭证);返回原因字符串 = 拒绝。
    任何缺失/未知种类/未签名/缺必填证据 → 拒绝;任何异常 → 拒绝(绝不因为求值出错而放行)。"""
    try:
        att = attestations.get(strategy_id) if attestations else None
        if att is None:
            return f"no_validation_attestation:{strategy_id}"
        if att.strategy_id != strategy_id:
            return f"attestation_strategy_mismatch:{att.strategy_id}:{strategy_id}"
        if att.kind not in _KINDS:
            return f"unknown_attestation_kind:{att.kind}"
        if not att.attested_by:
            return f"attestation_unsigned:{strategy_id}"
        if att.kind == VALIDATION_PASS and not att.validation_result_hash:
            return f"validation_pass_without_result_hash:{strategy_id}"
        if att.kind == HUMAN_OVERRIDE and not att.reason:
            return f"human_override_without_reason:{strategy_id}"
        return None
    except Exception as exc:                       # fail-closed:求值异常一律拒绝
        return f"attestation_error:{type(exc).__name__}"


def load_attestations(path: str) -> dict:
    """从 JSON 文件加载 {strategy_id: ValidationAttestation}。fail-closed:文件缺失 → {}(空字典
    使 require 对每个策略都拒绝,等价于'全部升人工');存在但格式非法/条目缺必填字段 → raise
    `ValidationAttestationError`(不静默跳过一条可能承重的凭证——'坏'不能被当成'无')。

    文件形状(每个 strategy_id 一条):
    {
      "crowd_contra": {"kind": "human_override", "attested_by": "<真人>", "attested_at": <ms>,
                       "reason": "<为什么允许上真钱>"}
    }
    """
    if not os.path.exists(path):
        return {}
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        raise ValidationAttestationError(f"attestation file unreadable: {type(exc).__name__}") from None
    if not isinstance(raw, dict):
        raise ValidationAttestationError("attestation file must be a JSON object {strategy_id: {...}}")
    out: dict = {}
    for sid, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValidationAttestationError(f"attestation for {sid!r} must be an object")
        for field in _REQUIRED_FIELDS:
            if field not in entry:
                raise ValidationAttestationError(f"attestation for {sid!r} missing required field {field!r}")
        try:
            at = int(entry["attested_at"])
        except (TypeError, ValueError):
            raise ValidationAttestationError(f"attestation for {sid!r} has non-integer attested_at") from None
        out[sid] = ValidationAttestation(
            strategy_id=sid, kind=str(entry["kind"]), attested_by=str(entry["attested_by"]),
            attested_at=at, reason=str(entry.get("reason", "")),
            validation_result_hash=str(entry.get("validation_result_hash", "")))
    return out
