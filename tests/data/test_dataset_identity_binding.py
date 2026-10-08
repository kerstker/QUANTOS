"""P1-7 — dataset 身份必须绑定它真正消费的输入。

数据身份是 research session 记账的键(multiple-testing / bandit 状态、评估产物)。若它只贴
一个内容指纹,那么**标的集合、事件网格、PIT 知识时钟、来源版本**任一改变都不会改变身份:
两批数据契约不同的 run 会共用同一份记账。本文件钉死绑定身份(dataset_identity)的这几条性质,
并记录 dataset_version 仍是旧的内容指纹(四个 tools 证据写入方按该值固定)。
"""
from quantos.contracts.data import Bar
from services.data.research_adapter import PITResearchDataAdapter, dataset_identity
from services.data.versioning import content_hash_of
from services.research.session import _derive_dataset_version


class _VersionedStore:
    """最小 PIT store 替身:as_of / latest_as_of / fetch_bars + 一个来源版本戳。"""

    def __init__(self, *, version="v1", extra_vintage=False, c_bars=False):
        self.version = version
        self.bars = {
            "A": [_bar("A", 1, 1), _bar("A", 2, 3)],
            "B": [_bar("B", 1, 1)],
        }
        if c_bars:
            self.bars["C"] = [_bar("C", 1, 1)]
        if extra_vintage:
            self.bars["A"].append(_bar("A", 1, 2))          # 同一 event 的第二个 knowledge vintage
        self.fetch_calls = []

    def as_of(self, key, as_of_time, event_time_max=None):
        return [b for b in self.bars.get(key, ())
                if b.knowledge_time <= as_of_time and (event_time_max is None or b.event_time <= event_time_max)]

    def latest_as_of(self, key, as_of_time, event_time_max=None):
        rows = self.as_of(key, as_of_time, event_time_max)
        return rows[-1] if rows else None

    def fetch_bars(self, key, start, end):
        self.fetch_calls.append((key, start, end))
        return [b for b in self.bars.get(key, ()) if start <= b.event_time <= end]


def _bar(instrument_id, event_time, knowledge_time):
    return Bar(instrument_id=instrument_id, event_time=event_time, knowledge_time=knowledge_time,
               open=10.0, high=11.0, low=9.0, close=10.5, volume=100.0)


def test_identity_changes_with_every_consumed_input():
    port = PITResearchDataAdapter(_VersionedStore())
    base = port.dataset_identity(["A", "B"], [1, 2])

    assert base and len(base) == 64                             # stable_hash 的完整 sha256
    assert base != port.dataset_version(["A", "B"], [1, 2])      # 身份 ≠ 内容指纹

    # 标的集合:加一个在该窗口内没有 bar 的标的名 —— 内容指纹不变,身份必须变
    assert port.dataset_identity(["A", "B", "C"], [1, 2]) != base
    assert port.dataset_version(["A", "B", "C"], [1, 2]) == port.dataset_version(["A", "B"], [1, 2])

    # 事件网格:加一个窗口内没有 bar 的网格点
    assert port.dataset_identity(["A", "B"], [1, 2, 3]) != base

    # 知识时钟 / PIT 策略(决策滞后与最小可知滞后是策略的一部分)
    assert port.dataset_identity(["A", "B"], [1, 2], decision_lag=1, minimum_knowledge_lag=1) != base
    assert port.dataset_identity(["A", "B"], [1, 2], decision_lag=0, minimum_knowledge_lag=1) != base

    # 来源 / 版本
    other = PITResearchDataAdapter(_VersionedStore(version="v2"))
    assert other.dataset_identity(["A", "B"], [1, 2]) != base

    # 记录内容(多一个 knowledge vintage)
    changed = PITResearchDataAdapter(_VersionedStore(extra_vintage=True))
    assert changed.dataset_identity(["A", "B"], [1, 2]) != base


def test_identity_is_reproducible_and_order_independent():
    port = PITResearchDataAdapter(_VersionedStore())

    first = port.dataset_identity(["B", "A", "A"], [2, 1, 1])    # 顺序/重复不影响身份
    assert first == port.dataset_identity(["A", "B"], [1, 2])
    assert first == port.dataset_identity(["A", "B"], [1, 2])

    # 模块级 helper 与 port 方法给出同一身份(同一数据层实现)
    store = _VersionedStore()
    assert dataset_identity(store, ["A", "B"], [1, 2]) == \
        PITResearchDataAdapter(store).dataset_identity(["A", "B"], [1, 2])


def test_identity_is_empty_when_nothing_was_consumed():
    port = PITResearchDataAdapter(_VersionedStore())

    assert port.dataset_identity(["A"], []) == ""                # 空网格:无记录 -> 无身份
    assert port.dataset_identity(["A"], [99]) == ""              # 窗口内无 bar:同样不构造身份


def test_content_fingerprint_is_kept_unchanged_for_callers_that_pin_it():
    """dataset_version 仍是内容指纹(旧契约;tools 的 bar_version/bar_source_version 按它固定)。"""
    store = _VersionedStore()
    port = PITResearchDataAdapter(store)

    assert port.dataset_version(["A", "B"], [1, 2]) == content_hash_of([*store.bars["A"], *store.bars["B"]])
    assert store.fetch_calls == [("A", 1, 2), ("B", 1, 2)]


def test_research_session_derives_the_bound_identity():
    port = PITResearchDataAdapter(_VersionedStore())

    identity = _derive_dataset_version(port, ["A", "B"], [1, 2])
    assert identity == port.dataset_identity(["A", "B"], [1, 2])
    # 网格 / 标的集合任一改变 -> session 记账身份随之改变
    assert identity != _derive_dataset_version(port, ["A", "B"], [1, 2, 3])
    assert identity != _derive_dataset_version(port, ["A", "B", "C"], [1, 2])
    # 确定性:同一输入两次派生同一身份
    assert identity == _derive_dataset_version(port, ["A", "B"], [1, 2])


def test_port_without_a_bound_identity_falls_back_to_its_version():
    """无法提供绑定身份的 port:保留旧契约(不构造一个假装绑定的身份)。"""

    class _LegacyPort(_VersionedStore):
        def dataset_version(self, instruments, event_times):
            return "already-versioned"

    assert _derive_dataset_version(_LegacyPort(), ["A", "B"], [1, 2]) == "already-versioned"
