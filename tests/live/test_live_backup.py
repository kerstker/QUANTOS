"""tools/live_backup.py —— 灾备(2026-07-05,机构审计 P0)。

真钱决策的唯一证据(账本/审计链/mandate/证据钟)全部单机单盘,磁盘损坏 = 永久丢失。
本模块只备份**业务数据 + 配置**(账本/审计 db、对账/权益 jsonl、状态 json、mandate json),
**刻意不碰交易所 API key 文件**——密钥的备份/轮换是用户自己的安全实践(与全库"密钥只看
名字/长度、绝不记录值"的既有边界一致,复制密钥到第二个位置是需要用户自己决定的安全权衡,
不该由工具自动做)。
"""
import os

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
