"""P1-5 — rolling feature 必须按标的独立计算(跨标的污染回归)。

interleaved 多标的 bar 进入同一个 `rolling_feature` 调用时,窗口只能由**该标的自己的**
bar 组成:warm-up 按该标的自己的 bar 计数、聚合值必须与"只传该标的"的单标的调用逐个相等。
此前 sort key 只有 (event_time, knowledge_time),instrument_id 不在其中,于是 A/B 交错的
窗口会把对方的 close 混进来,再用**当前 bar 的** instrument_id 盖章输出 —— 污染无声。
"""
from quantos.contracts.data import Bar
from services.features.calculation import rolling_feature


def _bars(inst, closes, kt_offset=0):
    return [Bar(instrument_id=inst, event_time=i + 1, knowledge_time=i + 1 + kt_offset,
                open=c, high=c + 1, low=c - 1, close=c, volume=100)
            for i, c in enumerate(closes)]


def _rows(fvs):
    return [(f.instrument_id, f.event_time, f.knowledge_time, f.value) for f in fvs]


def _by_instrument(fvs):
    out = {}
    for fv in fvs:
        out.setdefault(fv.instrument_id, []).append(fv)
    return out


def test_interleaved_instruments_do_not_contaminate_each_other():
    a, b = _bars("A", [10, 12, 14, 16]), _bars("B", [100, 200, 300, 400])
    interleaved = [bar for pair in zip(a, b) for bar in pair]       # A,B,A,B,...
    mixed = rolling_feature("mean2", interleaved, window=2)

    per_inst = _by_instrument(mixed)
    # 每个标的的结果必须与单标的调用完全一致(值、event_time、knowledge_time 都要相等)
    assert _rows(per_inst["A"]) == _rows(rolling_feature("mean2", a, window=2))
    assert _rows(per_inst["B"]) == _rows(rolling_feature("mean2", b, window=2))


def test_interleaved_warmup_counts_the_instruments_own_bars():
    a, b = _bars("A", [10, 12, 14]), _bars("B", [100, 200, 300])
    interleaved = [bar for pair in zip(a, b) for bar in pair]
    per_inst = _by_instrument(rolling_feature("mean2", interleaved, window=2))

    assert [f.value for f in per_inst["A"]] == [None, 11.0, 13.0]     # 自己的 bar 计数 warm-up
    assert [f.value for f in per_inst["B"]] == [None, 150.0, 250.0]


def test_interleaved_output_is_stamped_with_the_owning_instrument():
    a, b = _bars("A", [10, 12]), _bars("B", [100, 200])
    interleaved = [bar for pair in zip(a, b) for bar in pair]
    per_inst = _by_instrument(rolling_feature("mean2", interleaved, window=2))

    # 每个标的的输出只能由该标的自己的 close 决定:11 = mean(10,12) / 150 = mean(100,200),
    # 而不是把对方的价格尺度(100/200)混进来的 55 / 56 / 106。
    assert [f.value for f in per_inst["A"]] == [None, 11.0]
    assert [f.value for f in per_inst["B"]] == [None, 150.0]


def test_multi_instrument_output_order_is_event_time_then_knowledge_time():
    a, b = _bars("A", [10, 12, 14, 16]), _bars("B", [100, 200, 300, 400])
    interleaved = [bar for pair in zip(a, b) for bar in pair]

    fvs = rolling_feature("m", interleaved, 2)
    keys = [(f.event_time, f.knowledge_time) for f in fvs]
    assert keys == sorted(keys)                                     # 仍是旧的 (event_time, kt) 全局序
    assert _rows(fvs) == _rows(rolling_feature("m", interleaved, 2))  # 确定性


def test_single_instrument_contract_is_unchanged():
    bars = _bars("X", [10, 12, 14, 16])
    fvs = rolling_feature("m", bars, 2)

    assert [f.instrument_id for f in fvs] == ["X"] * 4
    assert [f.event_time for f in fvs] == [1, 2, 3, 4]
    assert [f.knowledge_time for f in fvs] == [1, 2, 3, 4]
    assert [f.value for f in fvs] == [None, 11.0, 13.0, 15.0]
