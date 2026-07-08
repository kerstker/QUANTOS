"""tools/alfred_fetch.py 单元测试——全部走 fake transport,不出网。

钉住的不变量:
- knowledge_time = 该 vintage 的 realtime_start(真实首次可知日),绝不用 event_time 冒充;
- 分页装配行数必须与 count 对齐,不齐 raise(宁失败勿半库);
- 没有 API key -> 非零退出且零网络请求(绝不回退 fredgraph 最新修正值 = 前视);
- 一切异常/输出路径不泄漏 key(错误消息只带 series_id+offset+状态码,不带完整 URL)。
"""
from __future__ import annotations

import json

import pytest

from services.data.fundamental_store import SqliteFundamentalStore
from tools import alfred_fetch


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """依序回放 pages(dict -> 200 JSON;Exception -> raise;(dict, status) -> 指定状态码)。"""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []                      # full URLs requested (tests inspect offsets)

    def __call__(self, req, timeout=None):
        self.calls.append(req.full_url)
        item = self.pages.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            return _FakeResponse(item[0], status=item[1])
        return _FakeResponse(item)


def _obs(date, realtime_start, value):
    return {"date": date, "realtime_start": realtime_start, "realtime_end": "9999-12-31",
            "value": value}


# --- parse_observations(纯函数) ---


def test_parse_observations_knowledge_is_realtime_start():
    payload = {"count": 3, "observations": [
        _obs("2020-01-01", "2020-02-13", "257.971"),     # 首发 vintage
        _obs("2020-01-01", "2021-01-13", "258.687"),     # 次年季调重估(restatement)
        _obs("2020-02-01", "2020-03-11", "."),           # 缺失值
    ]}
    recs, rejected = alfred_fetch.parse_observations(payload)
    assert recs == [(20200101, 20200213, 257.971), (20200101, 20210113, 258.687)]
    assert rejected == []                                 # "." 是缺失,不是坏行


def test_parse_observations_rejects_lookahead():
    # realtime_start < date 理论不可能(发布恒晚于期首);防御:进 rejected,不产出记录
    payload = {"count": 2, "observations": [
        _obs("2020-01-01", "2019-12-31", "100.0"),
        _obs("2020-02-01", "2020-03-11", "101.0"),
    ]}
    recs, rejected = alfred_fetch.parse_observations(payload)
    assert recs == [(20200201, 20200311, 101.0)]
    assert len(rejected) == 1 and "lookahead" in rejected[0][-1]


def test_parse_observations_rejects_unparseable_value():
    payload = {"count": 1, "observations": [_obs("2020-01-01", "2020-02-13", "not-a-number")]}
    recs, rejected = alfred_fetch.parse_observations(payload)
    assert recs == [] and len(rejected) == 1


# --- vintage 入库后的 PIT 可见性(restatement 语义) ---


def test_alfred_store_restatement_pit_visibility(tmp_path):
    db = str(tmp_path / "macro_alfred_test.db")
    store = SqliteFundamentalStore(db, create=True)
    for event, know, val in [(20200101, 20200213, 100.0), (20200101, 20210105, 105.0)]:
        assert alfred_fetch.store_record(store, "CPIAUCSL", event, know, val) == "added"
    assert store.latest_as_of("CPIAUCSL", "MACRO", 20200212) is None          # 首发前一日:不可见
    assert store.latest_as_of("CPIAUCSL", "MACRO", 20200213).value == 100.0   # 首发日:首发值
    assert store.latest_as_of("CPIAUCSL", "MACRO", 20210104).value == 100.0   # 修正前:仍是首发值
    assert store.latest_as_of("CPIAUCSL", "MACRO", 20210105).value == 105.0   # 修正日:修正值


def test_store_record_rejects_contract_lookahead(tmp_path):
    # FeatureValue 契约本身拒 knowledge<event —— 计数上报,绝不悄悄改时间
    db = str(tmp_path / "macro_alfred_test.db")
    store = SqliteFundamentalStore(db, create=True)
    assert alfred_fetch.store_record(store, "CPIAUCSL", 20200101, 20191231, 1.0) == "contract-rejected"
    assert store.latest_as_of("CPIAUCSL", "MACRO", 99999999) is None


# --- 分页装配 ---


def test_alfred_paging_assembles_full_count():
    page1 = {"count": 5, "observations": [
        _obs("2020-01-01", "2020-02-13", "1.0"),
        _obs("2020-02-01", "2020-03-11", "2.0"),
        _obs("2020-03-01", "2020-04-10", "3.0")]}
    page2 = {"count": 5, "observations": [
        _obs("2020-04-01", "2020-05-12", "4.0"),
        _obs("2020-05-01", "2020-06-10", "5.0")]}
    opener = _FakeOpener([page1, page2])
    recs, rejected = alfred_fetch.fetch_series("UNRATE", "TESTKEY", opener=opener, page_limit=3)
    assert len(recs) == 5 and rejected == []
    assert len(opener.calls) == 2
    assert "offset=0" in opener.calls[0] and "offset=3" in opener.calls[1]


