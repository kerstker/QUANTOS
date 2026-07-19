"""死人开关驱动器(2026-07-05,机构审计 P0;2026-07-08 code review 修复补 backup 覆盖)。

检查每个应该定期运行的调度任务(前向证据钟、双所周度驱动器、风险哨兵、每日灾备)最后一次
成功运行的时间戳,过期未运行(scheduler 静默失败——机器休眠/任务被系统重启打断/依赖缺失)
就大声告警。本身也应该被调度(建议每小时,与风险哨兵同频)——是"谁来监督监督者"这个问题的
答案:一个独立、极简、只读的检查,不依赖它要检查的那些任务本身还活着。

用法:python -m tools.watchdog
"""
from __future__ import annotations

import json
import os
import time

from live.execution.watchdog_check import check_staleness
from tools.alert_sink import emit
from tools.live_track_advance import _STRATEGIES, _STRATEGY_ID, _VENUES, _venue_paths

_FORWARD_TRACK_STATUS = os.path.join("data", "cache", "forward_track_status.json")
_SENTINEL_STATUS = os.path.join("data", "cache", "risk_sentinel_status.json")
# 2026-07-08 code review 修复:此前 QUANTOS_backup(每日灾备,见 tools/live_backup.py +
# register_schedules.ps1)完全不在死人开关的监督范围内——它静默失败(比如 fail-closed 密码
# 闸门拒绝执行)会一直没人发现。tools/live_backup.py::main() 在每次完成运行(含空跑)时写这个
# ran_at 标记,fail-closed 拒绝路径刻意不写,所以过期的 ran_at 如实反映"已经多天没有成功备份"。
_BACKUP_STATUS = os.path.join("data", "cache", "backup_status.json")

# 每个任务的调度节奏 + 容忍窗口(工程默认,非业务风险数字):周度任务给 9 天缓冲(错过一次
# 还能补跑),哨兵给 3 小时(每小时调度,容忍一次失败 + 重试窗口),每日灾备给 2 天(错过一次
# 还能补跑,同 WEEKLY 的"缓冲 > 调度间隔"惯例)。
_WEEKLY_MAX_AGE_MS = 9 * 86_400_000
_HOURLY_MAX_AGE_MS = 3 * 3600_000
_DAILY_MAX_AGE_MS = 2 * 86_400_000


def _ran_at(path: str) -> "int | None":
    if not os.path.exists(path):
        return None
    try:
        data = json.load(open(path, encoding="utf-8"))
        return data.get("ran_at")
    except (json.JSONDecodeError, OSError):
        return None


def live_track_status_targets(venue_name: str, suffix: str) -> list:
    """per-(venue, **已调度** strategy) 的 status 新鲜度目标(2026-07-06 战线③,评审 #10)。
    默认策略沿历史路径、恒被期待(venue 已配置即应每周运行);非默认策略只有在其状态文件
    已存在(= 真的被调度运行过)时才进入期待集——注册表回答"谁能跑",不回答"谁被调度了",
    按注册表全集期待会对"注册了但只在别的 venue 调度"的策略永久误报。代价诚实记录在案:
    非默认策略被调度过一次后彻底下线,其遗留状态文件会持续告警,直到人工删除该文件
    (这是特性——静默消失的调度正是死人开关要抓的)。返回 [(label, path), ...]。"""
    targets = [(f"live_track_advance[{venue_name}]", _venue_paths(suffix)["status"])]
    for strat in sorted(_STRATEGIES):
        if strat == _STRATEGY_ID:
            continue                                     # 默认策略走历史路径,上面已覆盖
        path = _venue_paths(suffix, strategy_id=strat)["status"]
        if os.path.exists(path):
            targets.append((f"live_track_advance[{venue_name}:{strat}]", path))
    return targets


def main() -> None:
    now = int(time.time() * 1000)
    warnings = []

    warnings.append(check_staleness(_ran_at(_FORWARD_TRACK_STATUS), now=now,
                                    max_age_ms=_WEEKLY_MAX_AGE_MS, label="forward_track_advance"))
    for name, cfg in _VENUES.items():
        if not (os.path.exists(cfg["key_file"]) or os.environ.get(cfg["env"][0])):
            continue                                     # 未配置的 venue 不该被期待运行
        for label, path in live_track_status_targets(name, cfg["suffix"]):
            warnings.append(check_staleness(_ran_at(path), now=now,
                                            max_age_ms=_WEEKLY_MAX_AGE_MS, label=label))
    warnings.append(check_staleness(_ran_at(_SENTINEL_STATUS), now=now,
                                    max_age_ms=_HOURLY_MAX_AGE_MS, label="live_risk_sentinel"))
    warnings.append(check_staleness(_ran_at(_BACKUP_STATUS), now=now,
                                    max_age_ms=_DAILY_MAX_AGE_MS, label="live_backup"))

    fired = [w for w in warnings if w is not None]
    if fired:
        emit(fired, context="watchdog", now_ms=now)
    status_path = os.path.join("data", "cache", "watchdog_status.json")
    json.dump({"ran_at": now, "warnings": fired}, open(status_path, "w", encoding="utf-8"), indent=2)
    for w in fired:
        print(w, flush=True)
    if not fired:
        print("all scheduled jobs are within their expected cadence", flush=True)
    print(f"-> {status_path}", flush=True)


if __name__ == "__main__":
    main()
