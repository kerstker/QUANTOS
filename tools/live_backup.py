"""灾备(2026-07-05,机构审计 P0;2026-07-08 补 P1:静态加密)。

真钱决策的唯一证据(账本/审计链/mandate/证据钟)全部单机单盘——磁盘损坏 = 永久丢失,
DR 计划此前甚至不存在。本工具把业务数据 + 配置(不含 API 密钥,见 `build_backup_manifest`
docstring)复制到独立的时间戳备份目录。用法:

    python -m tools.live_backup                       # 备份到 data/backup/<timestamp>/
                                                        # (需先 export QUANTOS_BACKUP_PASSPHRASE=...)
    python -m tools.live_backup --dest D:/quantos-backup   # 备份到其它盘(真正的灾备该指向
                                                        # 不同物理磁盘,同盘备份只防文件损坏)
    python -m tools.live_backup --allow-plaintext      # 显式选择不加密(明文落盘,不推荐,
                                                        # 仅用于调试/无加密库环境的临时兜底)

P1 修补背景:此前 `shutil.copy2` 明文复制 `live_track*.db`(完整交易台账)/`live_audit*.db`/
`live_mandate*.json`(真实生效限额)到 `data/backup/<timestamp>/`,`register_schedules.ps1`
每日 03:00 自动跑、零加密——若备份目标是可移动介质或被云同步覆盖,暴露面比想象中大。

加密方案:AES-256-GCM(认证加密,篡改/错密码=解密报错,不是返回垃圾数据)+
PBKDF2-HMAC-SHA256(200,000 轮,标准库 `hashlib`)派生密钥,逐文件独立 salt/nonce。
只用 `cryptography` 包的 `AESGCM` 原语(Python 标准库没有 AES)。2026-07-08 code review 修复
前,`cryptography` 不是 QUANTOS 项目声明的依赖(不在 requirements.txt/pyproject.toml 里),只是
恰好装在这台机器的 conda 环境里(因 anaconda-auth/Scrapy/pyOpenSSL 等无关工具间接需要)——换一台
干净部署机器会在这个 fail-closed 密码闸门通过后、`encrypt_file` 内部才发现装不了,同样导致整次
备份失败退出。现已作为显式依赖声明在 `requirements.txt` / `pyproject.toml`(`live-backup` 可选组)
的 `cryptography==49.0.0`。本模块**从不静默降级成明文**:
没装 `cryptography` 时 `encrypt_bytes`/`decrypt_bytes` 清晰报错(`BackupEncryptionUnavailable`),
不会假装加密成功。密码来源只认环境变量 `QUANTOS_BACKUP_PASSPHRASE`(不硬编码、不发明新的
密钥管理机制);未设置该变量时默认**拒绝执行**(fail-closed),除非显式传 `--allow-plaintext`
主动选择不加密——"不加密"必须是显式选择,不能是默认/静默行为。密码同样有最低强度门槛
(非空白、strip 后 ≥12 字符,见 `_MIN_PASSPHRASE_LEN`);空白/极短密码会被 CLI 策略网关
和 `encrypt_bytes` 原语层双重拒绝(fail-closed),不会悄悄产出一个形同虚设的 `.enc` 文件。

还原:`tools.live_backup.decrypt_file(src_enc_path, dest_path, passphrase)` /
`decrypt_bytes(blob, passphrase)`——本次修补只做加密落盘,专用的还原 CLI 不在范围内
(如需要请另提)。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import sys
import time

_CACHE_DIR = os.path.join("data", "cache")
_KEY_DIR = "key"
# 死人开关新鲜度标记(2026-07-08 code review 修复):此前没有任何监控知道"备份是否成功/新鲜"
# ——tools/watchdog.py 只跟踪 forward_track/live_track/risk_sentinel,完全不知道 backup 这回事;
# QUANTOS_backup 计划任务也没把 stdout/stderr 落日志,静默失败(比如下面这道 fail-closed 密码
# 闸门拒绝执行)只体现在 Task Scheduler 的"上次运行结果"列,没人主动看就发现不了。main() 在每次
# **完成**运行(含"无事可备份"的诚实空跑)时写这个文件;fail-closed 拒绝路径(`_check_encryption_
# policy` 抛 `SystemExit`)刻意不写它——让 `tools/watchdog.py` 能像监督其它任务一样,靠 `ran_at`
# 过期检测出"这条计划任务已经连续多天没有成功跑过"。
_STATUS_PATH = os.path.join("data", "cache", "backup_status.json")

_PASSPHRASE_ENV = "QUANTOS_BACKUP_PASSPHRASE"
_ALLOW_PLAINTEXT_FLAG = "--allow-plaintext"
_MIN_PASSPHRASE_LEN = 12  # PBKDF2 200,000 轮救不回一个空白/极短密码——低于此长度一律当作没设密码。

# 加密备份文件格式:MAGIC(4B) + salt(16B) + nonce(12B) + AES-GCM 密文(含 16B tag)。
# 逐文件独立 salt + nonce——每个 .enc 文件自包含,不依赖同批次其它文件就能独立解密。
_MAGIC = b"QBK1"
_PBKDF2_ITERATIONS = 200_000
_SALT_LEN = 16
_NONCE_LEN = 12
_KEY_LEN = 32  # AES-256


class BackupEncryptionUnavailable(RuntimeError):
    """密码已配置,但加密原语不可用(`cryptography` 未安装/不可 import)。
    刻意不在这里静默退化成明文——调用方(main）应该让整次备份失败,而不是悄悄写明文。"""


class BackupDecryptionError(RuntimeError):
    """解密失败:密码错误、数据被篡改/损坏、或不是本模块产出的加密格式。
    AES-GCM 是认证加密,这三种情况都会在这里被挡住,不会返回看似合法的垃圾明文。"""


class BackupPassphraseTooWeak(RuntimeError):
    """密码非空,但空白/短于 `_MIN_PASSPHRASE_LEN` 字符——本质上约等于没有密码,
    PBKDF2 200,000 轮救不回一个单字符/空白密码的熵。在原语层(`encrypt_bytes`)和
    CLI 策略网关(`_check_encryption_policy`)两处都校验:原语层校验是纵深防御,
    不依赖调用方已经把关,未来任何直接调用 `encrypt_bytes`/`encrypt_file` 的新调用方
    (例如尚未实现的还原 CLI)同样会被挡住,不会悄悄产出一个形同虚设的 `.enc` 文件。"""


def _check_passphrase_strength(passphrase) -> None:
    """两处复用的最低强度校验。空白(仅空格等)或 strip 后短于 `_MIN_PASSPHRASE_LEN`
    字符 → 一律当作"没有密码"处理,fail-closed。"""
    if passphrase is None or not passphrase.strip() or len(passphrase.strip()) < _MIN_PASSPHRASE_LEN:
        raise BackupPassphraseTooWeak(
            "backup passphrase is blank/whitespace-only or shorter than "
            f"{_MIN_PASSPHRASE_LEN} characters after stripping — this is effectively no "
            "password at all (200,000 PBKDF2 iterations cannot rescue a low-entropy secret "
            "from brute force). Use a longer, non-blank passphrase."
        )


def _import_aesgcm():
    """真实 import,拆成独立函数只是为了方便测试用 monkeypatch 模拟"未装 cryptography"。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM


def _require_aesgcm():
    try:
        return _import_aesgcm()
    except ImportError as exc:
        raise BackupEncryptionUnavailable(
            f"{_PASSPHRASE_ENV} is set (encryption requested) but the 'cryptography' package "
            "is not installed/importable in this Python environment — refusing to silently "
            "fall back to a plaintext backup. Install it explicitly (pip install cryptography) "
            f"or rerun with {_ALLOW_PLAINTEXT_FLAG} to knowingly opt into an unencrypted backup."
        ) from exc


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_KEY_LEN)


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    """AES-256-GCM 加密。返回自包含 blob(含随机 salt/nonce),可直接落盘。
    纵深防御:即便调用方(CLI 策略网关)漏过了弱密码,这里也会拒绝空白/过短密码——
    绝不悄悄产出一个"看起来加密了"但形同虚设的 blob。"""
    aesgcm_cls = _require_aesgcm()
    _check_passphrase_strength(passphrase)
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(passphrase, salt)
    ciphertext = aesgcm_cls(key).encrypt(nonce, data, None)
    return _MAGIC + salt + nonce + ciphertext


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    """`encrypt_bytes` 的逆运算。密码错/数据损坏/不是本格式 → `BackupDecryptionError`
    (fail-closed:绝不返回猜出来的垃圾字节)。"""
    aesgcm_cls = _require_aesgcm()
    header_len = len(_MAGIC) + _SALT_LEN + _NONCE_LEN
    if len(blob) < header_len or not blob.startswith(_MAGIC):
        raise BackupDecryptionError(
            "not a recognized QUANTOS encrypted backup blob (bad magic header / too short)")
    salt = blob[len(_MAGIC):len(_MAGIC) + _SALT_LEN]
    nonce = blob[len(_MAGIC) + _SALT_LEN:header_len]
    ciphertext = blob[header_len:]
    key = _derive_key(passphrase, salt)
    # 局部 import(而不是模块顶层):`cryptography` 不是本项目的声明依赖(见模块 docstring)。
    # 走到这里说明 `_require_aesgcm()` 已经证明它可 import,这个 import 不会再引入新的
    # ImportError 风险。只窄收 InvalidTag(AES-GCM 认证失败 = 密码错/数据被篡改),
    # 其它异常类型(签名变了、内部编程错误等)原样向上抛,不会被误伪装成"密码错误"。
    from cryptography.exceptions import InvalidTag
    try:
        return aesgcm_cls(key).decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise BackupDecryptionError(
            "failed to decrypt backup data — wrong passphrase, or the data is corrupted/tampered"
        ) from exc


