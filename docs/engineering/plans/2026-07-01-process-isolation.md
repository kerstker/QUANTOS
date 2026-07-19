# 出进程签名权威 + Phase 14 进程隔离 实现计划(轨道3 子项目3)

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 造一个 L2 安全的、结构性拒绝的签名接缝(`services/governance/signing.py`)+ 一份 Phase 14 进程隔离威胁面/架构文档,把「真防御只能在进程外」变成有形、可机器验证的制品。

**Architecture:** 新扁平模块 `services/governance/signing.py` —— `SignedDecisionRef`(纯值)+ `SigningAuthority`(Protocol)+ `NullSigningAuthority`(L2 默认,进程内无密钥、`sign()` 即拒)+ `verify_signed_ref`(fail-closed,L2 恒 False)。配 `docs/PHASE_14_PROCESS_ISOLATION.md` 威胁面文档 + 治理测试。

**Tech Stack:** Python 3、pydantic v2(frozen)、pytest。无第三方新依赖,**不 import 任何密钥/HSM/网络 SDK**。

## Global Constraints

- L2_DRY_RUN_ONLY 一寸不让:五个 LIVE 标志不动;**进程内不持任何密钥材料**;**不建真跨进程签名器**;**不接进任何执行/门路径**;不弱化任何现有门;不改现有 `verify_ref` 的内容绑定行为。
- `signing.py` **不 import** 券商/CTP/FIX/网络/HSM/密钥 SDK;无墙钟/无随机/无密钥字面量。
- **不进** `research_runner` 白名单(不是 research kind)。
- 诚实边界:`verify_signed_ref` 在 L2 恒 False,绝不制造「已防护」假象;真出进程签名器/真进程隔离部署 = post-L2 = 显式非目标。不把进程内任何东西冒充成对受信代码的防御。

---

### Task PI1: 签名接缝模块(值 + 拒绝桩 + fail-closed 校验)

**Files:**
- Create: `services/governance/signing.py`
- Test: `tests/governance/test_signing.py`

**Interfaces:**
- Produces: `SigningUnavailable(PermissionError)`;`SignedDecisionRef`(frozen:`payload_hash`/`signature`/`key_id`/`issued_by`);`SigningAuthority`(Protocol, runtime_checkable:`is_out_of_process:bool`/`key_id:str`/`sign(payload_hash)->SignedDecisionRef`);`NullSigningAuthority`(`is_out_of_process=False`、`sign()` raise);`verify_signed_ref(ref, payload_hash, authority) -> bool`。

- [ ] **Step 1: Write the failing test**

```python
# tests/governance/test_signing.py
"""出进程签名权威接缝单元测试(轨道3 子项目3)。L2 下:签不出、验不过。"""
import pytest

from services.governance.signing import (NullSigningAuthority, SignedDecisionRef,
                                          SigningAuthority, SigningUnavailable,
                                          verify_signed_ref)


def test_signed_decision_ref_is_frozen_value():
    ref = SignedDecisionRef(payload_hash="ph", signature="sig", key_id="k1", issued_by="authority-x")
    assert ref.payload_hash == "ph"
    with pytest.raises(Exception):        # frozen
        ref.signature = "other"


def test_null_authority_conforms_to_protocol_but_refuses_to_sign():
    a = NullSigningAuthority()
    assert isinstance(a, SigningAuthority)         # runtime_checkable 结构一致
    assert a.is_out_of_process is False
    with pytest.raises(SigningUnavailable):
        a.sign("payload-hash")


def test_verify_is_fail_closed_without_out_of_process_authority():
    ref = SignedDecisionRef(payload_hash="ph", signature="sig", key_id="k1", issued_by="x")
    assert verify_signed_ref(ref, "ph", None) is False                      # 无权威
    assert verify_signed_ref(ref, "ph", NullSigningAuthority()) is False     # 非出进程权威
    assert verify_signed_ref(None, "ph", NullSigningAuthority()) is False    # 无 ref


def test_verify_refuses_even_a_spoofed_out_of_process_authority():
    # 受信代码即便伪造一个 is_out_of_process=True 的权威,也过不去(L2 无真验签能力)——fail-closed
    class SpoofAuthority:
        is_out_of_process = True
        key_id = "spoof"
        def sign(self, payload_hash):        # pragma: no cover
            return SignedDecisionRef(payload_hash=payload_hash, signature="x", key_id="spoof", issued_by="spoof")
    ref = SignedDecisionRef(payload_hash="ph", signature="x", key_id="spoof", issued_by="spoof")
    assert verify_signed_ref(ref, "ph", SpoofAuthority()) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_signing.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.governance.signing'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/governance/signing.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_signing.py -q`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/governance/signing.py tests/governance/test_signing.py
