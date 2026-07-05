"""原子文件状态落盘(2026-07-06,安全审查 punch-list 修复)。

风险哨兵(小时级)与周度驱动器共享同一批状态文件(peak_equity/day_state),此前用
`json.dump(obj, open(path, "w"))` 直接写——非原子操作,进程在写一半时被杀(OS kill/断电)
会留下截断的 JSON,下次加载 `JSONDecodeError` -> 现有代码 fail-open(悄悄当成"无历史状态"
重置),而不是 fail-closed。

修复:写临时文件 + `os.replace`(POSIX/Windows 均为原子重命名)——要么整份新内容落地、要么
原文件保持不变,不存在"写一半"的中间态。并发写序(谁的值应该赢)是领域语义,不是这里的
职责,交给调用方(见 `tools/live_track_advance.py` 的 peak_equity 只增不减 / day_state
同日首个写入者生效)。
"""
from __future__ import annotations

import json
import os
import tempfile


def atomic_write_json(path: str, obj) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