def encrypt_file(src_path: str, dest_path: str, passphrase: str) -> None:
    """读 `src_path`,加密,把 blob 写到 `dest_path`。用于备份的每个源文件。"""
    with open(src_path, "rb") as fh:
        data = fh.read()
    blob = encrypt_bytes(data, passphrase)
    with open(dest_path, "wb") as fh:
        fh.write(blob)


def decrypt_file(src_path: str, dest_path: str, passphrase: str) -> None:
    """`encrypt_file` 的逆运算——还原用(目前无专用 restore CLI,直接调用本函数)。"""
    with open(src_path, "rb") as fh:
        blob = fh.read()
    data = decrypt_bytes(blob, passphrase)
    with open(dest_path, "wb") as fh:
        fh.write(data)


def _check_encryption_policy(passphrase, allow_plaintext: bool) -> None:
    """fail-closed 闸门:没配密码 + 没显式选择明文 → 直接拒绝执行,绝不静默退化成明文备份。
    "不加密"必须是显式选择(--allow-plaintext),不能是默认/静默行为。同样 fail-closed 地
    拒绝空白/过短密码——一次运维手滑把环境变量设成空格/极短字符串,不该被静默放行、
    产出一个看起来加密了但形同虚设的 .enc 文件。"""
    if not passphrase and not allow_plaintext:
        raise SystemExit(
            f"refusing to run: {_PASSPHRASE_ENV} is not set. Backups contain live trading "
            "ledgers/audit trail/mandate limits and must be encrypted by default. Either set "
            f"the passphrase env var to encrypt, or rerun with {_ALLOW_PLAINTEXT_FLAG} to "
            "explicitly opt into an unencrypted backup (not recommended)."
        )
    if passphrase:
        try:
            _check_passphrase_strength(passphrase)
        except BackupPassphraseTooWeak as exc:
            raise SystemExit(str(exc)) from exc


