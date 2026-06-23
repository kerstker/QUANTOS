"""Intent builder (Phase 10) — target book -> OrderIntents.

A pure, deterministic STRATEGY/TRANSLATION step (NOT permission): given target and
current signed positions, emit the delta orders. Risk permission is decided
separately by the pre-trade gate. No sizing magic, no price origination.
"""
from __future__ import annotations

from typing import Dict

from quantos.contracts.trading import OrderIntent, OrderSide, OrderType


def build_intents(targets: Dict[str, int], current: Dict[str, int],
                  *, strategy_id: str = "") -> list[OrderIntent]:
    """One OrderIntent per instrument whose target signed position differs from
    current. delta>0 -> BUY, delta<0 -> SELL, |delta| = quantity. Deterministic
    (instruments sorted)."""
    out: list[OrderIntent] = []
    for iid in sorted(set(targets) | set(current)):
        delta = int(targets.get(iid, 0)) - int(current.get(iid, 0))
        if delta == 0:
            continue
        out.append(OrderIntent(
            instrument_id=iid,
            side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
            quantity=abs(delta),
            order_type=OrderType.MARKET,
            strategy_id=strategy_id,
        ))
    return out
