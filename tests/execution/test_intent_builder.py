"""Phase 10 — intent builder: target book -> delta OrderIntents (pure, deterministic)."""
from quantos.contracts.trading import OrderSide
from services.execution.intent_builder import build_intents


def test_builds_delta_orders():
    intents = {i.instrument_id: i for i in
               build_intents({"AAA": 100, "BBB": -50, "CCC": 10}, {"AAA": 40, "BBB": 0, "CCC": 10})}
    assert intents["AAA"].side == OrderSide.BUY and intents["AAA"].quantity == 60
    assert intents["BBB"].side == OrderSide.SELL and intents["BBB"].quantity == 50
    assert "CCC" not in intents                       # unchanged -> no order


def test_flip_long_to_short_emits_full_delta():
    intents = build_intents({"AAA": -30}, {"AAA": 50})   # 50 -> -30 = sell 80
    assert intents[0].side == OrderSide.SELL and intents[0].quantity == 80


def test_deterministic_sorted_by_instrument():
    intents = build_intents({"B": 1, "A": 1}, {})
    assert [i.instrument_id for i in intents] == ["A", "B"]