git commit -m "feat(governance): 出进程签名权威接缝(SignedDecisionRef + NullSigningAuthority 拒绝 + verify fail-closed)"
```

---

### Task PI2: Phase 14 进程隔离威胁面/架构文档

**Files:**
- Create: `docs/PHASE_14_PROCESS_ISOLATION.md`

**Interfaces:** 无代码接口;这是 repo 内的威胁面/架构文档(子项目3 的主交付)。

- [ ] **Step 1: 写文档**

创建 `docs/PHASE_14_PROCESS_ISOLATION.md`,须含以下小节(用中文,代码标识符英文):

1. **标题 + L2 免责**:本文描述 post-L2 目标架构;L2 下只落一个拒绝式接缝(`services/governance/signing.py`),不建真跨进程签名器。
2. **核心事实**:单进程内挡不住受信代码(能重算无密钥哈希、凑 `HUMAN` `Actor`、直接调 manager/catalog、读同进程密钥)。真防御只能在进程外。
3. **受信代码可绕过面 × 出进程签名权威怎么堵(表)**,逐行列:
   - `ExecutionBoundary.verify_ref`(无密钥内容哈希,受信代码可重算)→ admit 前要求经出进程权威验证的 `SignedDecisionRef`。
   - `approval.verify_multi_party_grant`(受信代码可凑三个 `HUMAN` `Actor`)→ grant 内容哈希须由出进程权威签发。
   - `PreTradeRiskGate.issue_ref`(内容绑定非密码学证明)→ 决策内容哈希由出进程权威签,执行进程独立验签。
   - `AlphaLifecycleManager`/`KillSwitch`/`catalog._set_state` 直接调用(受信代码可绕 chokepoint)→ 状态迁移的授权 ref 须出进程签发。
   - `assert_dry_run_adapter`(运行时防误用,非信任边界)→ 换真适配器的授权须出进程签发 + 人工门控。
4. **架构(文字图)**:研究进程(无私钥,顶多公钥)↔ 独立签名权威进程(持私钥,人工门控,可能 HSM)↔ 执行进程(admit 前验签)。三进程/三信任域,私钥永不进研究进程。
5. **post-L2 迁移路径**:`ExecutionBoundary.admit(..., signed_ref)` 在 readiness>L2 时额外要求 `verify_signed_ref(signed_ref, payload_hash, pubkey_authority)`;`payload_hash` = approval grant / risk 决策的内容哈希。
6. **为何 L2 不接线**:L2 无真权威 → 强接要么死代码、要么堵死能跑的干跑流水线,两者都不诚实。故 L2 下签名接缝是独立的拒绝式制品,与 `LiveBrokerAdapter`(也不接 live 路径、只拒绝)对称。
7. **与轨道3 前两子项目的关系**:子项目1(券商接缝)、子项目2(多方批准)在单进程内把*运行时误用*挡住;子项目3 画出唯一能挡*受信代码*的那层(出进程),但明确它是 post-L2。三者合起来 = 通往实盘的完整安全脚手架骨架。

- [ ] **Step 2: 提交**

```bash
git add docs/PHASE_14_PROCESS_ISOLATION.md
git commit -m "docs: Phase 14 进程隔离威胁面/架构文档(受信代码绕过面 × 出进程签名权威怎么堵)"
```

---

### Task PI3: 治理边界钉死 + 全门绿 + 收口文档

**Files:**
- Create: `tests/governance/test_signing_boundary.py`
- Modify: `CLAUDE.md`、`docs/PROJECT_STATE.md`、记忆 `track3-live-scaffold.md`

**Interfaces:**
- Consumes: PI1 的 `signing.py`。

- [ ] **Step 1: Write the failing test**

```python
# tests/governance/test_signing_boundary.py
"""治理:机器钉死签名接缝的 L2 边界。
1) signing.py 不 import 券商/网络/HSM/密钥 SDK;
2) signing.py 无嵌入密钥材料(无 PEM 头、无 *_KEY/SECRET 字面量赋值);
3) NullSigningAuthority.sign() 在 L2 raise;verify_signed_ref 无出进程权威时 fail-closed。"""
import ast
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MOD = os.path.join(_ROOT, "services", "governance", "signing.py")

