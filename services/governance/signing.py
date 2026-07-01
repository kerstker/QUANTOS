"""出进程签名权威接缝(轨道3 子项目3)—— L2_DRY_RUN_ONLY,一寸不让。

单进程内挡不住受信代码绕过(它能重算无密钥哈希、凑 Actor、直接调 manager、读同进程密钥)——这是硬事实。
真防御只能在进程外:一个持私钥的签名权威跑在研究进程够不到的独立信任域(post-L2、人工门控)。本模块把这个
边界变成有形、会拒绝、可机器钉死的接缝:进程内无密钥、NullSigningAuthority.sign() 即拒、verify_signed_ref
在 L2 恒 False。**不建真跨进程签名器、不持任何密钥材料、不接进任何执行路径。** 真出进程权威 = post-L2 = 非目标。

详见 docs/PHASE_14_PROCESS_ISOLATION.md(威胁面 + 架构 + post-L2 迁移路径)。
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class SigningUnavailable(PermissionError):
    pass


class SignedDecisionRef(BaseModel):
    """一个出进程权威签过的决策/批准引用长什么样——纯数据,本身不含也不产生密钥。"""
    model_config = ConfigDict(frozen=True)
    payload_hash: str = Field(min_length=1)   # 被签内容的哈希(如 approval grant / risk 决策的内容哈希)
    signature: str = Field(min_length=1)      # 出进程权威产出的签名(L2 下无从产生)
    key_id: str = Field(min_length=1)         # 签名所用密钥标识(公钥可验证,私钥在进程外)
    issued_by: str = Field(min_length=1)      # 签发权威身份(独立信任域)


@runtime_checkable
class SigningAuthority(Protocol):
    """出进程签名权威接缝。真实现 = post-L2、独立进程/信任域、持私钥;研究进程顶多拿公钥验证。"""
    is_out_of_process: bool
    key_id: str

    def sign(self, payload_hash: str) -> SignedDecisionRef: ...


class NullSigningAuthority:
    """L2 默认 —— 结构性拒绝的空壳,无密钥、不能签。sign() 恒 raise。绝不 import 密钥/HSM/网络 SDK。"""
    is_out_of_process = False
    key_id = ""

    def sign(self, payload_hash: str) -> SignedDecisionRef:
        raise SigningUnavailable(
            "no out-of-process signing authority at L2_DRY_RUN_ONLY — live-authorizing "
            "signatures require post-L2 process isolation (Phase 14).")


def verify_signed_ref(ref: Optional[SignedDecisionRef], payload_hash: str,
                      authority: Optional[SigningAuthority]) -> bool:
    """fail-closed。L2 下永远 False:无出进程权威(NullSigningAuthority.is_out_of_process=False / authority=None)。
    即便受信代码伪造一个 is_out_of_process=True 的权威,也过不去——真公钥验签是 post-L2 才有的能力,此处结构性拒绝。"""
    try:
        if authority is None or not getattr(authority, "is_out_of_process", False):
            return False
        if ref is None or not payload_hash or ref.payload_hash != payload_hash:
            return False
        # 走到这里意味着存在一个"出进程权威"——那是 post-L2 才有的东西。L2 下无真验签能力,结构性拒绝。
        raise SigningUnavailable("out-of-process signature verification is a post-L2 capability")
    except Exception:
        return False
