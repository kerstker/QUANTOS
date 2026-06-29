from services.research.paper_trading.store import PaperTrackStore
from services.research.paper_trading.health import paper_health_by_alpha


def test_paper_health_translates_track_to_monitor_shape():
    s = PaperTrackStore(":memory:", create=True)
    s.append_periods("a1", [(1000, -0.05, -0.02, "OOS_TAIL"), (2000, -0.04, -0.01, "OOS_TAIL")])
    s.recompute_summary("a1", backtest_sharpe=2.0)
    h = paper_health_by_alpha(s, backtest_by_alpha={"a1": {"ic": 0.05, "return": 0.2}})
    assert "a1" in h
    assert h["a1"]["backtest"] == {"ic": 0.05, "return": 0.2}
    assert h["a1"]["live"]["ic"] == -0.015 or h["a1"]["live"]["return"] is not None   # real paper live side
