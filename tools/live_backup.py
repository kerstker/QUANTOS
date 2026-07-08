"""灾备(2026-07-05,机构审计 P0)。

真钱决策的唯一证据(账本/审计链/mandate/证据钟)全部单机单盘——磁盘损坏 = 永久丢失,
DR 计划此前甚至不存在。本工具把业务数据 + 配置(不含 API 密钥,见 `build_backup_manifest`
docstring)复制到独立的时间戳备份目录。用法:

    python -m tools.live_backup                       # 备份到 data/backup/<timestamp>/
    python -m tools.live_backup --dest D:/quantos-backup   # 备份到其它盘(真正的灾备该指向
                                                        # 不同物理磁盘,同盘备份只防文件损坏)
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
import time

_CACHE_DIR = os.path.join("data", "cache")
_KEY_DIR = "key"

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


def main() -> None:
    dest_root = os.path.join("data", "backup")
    if "--dest" in sys.argv:
        dest_root = sys.argv[sys.argv.index("--dest") + 1]
    manifest = build_backup_manifest(_CACHE_DIR, _KEY_DIR)
    if not manifest:
        print("nothing to back up yet (no live-path artifacts found)", flush=True)
        return
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = os.path.join(dest_root, stamp)
    os.makedirs(dest, exist_ok=True)
    for src in manifest:
        shutil.copy2(src, os.path.join(dest, os.path.basename(src)))
    print(f"backed up {len(manifest)} file(s) -> {dest}", flush=True)
    print("note: API key files are NOT included — key backup/rotation is your own "
         "security practice", flush=True)


if __name__ == "__main__":
    main()
