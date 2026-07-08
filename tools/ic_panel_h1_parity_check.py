"""IC 面板 h=1 逐位一致校验(schema v2 迁移的可执行检测点,评审 required_change)。

用法:python -m tools.ic_panel_h1_parity_check <旧快照> <新快照>

对齐 (name, universe) 后,逐行比较顶层六统计(mean_ic/ic_std/icir/hit_rate/t_stat/n_periods)
+ per_year,要求与旧快照**逐位相等**(JSON 解码后的 == 精确比较,不带容差)。任何漂移都非零退出
并逐项列出——顶层 h=1 数字是既有交叉核对资产(evidence/ic_panel.json 数字引用它),schema 迁移
只许加字段,不许动 h=1 数字;任何漂移都是实现 bug 的信号,不是"可接受的重构副作用"。

正确用法注意:两份快照必须出自**同一份底层数据**(先用旧代码对当前数据跑一份基线,再用新代码跑,
两跑之间不刷新任何数据),否则数据漂移会被误报成代码漂移。离线、只读、非门禁常驻(一次性迁移校验,
但保留在 repo 里供未来快照迁移复用)。
"""
from __future__ import annotations

import json
import sys

_STATS = ("mean_ic", "ic_std", "icir", "hit_rate", "t_stat", "n_periods", "per_year")


def compare_snapshots(old: dict, new: dict) -> list:
    """返回漂移清单(空 = 逐位一致)。只比 h=1 口径的顶层字段;新快照允许多字段(by_period 等)。"""
    drifts = []
    old_rows = {(r["name"], r["universe"]): r for r in old.get("rows", [])}
    new_rows = {(r["name"], r["universe"]): r for r in new.get("rows", [])}
    for key in sorted(old_rows):
        if key not in new_rows:
            drifts.append(f"{key}: row MISSING from new snapshot")
            continue
        for f in _STATS:
            ov, nv = old_rows[key].get(f), new_rows[key].get(f)
            if ov != nv:
                drifts.append(f"{key}: {f} drifted {ov!r} -> {nv!r}")
    # 新快照里多出来的因子行不算漂移(注册表加行是正常演化);多出来的字段(by_period 等)同理。
    return drifts


def main(argv: list) -> int:
    if len(argv) != 3:
        print("usage: python -m tools.ic_panel_h1_parity_check <old_snapshot.json> <new_snapshot.json>",
              file=sys.stderr)
        return 2
    old = json.load(open(argv[1], encoding="utf-8"))
    new = json.load(open(argv[2], encoding="utf-8"))
    drifts = compare_snapshots(old, new)
    if drifts:
        print(f"H1 PARITY FAILED: {len(drifts)} drift(s):", file=sys.stderr)
        for d in drifts:
            print(f"  - {d}", file=sys.stderr)
        return 1
    n = len(old.get("rows", []))
    print(f"H1 PARITY OK: {n} rows, top-level six stats + per_year bitwise-identical")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
