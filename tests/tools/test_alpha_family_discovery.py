from services.research.alpha_expression import FieldPanel
from tools.alpha_family_discovery import _pbo_by_cadence, _periods_for_signal


def test_pbo_uses_aligned_calendar_blocks():
    rows = []
    for cid, shift in (("a", 0.0), ("b", 0.001)):
        rows.append({
            "horizon": 1,
            "periods": [type("P", (), {"event_time": i})() for i in range(32)],
            "returns": [0.01 + shift if i % 2 == 0 else -0.005 for i in range(32)],
        })
    value = _pbo_by_cadence({"a": rows[0], "b": rows[1]})
    assert set(value) == {"a", "b"}
    assert value["a"] is not None
    assert 0.0 <= value["a"] <= 1.0


def test_panel_period_builder_does_not_use_future_adv():
    panel = FieldPanel(
        ("A", "B"), (1, 2, 3),
        {"close": [[10.0, 20.0], [11.0, 19.0], [12.0, 18.0]],
         "volume": [[100.0, 200.0], [110.0, 190.0], [120.0, 180.0]]},
    )
    signal = [[0.0, 1.0], [0.2, 0.8], [0.3, 0.7]]
    periods = _periods_for_signal(signal, panel, horizon=1)
    assert periods[0].event_time == 1
    assert periods[0].adv_notional == (1000.0, 4000.0)
