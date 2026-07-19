# 出进程签名权威 + Phase 14 进程隔离 设计(轨道3 子项目3)

> L2_DRY_RUN_ONLY,一寸不让。本子项目只造**设计文档 + 一个结构性拒绝的签名接缝**,把「真防御只能在
> 进程外」这句话变成有形、可机器验证的制品——但那个制品本身仍是 L2 合规的:**进程内不持任何密钥材料、
> 不建真跨进程签名器、不接进任何执行路径、不动五个 LIVE 标志**。

## 1. 背景:反复出现的同一句诚实注脚

QUANTOS 全代码库里,几个安全面都带着**同一句**诚实注脚——"单进程内挡不住受信代码":

- `services/execution/boundary.py::ExecutionBoundary.verify_ref` —— ref 是**无密钥**内容哈希,
  "NOT a trust boundary against TRUSTED in-process code, which could recompute it … deferred to
  **out-of-process signed approval/decision refs** before any move beyond L2"。
- `services/risk/pre_trade/gate.py::issue_ref/verify_ref` —— "content binding, NOT a cryptographic
  proof … an HMAC-signed token that the EXECUTION boundary independently verifies is Phase 10/11 …
  an in-process caller that bypasses the gate is out of this layer's scope"。
- `services/trading/broker_adapter.py::assert_dry_run_adapter` —— 守卫是运行时防误用,**非**受信代码信任边界。
- `services/governance/approval.py::verify_multi_party_grant` —— 强化的是*批准的构造路径*,单进程内挡不住
  受信代码凑三个 `HUMAN` `Actor` 绕过。
- `CLAUDE.md` 威胁模型 —— "a single process cannot defend against TRUSTED CODE that constructs a HUMAN
  Actor, calls managers directly … That build-time surface is governed by code review, the governance
  scanner, and **(Phase 14) deployment/process isolation**"。

**子项目3 就是这句注脚点名的那个 Phase 14。** 它是轨道3 的收口:一次性回答"这些'受信代码可绕过'的面,
真防御长什么样"。

## 2. 核心事实(为什么只能是设计 + 拒绝式接缝)

**单进程内,你根本挡不住受信代码。** 受信代码能重算无密钥哈希、能构造 `HUMAN` `Actor`、能直接调
manager/catalog、能读到同进程里的任何密钥。这是硬事实,代码库已反复陈述。所以真防御**只能在进程外**:
一个持有密钥的**签名权威**跑在研究进程够不到的独立进程/信任域(post-L2、人工门控、可能 HSM),研究进程
**顶多**拿到验证用的公钥、拿不到签名能力。

在 L2 我们**绝不**建这个真跨进程签名器(那是 post-L2)。所以本子项目诚实的形态是:

1. **设计/威胁面文档** —— 出进程签名权威的架构 + 它怎么逐个堵上第 1 节那些面 + post-L2 迁移路径。
2. **一个 L2 安全的拒绝式签名接缝** —— 把"签名权威"这个边界变成有形的、会拒绝的、可机器钉死的制品
   (进程内无密钥、`sign()` 即拒、`verify` fail-closed),和 `LiveBrokerAdapter`、approval 的拒绝完全对称。

## 3. 诚实边界(非目标)

- **不接进 L2 的执行路径。** 关键决定:L2 下没有真权威,若把"必须签名"强行接进 `ExecutionBoundary.admit`
  或干跑流水线,要么是**死代码**,要么把**能跑的干跑流水线全堵死**——两者都不诚实。所以签名接缝是个*独立的、
  可测的、会拒绝的*制品 + 描述 post-L2 接线的文档。与 `LiveBrokerAdapter`(也不接进任何 live 路径、只拒绝)对称。
- **不持任何密钥材料**:`signing.py` 里没有硬编码密钥/秘密常量;不 import HSM/密钥库/网络 SDK。
- 不建真跨进程签名器、不动五个 LIVE 标志、不弱化任何现有门、不改现有 `verify_ref` 的内容绑定行为(它在 L2 照常工作)。
- 不做前端、不做 console 端点、不做真进程隔离部署(那是 post-L2 运维)。

## 4. 架构(方案 1:签名接缝 + 威胁面文档)

新建**扁平模块** `services/governance/signing.py`(和 `approval.py` 同域——都是「谁有权授权」的信任权威)。
核心是纯值 + 拒绝桩 + fail-closed 校验,**无墙钟、无随机、无密钥**。

### 4.1 值对象与异常(第 1 块)

```python
class SigningUnavailable(PermissionError):
    pass

class SignedDecisionRef(BaseModel):          # frozen
    payload_hash: str = Field(min_length=1)  # 被签内容的哈希(如某个 approval grant / risk 决策的内容哈希)
    signature: str = Field(min_length=1)     # 出进程权威产出的签名(L2 下无从产生)
    key_id: str = Field(min_length=1)        # 签名所用密钥标识(公钥可验证,私钥在进程外)
    issued_by: str = Field(min_length=1)     # 签发权威的身份(独立信任域)
```

`SignedDecisionRef` 是**纯数据**——它只描述"一个出进程权威签过的引用长什么样",本身不含也不产生密钥。

### 4.2 接缝与结构性拒绝(第 2 块)

