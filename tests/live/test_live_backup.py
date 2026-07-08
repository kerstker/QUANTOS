"""tools/live_backup.py —— 灾备(2026-07-05,机构审计 P0)。

真钱决策的唯一证据(账本/审计链/mandate/证据钟)全部单机单盘,磁盘损坏 = 永久丢失。
本模块只备份**业务数据 + 配置**(账本/审计 db、对账/权益 jsonl、状态 json、mandate json),
**刻意不碰交易所 API key 文件**——密钥的备份/轮换是用户自己的安全实践(与全库"密钥只看
名字/长度、绝不记录值"的既有边界一致,复制密钥到第二个位置是需要用户自己决定的安全权衡,
不该由工具自动做)。
"""
import json
import os
import sys

import pytest

from tools.live_backup import build_backup_manifest


def test_manifest_includes_ledgers_and_audit_and_mandate(tmp_path):
    cache = tmp_path / "cache"
    key = tmp_path / "key"
    cache.mkdir()
    key.mkdir()
    (cache / "live_track.db").write_text("x")
    (cache / "live_audit.db").write_text("x")
    (cache / "live_parity.jsonl").write_text("x")
    (cache / "live_equity.jsonl").write_text("x")
    (cache / "live_track_status.json").write_text("x")
    (key / "live_mandate.json").write_text("x")
    manifest = build_backup_manifest(str(cache), str(key))
    names = {os.path.basename(p) for p in manifest}
    assert {"live_track.db", "live_audit.db", "live_parity.jsonl", "live_equity.jsonl",
           "live_track_status.json", "live_mandate.json"} <= names


def test_manifest_never_includes_api_key_files(tmp_path):
    cache = tmp_path / "cache"
    key = tmp_path / "key"
    cache.mkdir()
    key.mkdir()
    (key / "binance_test_key.json").write_text('{"api_key": "x", "api_secret": "y"}')
    (key / "bybit_demo_key.json").write_text('{"api_key": "x", "api_secret": "y"}')
    (key / "live_mandate.json").write_text("x")
    manifest = build_backup_manifest(str(cache), str(key))
    names = {os.path.basename(p) for p in manifest}
    assert "binance_test_key.json" not in names
    assert "bybit_demo_key.json" not in names
    assert "live_mandate.json" in names


def test_manifest_covers_per_strategy_status_files(tmp_path):
    # 2026-07-06 战线③(评审 #6):非默认策略的 status 落
    # live_track_status{suffix}_{strategy}.json —— 备份 glob "live_track_status*.json"
    # 必须覆盖它,这里钉死(不许它只是"碰巧匹配")
    cache = tmp_path / "cache"
    key = tmp_path / "key"
    cache.mkdir()
    key.mkdir()
    (cache / "live_track_status_stratX.json").write_text("x")
    (cache / "live_track_status_bybit_demo_stratX.json").write_text("x")
    names = {os.path.basename(p) for p in build_backup_manifest(str(cache), str(key))}
    assert {"live_track_status_stratX.json",
            "live_track_status_bybit_demo_stratX.json"} <= names


def test_manifest_skips_nonexistent_files_honestly(tmp_path):
    cache = tmp_path / "cache"
    key = tmp_path / "key"
    cache.mkdir()
    key.mkdir()
    manifest = build_backup_manifest(str(cache), str(key))
    assert manifest == []


def test_manifest_template_file_is_excluded_since_it_is_not_a_real_mandate(tmp_path):
    cache = tmp_path / "cache"
    key = tmp_path / "key"
    cache.mkdir()
    key.mkdir()
    (key / "live_mandate.json.template").write_text("x")
    manifest = build_backup_manifest(str(cache), str(key))
    assert manifest == []


# ---------------------------------------------------------------------------
# 2026-07-08 P1(全维度审计):灾备明文暴露修补。
# shutil.copy2 明文复制 live_track*.db / live_audit*.db / live_mandate*.json ——
# 真实交易台账 + 生效限额落到 data/backup/ 不加密。以下测试钉死:有密码就是真加密
# (AES-256-GCM,密钥由 PBKDF2-HMAC-SHA256 派生)、同密码可逐字节还原、错密码/篡改
# 明确失败(不是返回垃圾)、没有 cryptography 库时清晰报错(不是静默退化成明文)。
# ---------------------------------------------------------------------------
from tools.live_backup import (  # noqa: E402
    BackupDecryptionError,
    BackupEncryptionUnavailable,
    BackupPassphraseTooWeak,
    decrypt_bytes,
    decrypt_file,
    encrypt_bytes,
    encrypt_file,
)


