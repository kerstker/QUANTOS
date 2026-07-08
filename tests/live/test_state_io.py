"""live/execution/state_io.py —— 原子文件状态落盘(2026-07-06,安全审查 punch-list 修复:
peak_equity/day_state 此前 `json.dump(obj, open(path, "w"))` 非原子,进程被杀在写一半时会
留下截断 JSON,下次加载 JSONDecodeError -> 现有 fail-open 逻辑悄悄当成"无历史状态"重置。
"""
import json
import os

import pytest

from live.execution.state_io import atomic_write_json


def test_atomic_write_json_creates_file_with_content(tmp_path):
    path = str(tmp_path / "state.json")
    atomic_write_json(path, {"peak_equity": 100.0})
    assert json.load(open(path, encoding="utf-8")) == {"peak_equity": 100.0}


def test_atomic_write_json_overwrites_existing_file(tmp_path):
    path = str(tmp_path / "state.json")
    atomic_write_json(path, {"peak_equity": 100.0})
    atomic_write_json(path, {"peak_equity": 200.0})
    assert json.load(open(path, encoding="utf-8")) == {"peak_equity": 200.0}


def test_atomic_write_json_leaves_original_untouched_on_serialization_error(tmp_path):
    path = str(tmp_path / "state.json")
    atomic_write_json(path, {"peak_equity": 100.0})
    with pytest.raises(TypeError):
        atomic_write_json(path, {"peak_equity", "not json serializable as a set"})
    # 原文件必须完好——绝不能留下一份写了一半的损坏内容
    assert json.load(open(path, encoding="utf-8")) == {"peak_equity": 100.0}


def test_atomic_write_json_does_not_leave_temp_file_behind(tmp_path):
    path = str(tmp_path / "state.json")
    atomic_write_json(path, {"peak_equity": 100.0})
    try:
        atomic_write_json(path, {"peak_equity", "bad"})
    except TypeError:
        pass
    leftovers = [f for f in os.listdir(str(tmp_path)) if f != "state.json"]
    assert leftovers == []