_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
_NETWORK_ROOTS = {"urllib", "socket", "requests", "httpx", "aiohttp", "websocket", "websockets",
                  "ftplib", "telnetlib", "http"}
# 密钥/HSM 库:L2 下签名接缝不该碰真密码学密钥材料
_KEY_HSM_ROOTS = {"pkcs11", "hsm", "cryptography", "nacl", "Crypto", "keyring", "boto3"}


def _import_roots(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            roots.add(n.module.split(".")[0])
    return roots


def test_signing_imports_no_sdk_network_or_key_lib():
    roots = _import_roots(_MOD)
    for bad, label in ((_FORBIDDEN_SDK, "broker SDK"), (_NETWORK_ROOTS, "network"),
                       (_KEY_HSM_ROOTS, "key/HSM lib")):
        assert not (roots & bad), f"FORBIDDEN {label} in signing.py: {sorted(roots & bad)}"


def test_signing_has_no_embedded_key_material():
    src = open(_MOD, encoding="utf-8").read()
    assert "-----BEGIN" not in src                                   # 无 PEM 密钥头
    # 无形如 PRIVATE_KEY = "..." / SECRET_KEY = '...' / API_KEY = "..." 的密钥字面量赋值
    assert not re.search(r'(PRIVATE_KEY|SECRET_KEY|API_KEY|PRIVATE_SECRET)\s*=\s*["\']', src)


def test_l2_cannot_sign_or_verify():
    from services.governance.signing import (NullSigningAuthority, SignedDecisionRef,
                                             SigningUnavailable, verify_signed_ref)
    with pytest.raises(SigningUnavailable):
        NullSigningAuthority().sign("ph")
    ref = SignedDecisionRef(payload_hash="ph", signature="s", key_id="k", issued_by="x")
    assert verify_signed_ref(ref, "ph", NullSigningAuthority()) is False
    assert verify_signed_ref(ref, "ph", None) is False
```

- [ ] **Step 2: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_signing_boundary.py -q`
Expected: PASS(3 passed)

- [ ] **Step 3: 全门绿**

Run: `PYTHONPATH=. python scripts/validate.py`
Expected: 末尾 `VALIDATE PASSED (fail-closed gate green).`
若失败:读报错、修根因、重跑,直到绿。

- [ ] **Step 4: 收口文档 + 记忆**

- `CLAUDE.md`:在 `services/governance/approval.py` 条目后加 `services/governance/signing.py` 条目(拒绝式签名接缝 + 不接 L2 执行路径 + 指向 `docs/PHASE_14_PROCESS_ISOLATION.md`);把「轨道3 剩余」改为 **轨道3 三子项目全 landed(接缝 ✓ / 多方批准 ✓ / 进程隔离设计 ✓)**。
- `docs/PROJECT_STATE.md`:轨道3 段落更新——子项目3 landed,轨道3 收口。
- 记忆 `track3-live-scaffold.md`:子项目3 PENDING→DONE;description 更新为三子项目全完成。

- [ ] **Step 5: Commit**

```bash
git add tests/governance/test_signing_boundary.py CLAUDE.md docs/PROJECT_STATE.md
git commit -m "test(governance)+docs: 签名接缝 L2 边界机器钉死(无密钥/SDK、L2 签不出验不过)+ 轨道3 收口"
```

---

## 自审(Self-Review)

- **Spec coverage:** spec 四块 ↔ 计划三任务(PI1 = 值/接缝/拒绝/校验三块合一;PI2 = 威胁面文档;PI3 = 治理测试 + 门 + 收口),诚实边界在 Global Constraints + PI1 docstring + PI2 文档 §6 + PI3 治理测试四处落地。
- **Placeholder scan:** 无 TBD/TODO;PI1 完整代码,PI2 文档结构逐节点名,PI3 完整测试。
- **Type consistency:** `SignedDecisionRef`(4 字段)、`SigningAuthority`(is_out_of_process/key_id/sign)、`NullSigningAuthority`、`verify_signed_ref(ref, payload_hash, authority)` 在 PI1 定义与 PI1/PI3 测试引用吻合;`SigningUnavailable` 全程一致。
- **不接执行路径核对:** 计划不改 `ExecutionBoundary`/`gate`/干跑流水线;PI2 §5/§6 把接线明确划为 post-L2 + 说明 L2 不接线的理由。
- **诚实核对:** `verify_signed_ref` L2 恒 False(含伪造出进程权威也 False),不制造已防护假象;治理测试钉死无密钥材料。