# 业务数据 + 配置(不含密钥)。用前缀/精确名匹配,不用"复制一切"式通配符——新文件类型加一行,
# 而不是意外把不相关的缓存(如研究快照)也拖进灾备,让备份体积/含义失焦。
_CACHE_PATTERNS = [
    "live_track*.db", "live_audit*.db", "live_parity*.jsonl", "live_equity*.jsonl",
    "live_track_status*.json", "live_risk_state*.json", "live_day_state*.json",
    "risk_sentinel_status.json", "forward_track_status.json", "paper_track.db",
    "watchdog_status.json", "gonogo_report.json",
]
# key/ 目录只备份 mandate(业务风险限额配置,非密钥);.template 文件本来就不含真实数字,
# 备份它没有意义。API key 文件(*_key.json)刻意不在这里——见模块 docstring。
_KEY_PATTERNS = ["live_mandate*.json"]


def build_backup_manifest(cache_dir: str, key_dir: str) -> list:
    """纯扫描(无副作用):返回需要备份的真实存在的文件绝对/相对路径列表。不存在的模式
    诚实跳过(不报错,一台新检出的机器本来就没有这些文件)。"""
    out = []
    for pattern in _CACHE_PATTERNS:
        out.extend(sorted(glob.glob(os.path.join(cache_dir, pattern))))
    for pattern in _KEY_PATTERNS:
        out.extend(p for p in sorted(glob.glob(os.path.join(key_dir, pattern)))
                  if not p.endswith(".template"))
    return out


