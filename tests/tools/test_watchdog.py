"""tools/watchdog.py —— 死人开关驱动器 main() 接线(2026-07-08 code review 修复)。

新增覆盖:每日灾备(tools/live_backup.py)新鲜度检查接进死人开关体系。修复前,watchdog 只
监督前向证据钟/双所周度驱动器/风险哨兵三类任务——QUANTOS_backup 这条计划任务静默失败(比如
2026-07-08 新加的 fail-closed 密码闸门拒绝执行)不会被任何东西发现。

全部用 `monkeypatch.chdir(tmp_path)` 隔离:main() 内部路径全是相对 "data/cache/..." 字符串,
chdir 后天然不会碰真实仓库的 data/cache/(也不依赖额外的路径 monkeypatch)。同时把 `_VENUES`
打成空字典,避免真实仓库 key/ 目录里的 testnet key 文件(或宿主机残留的 env var)让每个测试
环境的行为不一致——那部分不是本次要测的东西,只保留单一变量:backup 新鲜度检查本身。
"""
import json

import tools.watchdog as watchdog


def _write_ran_at(tmp_path, rel_path, ran_at_ms):
    p = tmp_path / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ran_at": ran_at_ms}), encoding="utf-8")


def _read_watchdog_status(tmp_path):
    return json.loads((tmp_path / "data" / "cache" / "watchdog_status.json").read_text(encoding="utf-8"))


def test_stale_backup_status_is_flagged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(watchdog, "_VENUES", {})
    import time
    now = int(time.time() * 1000)
    _write_ran_at(tmp_path, watchdog._FORWARD_TRACK_STATUS, now)
    _write_ran_at(tmp_path, watchdog._SENTINEL_STATUS, now)
    _write_ran_at(tmp_path, watchdog._BACKUP_STATUS, now - 20 * 86_400_000)  # 20 天前,远超 2 天容忍窗口

    watchdog.main()

    status = _read_watchdog_status(tmp_path)
    assert any("live_backup" in w for w in status["warnings"])


def test_fresh_backup_status_is_not_flagged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(watchdog, "_VENUES", {})
    import time
    now = int(time.time() * 1000)
    _write_ran_at(tmp_path, watchdog._FORWARD_TRACK_STATUS, now)
    _write_ran_at(tmp_path, watchdog._SENTINEL_STATUS, now)
    _write_ran_at(tmp_path, watchdog._BACKUP_STATUS, now)  # 刚跑过

    watchdog.main()

    status = _read_watchdog_status(tmp_path)
    assert not any("live_backup" in w for w in status["warnings"])


def test_missing_backup_status_is_flagged_as_never_ran(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(watchdog, "_VENUES", {})
    import time
    now = int(time.time() * 1000)
    _write_ran_at(tmp_path, watchdog._FORWARD_TRACK_STATUS, now)
    _write_ran_at(tmp_path, watchdog._SENTINEL_STATUS, now)
    # backup_status.json 干脆不存在 —— 模拟"这台机器上这条计划任务从没成功跑过一次"
    # (比如 register_schedules.ps1 注册的密码闸门拒绝了每一次运行)

    watchdog.main()

    status = _read_watchdog_status(tmp_path)
    assert any("live_backup" in w for w in status["warnings"])