def test_encrypt_decrypt_bytes_roundtrip_is_byte_identical():
    original = b'{"max_gross_notional": 999888.77, "marker": "SUPER_SECRET_MANDATE_MARKER"}'
    blob = encrypt_bytes(original, "correct horse battery staple")
    assert decrypt_bytes(blob, "correct horse battery staple") == original


def test_encrypted_bytes_do_not_contain_known_plaintext_marker():
    marker = b"SUPER_SECRET_MANDATE_MARKER_998877"
    original = b'{"max_gross_notional": 250000.0, "marker": "' + marker + b'"}'
    blob = encrypt_bytes(original, "hunter2-passphrase")
    # 直接读加密产物的字节流,已知敏感明文字符串必须一个都看不到
    assert marker not in blob
    assert b"max_gross_notional" not in blob
    assert b"250000" not in blob


def test_decrypt_with_wrong_passphrase_fails_clearly_not_garbage():
    blob = encrypt_bytes(b"top secret live limits and ledger data", "right-passphrase")
    with pytest.raises(BackupDecryptionError):
        decrypt_bytes(blob, "wrong-passphrase")


def test_decrypt_tampered_ciphertext_fails_clearly_not_garbage():
    blob = bytearray(encrypt_bytes(b"top secret live limits and ledger data", "tamper-test-passphrase"))
    blob[-1] ^= 0xFF  # 篡改密文/GCM tag 的最后一字节
    with pytest.raises(BackupDecryptionError):
        decrypt_bytes(bytes(blob), "tamper-test-passphrase")


def test_decrypt_garbage_input_fails_clearly_not_garbage():
    with pytest.raises(BackupDecryptionError):
        decrypt_bytes(b"not-a-real-backup-blob-at-all", "pw")


def test_encrypt_without_cryptography_library_raises_actionable_error(monkeypatch):
    # 模拟"这台机器没装 cryptography"——不能静默退化成明文,必须清晰报错说明需要什么。
    import tools.live_backup as live_backup

    def _boom():
        raise ImportError("simulated: cryptography not installed")

    monkeypatch.setattr(live_backup, "_import_aesgcm", _boom)
    with pytest.raises(BackupEncryptionUnavailable, match="cryptography"):
        encrypt_bytes(b"data", "pw")


# ---------------------------------------------------------------------------
# 2026-07-08 对抗式审查修补:弱/空白 passphrase 在原语层(encrypt_bytes)也要被拒绝——
# 不能只依赖 CLI 策略网关。空格、单字符、11 字符都不可接受;12 字符是最低门槛。
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("weak", ["", " ", "   ", "x", "short-pass"])  # "short-pass" == 10 chars < 12
def test_encrypt_bytes_rejects_blank_or_too_short_passphrase(weak):
    with pytest.raises(BackupPassphraseTooWeak):
        encrypt_bytes(b"data", weak)


def test_encrypt_bytes_accepts_passphrase_at_minimum_length():
    # 恰好 12 字符(strip 后)应当被接受,不应误伤边界值。
    passphrase = "x" * 12
    blob = encrypt_bytes(b"data", passphrase)
    assert decrypt_bytes(blob, passphrase) == b"data"


def test_decrypt_non_invalidtag_exception_propagates_and_is_not_masked(monkeypatch):
    # 非认证失败的异常(比如库签名变了导致的 TypeError,代表本模块自身的编程错误)
    # 不该被 decrypt_bytes 伪装成"密码错误/数据篡改"——必须原样向上传播。
    import tools.live_backup as live_backup

    blob = encrypt_bytes(b"payload", "sufficiently-long-passphrase")

    class _BoomAESGCM:
        def __init__(self, key):
            pass

        def decrypt(self, nonce, ciphertext, associated_data):
            raise TypeError("simulated internal programming error, not an auth failure")

    monkeypatch.setattr(live_backup, "_import_aesgcm", lambda: _BoomAESGCM)
    with pytest.raises(TypeError):
        decrypt_bytes(blob, "sufficiently-long-passphrase")


def test_encrypt_file_then_decrypt_file_roundtrip_is_byte_identical(tmp_path):
    src = tmp_path / "live_mandate.json"
    original_bytes = b'{"max_gross_notional": 123456.0, "venue": "binance"}'
    src.write_bytes(original_bytes)

    enc = tmp_path / "live_mandate.json.enc"
    encrypt_file(str(src), str(enc), "passphrase-123")

    # 加密产物落盘后,直接读字节流看不到原始限额数字
    assert b"123456" not in enc.read_bytes()

    restored = tmp_path / "restored_live_mandate.json"
    decrypt_file(str(enc), str(restored), "passphrase-123")
    assert restored.read_bytes() == original_bytes


