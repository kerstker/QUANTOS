import numpy as np
from services.alpha.panel_backtest import PanelPeriod
from services.research.ml.feature_panel import build_feature_panel


def test_aligns_factors_into_matrix_inner_join():
    # 2 factors, 2 periods, instruments per period; factor 'b' missing ETH in period 0 -> that row dropped
    fa = [PanelPeriod(instruments=("BTC", "ETH"), signals=(1.0, 2.0), forward_returns=(0.01, 0.02)),
          PanelPeriod(instruments=("BTC", "ETH"), signals=(3.0, 4.0), forward_returns=(0.03, 0.04))]
    fb = [PanelPeriod(instruments=("BTC",), signals=(9.0,), forward_returns=(0.01,)),
          PanelPeriod(instruments=("BTC", "ETH"), signals=(7.0, 8.0), forward_returns=(0.03, 0.04))]
    fp = build_feature_panel({"a": fa, "b": fb})
    assert fp.factor_names == ["a", "b"]                       # sorted, stable column order
    # rows: period0 -> only BTC (ETH missing in b); period1 -> BTC, ETH
    assert fp.rows == [(0, "BTC"), (1, "BTC"), (1, "ETH")]
    assert fp.X.shape == (3, 2) and fp.y.shape == (3,)
    assert list(fp.X[0]) == [1.0, 9.0]                          # (period0,BTC): a=1.0, b=9.0
    assert list(fp.X[2]) == [4.0, 8.0]                          # (period1,ETH): a=4.0, b=8.0
    assert abs(fp.y[2] - 0.04) < 1e-12


def test_empty_factors_yields_empty_panel():
    fp = build_feature_panel({})
    assert fp.X.shape[0] == 0 and fp.rows == [] and fp.factor_names == []