```python
@runtime_checkable
class SigningAuthority(Protocol):
    """出进程签名权威接缝。真实现 = post-L2、独立进程/信任域、持私钥;研究进程顶多拿公钥验证。"""
    is_out_of_process: bool
    key_id: str
    def sign(self, payload_hash: str) -> SignedDecisionRef: ...

class NullSigningAuthority:
    """L2 默认 —— 结构性拒绝的空壳,无密钥、不能签。sign() 恒 raise。绝不 import 密钥/HSM/网络 SDK。
    真出进程权威 = post-L2、人工门控 = 显式非目标。"""
    is_out_of_process = False
    key_id = ""
    def sign(self, payload_hash: str) -> SignedDecisionRef:
        raise SigningUnavailable(
            "no out-of-process signing authority at L2_DRY_RUN_ONLY — live-authorizing "
            "signatures require post-L2 process isolation (Phase 14).")
```

### 4.3 fail-closed 校验(第 3 块)

```python
def verify_signed_ref(ref: Optional[SignedDecisionRef], payload_hash: str,
                      authority: Optional[SigningAuthority]) -> bool:
    """fail-closed:唯一(未来)放行条件——authority 是出进程权威 ∧ ref 绑定到 payload_hash ∧ 签名经权威公钥验证。
    L2 下 authority 为 None 或 NullSigningAuthority(非出进程)→ 恒 False。任何异常 → False。"""
```

L2 下永远走不到"签名验证"那一步:没有出进程权威 → `is_out_of_process` 为 False / authority 为 None → 直接 False。
（真正的公钥验签逻辑是 post-L2 的事;此处只把"无权威即拒"钉死为 fail-closed。）

### 4.4 威胁面文档 + 治理测试(第 4 块)

- `docs/PHASE_14_PROCESS_ISOLATION.md` —— 一份 repo 内的威胁面/架构文档:
  - **表:每个"受信代码可绕过"的面**(第 1 节那 5 个)× **出进程签名权威怎么堵**。
  - **架构图(文字)**:研究进程(无私钥,顶多公钥)↔ 独立签名权威进程(持私钥,人工门控)↔ 执行进程
    (admit 前验签)。
  - **post-L2 迁移路径**:将来 `ExecutionBoundary.admit` 在 readiness>L2 时额外要求一个经出进程权威验证的
    `SignedDecisionRef`;approval grant / risk 决策的内容哈希作为 `payload_hash` 被签。**L2 下不接线**。
  - **为何 L2 不接线**(死代码 vs 堵死干跑,二者都不诚实)。
- `tests/governance/test_signing_boundary.py` —— 机器钉死:
  - `signing.py` import 根 ∩ 券商/网络/HSM SDK = ∅(复用 `_import_roots` AST 检查,`_FORBIDDEN_SDK` +
    `_NETWORK_ROOTS` + HSM/密钥根 `{pkcs11, hsm, cryptography, nacl, Crypto, keyring, boto3}`);
  - `signing.py` **无嵌入密钥材料**:源码里无形如私钥/秘密常量的赋值(启发式:无 `PRIVATE_KEY`/`SECRET`/
    `-----BEGIN` 字面量);
  - `NullSigningAuthority().sign("x")` raise `SigningUnavailable`;
  - `verify_signed_ref(任意 ref, "x", NullSigningAuthority())` 与 `verify_signed_ref(None, "x", None)` 均 False。

## 5. 数据流(L2 现状 vs post-L2 目标)

```
L2 现状(本子项目):
  authority = NullSigningAuthority()          # 进程内、无密钥
  authority.sign(payload_hash)                -> raise SigningUnavailable        # 签不出来
  verify_signed_ref(any_ref, payload_hash, authority) -> False                   # 无出进程权威,fail-closed
  # 不接进 ExecutionBoundary / 干跑流水线 —— 独立制品

post-L2 目标(仅文档描述,本子项目不建):
  研究进程 --payload_hash--> [独立签名权威进程,持私钥,人工门控] --SignedDecisionRef--> 执行进程
  ExecutionBoundary.admit(..., signed_ref) 额外要求 verify_signed_ref(signed_ref, payload_hash, pubkey_authority)
```

## 6. 错误处理与安全约束(全 fail-closed)

- `NullSigningAuthority.sign` 恒 raise(L2 下签不出任何东西)。
- `verify_signed_ref` 任何不满足或任何异常 → False(绝不"出错=放行")。
- `signing.py` 无墙钟/无随机/无密钥材料;只依赖 `quantos.contracts`(hashing/BaseModel)与 stdlib。
- **不进** `research_runner` 白名单;**不接进**任何执行/门路径。

## 7. 文件清单

- 新建 `services/governance/signing.py`(值对象 + 接缝 + 拒绝桩 + fail-closed 校验)。
- 新建 `docs/PHASE_14_PROCESS_ISOLATION.md`(威胁面/架构/迁移路径文档)。
- 新建 `tests/governance/test_signing_boundary.py`(无密钥材料 + 无 SDK/网络/HSM import + sign 拒绝 + verify fail-closed)。
- 新建 `tests/governance/test_signing.py`(值对象/拒绝/校验单元)。
- 改 `CLAUDE.md`、`docs/PROJECT_STATE.md`、记忆 [[track3-live-scaffold]]:子项目3 landed → 轨道3 收口。

## 8. 自审(spec self-review)

- **占位符扫描:** 无 TBD/TODO;每块给了签名与不变量。
- **内部一致:** "不接进 L2 执行路径"在第 2/3/4/5 节一致;拒绝式接缝与 `LiveBrokerAdapter`/approval 的对称性一致。
- **范围:** 单一实现计划可覆盖(一个模块 + 一份文档 + 两个测试);真跨进程签名器、真进程隔离部署显式划到范围外。
- **歧义:** 「进程内无密钥材料」= 治理测试用启发式钉死源码无私钥/秘密字面量——已在 4.4 写明判据。
- **诚实核对:** 不把进程内的任何东西冒充成对受信代码的防御;`verify_signed_ref` 在 L2 恒 False,不制造"已防护"的假象。