def _write_status(ran_at_ms: int, *, file_count: int, encrypted: bool) -> None:
    """写 `_STATUS_PATH`——`tools/watchdog.py` 的 backup 新鲜度检查读这个文件的 `ran_at`。
    只应在 main() 真正完成一次运行(哪怕是"无事可备份"的空跑)后调用;fail-closed 拒绝路径
    绝不调用它,让过期的 `ran_at` 如实反映"这条计划任务已经多天没有成功跑过"。"""
    os.makedirs(os.path.dirname(_STATUS_PATH), exist_ok=True)
    with open(_STATUS_PATH, "w", encoding="utf-8") as fh:
        json.dump({"ran_at": ran_at_ms, "file_count": file_count, "encrypted": encrypted}, fh, indent=2)


def main() -> None:
    dest_root = os.path.join("data", "backup")
    if "--dest" in sys.argv:
        dest_root = sys.argv[sys.argv.index("--dest") + 1]
    passphrase = os.environ.get(_PASSPHRASE_ENV)
    allow_plaintext = _ALLOW_PLAINTEXT_FLAG in sys.argv
    # fail-closed gate FIRST: refuse before touching the filesystem (no dest dir created,
    # no status file written — see _STATUS_PATH docstring above) unless a passphrase is
    # configured or plaintext was explicitly opted into.
    _check_encryption_policy(passphrase, allow_plaintext)
    manifest = build_backup_manifest(_CACHE_DIR, _KEY_DIR)
    now_ms = int(time.time() * 1000)
    if not manifest:
        print("nothing to back up yet (no live-path artifacts found)", flush=True)
        _write_status(now_ms, file_count=0, encrypted=bool(passphrase))
        return
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = os.path.join(dest_root, stamp)
    os.makedirs(dest, exist_ok=True)
    for src in manifest:
        basename = os.path.basename(src)
        if passphrase:
            # encrypt_file raises BackupEncryptionUnavailable if `cryptography` isn't
            # importable — propagates and fails the whole run rather than silently
            # falling back to a plaintext copy.
            encrypt_file(src, os.path.join(dest, basename + ".enc"), passphrase)
        else:
            shutil.copy2(src, os.path.join(dest, basename))
    _write_status(now_ms, file_count=len(manifest), encrypted=bool(passphrase))
    print(f"backed up {len(manifest)} file(s) -> {dest} "
         f"({'encrypted' if passphrase else 'PLAINTEXT (--allow-plaintext)'})", flush=True)
    print("note: API key files are NOT included — key backup/rotation is your own "
         "security practice", flush=True)


if __name__ == "__main__":
    main()
