import pytest

from services.research.macro_regime import (bucket_returns, pit_latest_asof, pit_series,
                                            pit_yoy_asof, sign_regime, tercile_labels)


def test_tercile_labels_splits_three():
    labels = tercile_labels([1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert labels[0] == "low" and labels[-1] == "high" and "mid" in labels


def test_tercile_preserves_none():
    labels = tercile_labels([1, 2, None, 8, 9])
    assert labels[2] is None


def test_sign_regime():
    assert sign_regime(-0.5) == "inverted"
    assert sign_regime(0.3) == "normal"
    assert sign_regime(None) is None


def test_bucket_returns_groups_and_scores():
    rs = [0.02, -0.01, 0.03, -0.02]
    lb = ["high", "low", "high", "low"]
    out = bucket_returns(rs, lb)
    assert out["high"]["n"] == 2 and out["low"]["n"] == 2
    assert out["high"]["mean_return"] > out["low"]["mean_return"]


def test_bucket_returns_ignores_none_labels():
    out = bucket_returns([0.01, 0.02], ["a", None])
    assert "a" in out and None not in out and out["a"]["n"] == 1


# --- ALFRED vintage 的 PIT 求值纯函数(rows = (event_yyyymmdd, knowledge_yyyymmdd, value)) ---

# CPI 式月频 fixture:2019-01 与 2020-01 两个观测;2019-01 在 2021-06-01 被季调重估改写(晚知修正)。
_CPI_ROWS = [
    (20190101, 20190213, 100.0),
    (20200101, 20200213, 110.0),
    (20190101, 20210601, 105.0),      # restatement:知道它要等到 2021-06-01
]


def test_pit_yoy_asof_uses_only_knowable_rows():
    # 修正 realtime_start 之前:基期用首发值 100 -> yoy = 10%
    assert pit_yoy_asof(_CPI_ROWS, 20200301) == pytest.approx(110.0 / 100.0 - 1.0)
    # 修正之后:同一个基期变成修正值 105 -> yoy 变 4.76%(跨 vintage 混用是特性——
    # 每个值都是 as_of 时点研究员真实拥有的最佳已知值)
    assert pit_yoy_asof(_CPI_ROWS, 20210701) == pytest.approx(110.0 / 105.0 - 1.0)


def test_pit_yoy_asof_missing_base_month_none():
    rows = [(20200101, 20200213, 110.0)]                  # 12 个月前无观测
    assert pit_yoy_asof(rows, 20200301) is None


def test_pit_yoy_asof_nothing_knowable_none():
    assert pit_yoy_asof(_CPI_ROWS, 20190212) is None      # 全部 knowledge > as_of
    assert pit_yoy_asof([], 20200101) is None


def test_pit_yoy_asof_quarterly_months_back_12():
    # GDPC1 季频:months_back=12 落在去年同季(期首月相减恰 12)
    rows = [(20200101, 20200430, 19000.0), (20210101, 20210429, 19500.0)]
    assert pit_yoy_asof(rows, 20210501) == pytest.approx(19500.0 / 19000.0 - 1.0)
    assert pit_yoy_asof(rows, 20210401) is None           # 2021Q1 尚未发布


def test_pit_latest_asof_restatement_visibility():
    assert pit_latest_asof(_CPI_ROWS, 20190212) is None
    assert pit_latest_asof(_CPI_ROWS, 20190213) == 100.0
    assert pit_latest_asof(_CPI_ROWS, 20200213) == 110.0  # 最新 event 优先(不是最新 knowledge)
    # 2021-06 的修正改写的是 2019-01(老月份),最新 event 仍是 2020-01 -> 值不变
    assert pit_latest_asof(_CPI_ROWS, 20210701) == 110.0


def test_pit_series_matches_point_functions():
    # 批量版(单调游标预索引)必须与逐点函数语义完全一致
    as_ofs = [20190101, 20190213, 20200101, 20200213, 20200301, 20210601, 20210701]
    got_level = pit_series(_CPI_ROWS, as_ofs)
    got_yoy = pit_series(_CPI_ROWS, as_ofs, months_back=12)
    assert got_level == [pit_latest_asof(_CPI_ROWS, a) for a in as_ofs]
    assert got_yoy == [pit_yoy_asof(_CPI_ROWS, a, 12) for a in as_ofs]


def test_pit_functions_agree_on_exact_duplicate_event_and_knowledge_tie():
    # 2026-07-06 对抗式复审 CONFIRMED 修复的反例:同一 (event, knowledge) 出现两次但 value
    # 不同(如同日两次录入/覆盖)。修复前 pit_latest_asof(严格 `>`,平局保留*第一*行)与
    # pit_series(排序键含 value,平局胜负偶然取决于数值大小)在这种输入上会给出不同答案,
    # 打破二者"语义完全一致"的等价性声明——三个函数现在统一"平局由 rows 里**最后**出现的
    # 那行胜出"。
    rows = [(20200101, 20200213, 100.0), (20200101, 20200213, 200.0)]
    assert pit_latest_asof(rows, 20200213) == 200.0     # 最后一行(200.0)胜出,不是 100.0
    assert pit_series(rows, [20200213]) == [200.0]

    # YoY 路径同一枚反例:基期(months_back 之前那个月)也可能有平局,pit_yoy_asof 与
    # pit_series(months_back=) 必须一致地选"最后出现"的那行。
    yoy_rows = [(20190101, 20190213, 10.0), (20190101, 20190213, 20.0),   # 基期平局
               (20200101, 20200213, 30.0)]
    assert pit_yoy_asof(yoy_rows, 20200213, 12) == pytest.approx(30.0 / 20.0 - 1.0)
    assert pit_series(yoy_rows, [20200213], months_back=12) == [pytest.approx(30.0 / 20.0 - 1.0)]


def test_pit_series_rejects_unsorted_as_ofs():
    with pytest.raises(ValueError):
        pit_series(_CPI_ROWS, [20210101, 20200101])


def test_macro_regime_driver_failsoft_without_alfred_db(tmp_path):
    # 驱动器侧:无 macro_alfred.db -> 通胀/就业维整体缺席({}),不 raise、不冒充
    from tools.macro_regime_research import _alfred_regime_labels
    dims = _alfred_regime_labels([1577836800000, 1578441600000],
                                 db_path=str(tmp_path / "no_such.db"))
    assert dims == {}
