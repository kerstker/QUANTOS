import json

import pytest

from live.governance.mandate_store import MandateLoadError, load_mandate, _REQUIRED
from tools.create_live_mandate_template import build_template, write_template


def test_build_template_includes_all_fields_load_mandate_requires():
    tmpl = build_template()
    missing = [f for f in _REQUIRED if f not in tmpl]
    assert missing == []


def test_build_template_venue_prefilled_to_testnet():
    # 结构性字段,必须匹配当前驱动器恒连的 venue.venue_mode,不是风险数字
    assert build_template()["venue"] == "testnet"


def test_build_template_placeholder_numbers_cannot_load_as_real_mandate(tmp_path):
    path = tmp_path / "live_mandate.json"
    path.write_text(json.dumps(build_template()), encoding="utf-8")
    # 模板原样复制成真实文件名也必须 fail-closed,不能被误当真实 mandate 加载
    with pytest.raises(MandateLoadError):
        load_mandate(str(path))


def test_write_template_creates_file_and_parent_dir(tmp_path):
    path = tmp_path / "nested" / "key" / "live_mandate.json.template"
    written = write_template(str(path))
    assert written is True
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["venue"] == "testnet"


def test_write_template_does_not_overwrite_existing_without_force(tmp_path):
    path = tmp_path / "live_mandate.json.template"
    path.write_text('{"user_was_editing_this": true}', encoding="utf-8")
    written = write_template(str(path))
    assert written is False
    assert json.loads(path.read_text(encoding="utf-8")) == {"user_was_editing_this": True}


def test_write_template_force_overwrites(tmp_path):
    path = tmp_path / "live_mandate.json.template"
    path.write_text('{"stale": true}', encoding="utf-8")
    written = write_template(str(path), force=True)
    assert written is True
    assert json.loads(path.read_text(encoding="utf-8"))["venue"] == "testnet"