def test_policy_refuses_to_run_when_no_passphrase_and_no_explicit_optout():
    import tools.live_backup as live_backup

    with pytest.raises(SystemExit):
        live_backup._check_encryption_policy(None, False)
    with pytest.raises(SystemExit):
        live_backup._check_encryption_policy("", False)  # 空字符串也算"没配密码"


def test_policy_allows_when_passphrase_is_set():
    import tools.live_backup as live_backup

    live_backup._check_encryption_policy("some-real-passphrase", False)  # 不应抛异常


def test_policy_allows_when_plaintext_explicitly_opted_out():
    import tools.live_backup as live_backup

    live_backup._check_encryption_policy(None, True)  # 不应抛异常——显式选择过


# ---------------------------------------------------------------------------
# 2026-07-08 对抗式审查修补:单个空格/极短密码此前会被 `not passphrase` 判定为"已设置"
# 而静默放行,产出一个几乎不设防的 .enc 文件。这里钉死策略网关把它们当"没设密码"一样拒绝。
# ---------------------------------------------------------------------------
def test_policy_refuses_whitespace_only_passphrase_even_without_optout():
    import tools.live_backup as live_backup

    with pytest.raises(SystemExit):
        live_backup._check_encryption_policy(" ", False)


def test_policy_refuses_too_short_passphrase():
    import tools.live_backup as live_backup

    with pytest.raises(SystemExit):
        live_backup._check_encryption_policy("short", False)  # 5 chars < 12


# ---------------------------------------------------------------------------
# main() 端到端接线测试。全部通过 monkeypatch 把 _CACHE_DIR/_KEY_DIR/--dest 重定向到
# tmp_path,绝不触碰真实 data/cache/——即便 refuse 分支提前退出前有只读 glob 扫描,
# 扫的也是重定向后的临时目录。
# ---------------------------------------------------------------------------
def test_main_writes_encrypted_dot_enc_files_when_passphrase_set(tmp_path, monkeypatch):
    import tools.live_backup as live_backup

    cache = tmp_path / "cache"
    key = tmp_path / "key"
    dest_root = tmp_path / "backup"
    status_path = tmp_path / "status" / "backup_status.json"
    cache.mkdir()
    key.mkdir()
    marker = "MARKER_ABC123_SENSITIVE_LIMIT"
    (cache / "live_track.db").write_text(f"ledger contents {marker}")
    (key / "live_mandate.json").write_text(f'{{"max_gross_notional": 500000, "tag": "{marker}"}}')

    monkeypatch.setattr(live_backup, "_CACHE_DIR", str(cache))
    monkeypatch.setattr(live_backup, "_KEY_DIR", str(key))
    monkeypatch.setattr(live_backup, "_STATUS_PATH", str(status_path))
    monkeypatch.setenv("QUANTOS_BACKUP_PASSPHRASE", "integration-test-passphrase")
    monkeypatch.setattr(sys, "argv", ["live_backup", "--dest", str(dest_root)])

    live_backup.main()

    subdirs = list(dest_root.iterdir())
    assert len(subdirs) == 1
    out_files = list(subdirs[0].iterdir())
    names = {p.name for p in out_files}
    assert names == {"live_track.db.enc", "live_mandate.json.enc"}
    for p in out_files:
        assert marker.encode() not in p.read_bytes()

    restored = tmp_path / "restored_mandate.json"
    live_backup.decrypt_file(
        str(subdirs[0] / "live_mandate.json.enc"), str(restored), "integration-test-passphrase")
    assert marker in restored.read_text()

    # 2026-07-08 code review 修复:main() 必须在成功运行后写出死人开关新鲜度标记
    # (data/cache/backup_status.json 的 ran_at),供 tools/watchdog.py 检测这条计划任务
    # 是否还在按预期节奏运行(register_schedules.ps1 的 QUANTOS_backup 任务此前静默失败
    # 完全无人知晓——见该脚本 2026-07-08 修补的注释)。
    status = json.loads(status_path.read_text())
    assert isinstance(status["ran_at"], int) and status["ran_at"] > 0
    assert status["file_count"] == 2
    assert status["encrypted"] is True