def test_alfred_paging_count_mismatch_raises():
    # 承诺 count=6 但两页只给 5 行、第三页空 -> raise(宁失败勿半库),消息不带 URL/key
    page1 = {"count": 6, "observations": [_obs("2020-01-01", "2020-02-13", "1.0")] * 3}
    page2 = {"count": 6, "observations": [_obs("2020-04-01", "2020-05-12", "4.0")] * 2}
    page3 = {"count": 6, "observations": []}
    with pytest.raises(RuntimeError) as ei:
        alfred_fetch.fetch_series("UNRATE", "SEKRET123", opener=_FakeOpener([page1, page2, page3]),
                                  page_limit=3)
    msg = str(ei.value)
    assert "paging mismatch" in msg and "UNRATE" in msg
    assert "SEKRET123" not in msg and "http" not in msg.lower()


# --- 无 key:fail-closed,零网络请求 ---


def test_alfred_missing_key_fails_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(alfred_fetch, "_KEY_PATH", str(tmp_path / "no_such_key.txt"))
    opener = _FakeOpener([])
    with pytest.raises(SystemExit) as ei:
        alfred_fetch.main(opener=opener)
    assert ei.value.code == 1
    assert opener.calls == []                             # 零网络请求
    err = capsys.readouterr().err
    assert "FRED API key" in err and "fredgraph" in err   # 诚实说明,不静默


def test_alfred_blank_key_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    kp = tmp_path / "fred_api_key.txt"
    kp.write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(alfred_fetch, "_KEY_PATH", str(kp))
    opener = _FakeOpener([])
    with pytest.raises(SystemExit) as ei:
        alfred_fetch.main(opener=opener)
    assert ei.value.code == 1 and opener.calls == []


# --- key 泄漏面:异常/输出路径绝不带 key(评审 required_change) ---


def test_transport_error_message_never_leaks_key(capsys):
    key = "SEKRET123"
    boom = Exception(f"connect fail https://api.stlouisfed.org/x?api_key={key}&offset=0")
    with pytest.raises(RuntimeError) as ei:
        alfred_fetch._fetch_page("CPIAUCSL", 0, key, opener=_FakeOpener([boom]))
    msg = str(ei.value)
    assert key not in msg and "api_key" not in msg
    assert "CPIAUCSL" in msg and "offset=0" in msg        # 定位信息够用:series+offset
    assert ei.value.__suppress_context__                   # 原始异常(含 URL)不进 traceback 链
    out = capsys.readouterr()
    assert key not in out.out and key not in out.err


def test_http_error_status_reported_without_url(capsys):
    key = "SEKRET123"
    payload = {"error_code": 400, "error_message": "Bad Request"}
    with pytest.raises(RuntimeError) as ei:
        alfred_fetch._fetch_page("UNRATE", 100, key, opener=_FakeOpener([(payload, 400)]))
    msg = str(ei.value)
    assert "HTTP 400" in msg and "UNRATE" in msg
    assert key not in msg and "http://" not in msg and "https://" not in msg
    out = capsys.readouterr()
    assert key not in out.out and key not in out.err


def test_non_json_body_reported_without_url():
    class _R:
        status = 200

        def read(self):
            return b"<html>bot wall</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with pytest.raises(RuntimeError) as ei:
        alfred_fetch._fetch_page("GDPC1", 0, "SEKRET123", opener=lambda req, timeout=None: _R())
    msg = str(ei.value)
    assert "non-JSON" in msg and "SEKRET123" not in msg


# --- main 端到端(fake transport -> 真 sqlite 落库) ---


def test_main_end_to_end_writes_vintages(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FRED_API_KEY", "TESTKEY")
    db = str(tmp_path / "macro_alfred.db")
    monkeypatch.setattr(alfred_fetch, "_DB", db)
    monkeypatch.setattr(alfred_fetch, "_SERIES", ["CPIAUCSL"])
    page = {"count": 2, "observations": [
        _obs("2020-01-01", "2020-02-13", "257.971"),
        _obs("2020-01-01", "2021-01-13", "258.687")]}
    alfred_fetch.main(opener=_FakeOpener([page]))
    store = SqliteFundamentalStore(db)
    assert store.latest_as_of("CPIAUCSL", "MACRO", 20200214).value == 257.971
    assert store.latest_as_of("CPIAUCSL", "MACRO", 20210113).value == 258.687
    assert store.latest_as_of("CPIAUCSL", "MACRO", 20200101) is None   # 期首日尚未发布:不可见
    out = capsys.readouterr().out
    assert "TESTKEY" not in out                            # 正常输出同样不带 key