def test_main_raises_and_leaves_no_plaintext_when_crypto_unavailable_with_passphrase_set(
    tmp_path, monkeypatch
):
    # 2026-07-08 对抗式审查修补:模块 docstring/main() 内联注释反复强调的核心属性——
    # "设了密码但 cryptography 不可 import 时,必须让整次备份失败,绝不悄悄退回明文"——
    # 此前只在 encrypt_bytes 层单测覆盖,main() 端到端从未被钉死。这里补上。
    import tools.live_backup as live_backup

    cache = tmp_path / "cache"
    key = tmp_path / "key"
    dest_root = tmp_path / "backup"
    cache.mkdir()
    key.mkdir()
    (cache / "live_track.db").write_text("ledger contents that must never leak as plaintext")

    monkeypatch.setattr(live_backup, "_CACHE_DIR", str(cache))
    monkeypatch.setattr(live_backup, "_KEY_DIR", str(key))
    monkeypatch.setenv("QUANTOS_BACKUP_PASSPHRASE", "integration-test-passphrase")
    monkeypatch.setattr(sys, "argv", ["live_backup", "--dest", str(dest_root)])

    def _boom():
        raise ImportError("simulated: cryptography not installed")

    monkeypatch.setattr(live_backup, "_import_aesgcm", _boom)

    with pytest.raises(live_backup.BackupEncryptionUnavailable):
        live_backup.main()

    # 核心不变量:即便目标目录已被创建(os.makedirs 在加密之前跑),也不能有任何明文
    # (无 .enc 后缀)的源文件副本被写出——不加密必须是显式选择,不能是失败后的兜底。
    if dest_root.exists():
        for root, _dirs, files in os.walk(str(dest_root)):
            for name in files:
                assert name.endswith(".enc"), f"plaintext file leaked: {os.path.join(root, name)}"


def test_main_refuses_when_no_passphrase_and_no_optout_and_writes_nothing(tmp_path, monkeypatch):
    import tools.live_backup as live_backup

    cache = tmp_path / "cache"
    key = tmp_path / "key"
    dest_root = tmp_path / "backup"
    status_path = tmp_path / "status" / "backup_status.json"
    cache.mkdir()
    key.mkdir()
    (cache / "live_track.db").write_text("should never be backed up plaintext by default")

    monkeypatch.setattr(live_backup, "_CACHE_DIR", str(cache))
    monkeypatch.setattr(live_backup, "_KEY_DIR", str(key))
    monkeypatch.setattr(live_backup, "_STATUS_PATH", str(status_path))
    monkeypatch.delenv("QUANTOS_BACKUP_PASSPHRASE", raising=False)
    monkeypatch.setattr(sys, "argv", ["live_backup", "--dest", str(dest_root)])

    with pytest.raises(SystemExit):
        live_backup.main()
    assert not dest_root.exists()  # 拒绝执行,连目标目录都不该被创建
    # 2026-07-08 code review 修复:fail-closed 拒绝路径绝不写"运行成功"的新鲜度标记——
    # 否则 tools/watchdog.py 会被一个从未真正完成过备份的 ran_at 骗过去,永远不报警。
    assert not status_path.exists()


def test_main_allows_plaintext_backup_with_explicit_optout(tmp_path, monkeypatch):
    import tools.live_backup as live_backup

    cache = tmp_path / "cache"
    key = tmp_path / "key"
    dest_root = tmp_path / "backup"
    status_path = tmp_path / "status" / "backup_status.json"
    cache.mkdir()
    key.mkdir()
    (cache / "live_track.db").write_text("plaintext ok, this run explicitly opted out")

    monkeypatch.setattr(live_backup, "_CACHE_DIR", str(cache))
    monkeypatch.setattr(live_backup, "_KEY_DIR", str(key))
    monkeypatch.setattr(live_backup, "_STATUS_PATH", str(status_path))
    monkeypatch.delenv("QUANTOS_BACKUP_PASSPHRASE", raising=False)
    monkeypatch.setattr(sys, "argv", ["live_backup", "--dest", str(dest_root), "--allow-plaintext"])

    live_backup.main()

    subdirs = list(dest_root.iterdir())
    out_file = subdirs[0] / "live_track.db"
    assert out_file.exists()  # 未加密、无 .enc 后缀——显式选择的旧行为
    assert out_file.read_text() == "plaintext ok, this run explicitly opted out"

    status = json.loads(status_path.read_text())
    assert status["encrypted"] is False  # 明文 opt-out 也如实记录 encrypted=False
