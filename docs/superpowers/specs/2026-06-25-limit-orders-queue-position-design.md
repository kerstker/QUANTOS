# Limit Orders + Queue Position (event backtester, #3 sub-project)

**Date:** 2026-06-25
**Status:** Approved (brainstorming) → writing-plans
**Layer:** `services/research/backtest_engine/` (event-driven backtester)
**Readiness:** L2_DRY_RUN_ONLY — research backtesting only, no broker, no live surface.

## Goal

Raise backtest fidelity by modelling the two things a limit order genuinely
expresses under **bar-only (OHLCV) data, no L2 / no tick**:

1. **Non-execution risk** — a limit order may never fill (adverse selection); the
   strategy does not silently get the price it wanted.
2. **Queue position** — where you sit in the queue at your price level affects
   whether the bar's traded volume reaches you.

This is the maximum honest fidelity bar data can support. We do NOT fabricate an
L2 book or tick-level matching.

## Non-goals (YAGNI)

- No explicit order-level API (`submit_limit_order(...)`) parallel to the existing
  target-weight engine. Orders are derived from the target-weight decision.
- No per-instrument strategy-supplied limit price (the target-weight API emits
  weights only). The limit is derived from the decision close ± a configured offset.
- No multi-currency (separate, deferred #3 item).
- No change to `Book`, the cost model, corporate actions, or the reconciliation
  identity.

## Architecture & integration seam

New module `services/research/backtest_engine/limit_orders.py`. The engine gains a
single **optional** parameter `run_event_backtest(..., limit_fill=None)`:

- `limit_fill=None` (default) → behaviour is **byte-for-byte identical** to today
  (the existing `pending` market-delta path via `NextOpenFill`/`ParticipationFill`).
  Pure addition, zero regression.
- `limit_fill=LimitFill(...)` → target-weight deltas are routed into a **resting
  order book** that lives across bars, instead of the one-shot `pending` dict.

Fills always go through the unchanged `book.apply_fill(inst, filled, price, cost)`.
Therefore the global reconciliation identity

```
final_equity − initial == realized_pnl + final_unrealized + dividend_income − total_cost
```

holds **by construction**: unfilled / cancelled orders never touch cash or
position, so they cannot perturb the identity. Turnover accrues only on actual
fills (`abs(filled) × price`), exactly as today.

### `LimitFill` configuration

```
LimitFill(
    limit_offset_bps: float = 0.0,   # limit = close × (1 ∓ offset): buy below, sell above
    queue_fraction:   float = 0.25,  # initial queue_ahead = queue_fraction × decision-bar volume (conservative)
    participation:    float = 0.10,  # bar volume fraction tradeable at a touched level (matches ParticipationFill)
    expire_bars:      int   = 3,     # GTC lifetime; unfilled orders cancel after this many bars
)
```

All knobs are conservative-by-default: `queue_fraction > 0` means we assume we
start behind some resting liquidity, which **under-estimates fill / over-estimates
non-execution risk** — the correct direction for a safety-critical research
platform (never over-state a strategy's executability).

## Resting order state

```
RestingOrder(
    instrument_id: str,
    side: int,            # +1 buy, −1 sell
    qty_remaining: int,   # signed-magnitude shares still to fill
    limit_price: float,
    queue_ahead: float,   # liquidity that must trade before us; decremented as volume trades at our level
    bars_alive: int,      # incremented each bar; cancelled when > expire_bars
)
```

One resting order per instrument at a time (target-weight has a single desired
position per instrument, so there is never a reason to stack two).

## Per-bar processing order (deterministic)

At each `(ts, bars)` the engine processes in this fixed order:

1. **Corporate actions** on the carried position (unchanged from today).
2. **Limit-order matching** (new) — iterate resting orders in `sorted(inst)` order:

   ```
   buy order at limit L (sell is mirror-image on bar.high / +offset):
     if bar.low > L:            # not touched
         no fill; queue_ahead unchanged
     elif bar.low < L:          # price traded THROUGH the limit → queue cleared
         filled = qty_remaining
         fill_price = min(bar.open, L)        # gap-through fills at the better open
     else:                      # bar.low == L → touched, not through → partial
         vol_at_level = participation × bar.volume
         fillable = max(0, vol_at_level − queue_ahead)
         filled   = min(qty_remaining, fillable)
         queue_ahead = max(0, queue_ahead − vol_at_level)   # this bar's volume consumes our queue
     on filled ≠ 0:
         fill_price ×= (1 ± slippage_bps/1e4)               # slippage in price (sign by side)
         cost = abs(filled) × fill_price × (fee_bps + impact_bps(abs(filled), bar.volume)) / 1e4
         book.apply_fill(inst, filled, fill_price, cost); turnover += abs(filled) × fill_price
         qty_remaining −= filled
   ```

   Sell-side mirror: trigger on `bar.high` (`bar.high < L_sell` → not touched,
   `bar.high > L_sell` → through, `==` → touched/partial), `fill_price =
   max(bar.open, L_sell)` on gap-through.

3. **Market `pending`** path — only used when `limit_fill is None` (mutually
   exclusive with the resting book; never both for the same decision).
4. **Close mark + financing + equity record** (unchanged).
5. **Target decision** (new reconciliation against the resting book — below).

## Lifecycle & target-tracking handoff

At the close decision, for each instrument with target weight `w`:

```
desired = round(w · equity / close / lot) · lot
delta   = desired − current_position
if an order is resting for inst:
    if desired == current_position:        sign satisfied → cancel
    elif sign(delta) != resting.side:      direction reversed → cancel
    else:                                   keep it (SUPPRESS re-submit — no double-count)
elif delta != 0:
    open RestingOrder(side=sign(delta), qty_remaining=delta,
                      limit_price = close × (1 − off) if buy else close × (1 + off),
                      queue_ahead = queue_fraction × bar.volume,
                      bars_alive  = 0)
```

End of each bar: `bars_alive += 1` for every resting order; any with
`bars_alive > expire_bars` is cancelled (removed). A partially filled order keeps
its remaining quantity and accumulated (decremented) `queue_ahead` and continues
to rest until filled, expired, or reversed.

Cancellation is pure state removal — no cash or position effect.

## Error handling / fail-closed

- A resting order's instrument absent from the current bar → `ValueError` (same
  policy as the existing `pending` path's "no bar for queued instrument").
- Non-finite target weight → `ValueError` (unchanged).
- `queue_fraction < 0`, `participation <= 0 or > 1`, `expire_bars < 1`,
  `limit_offset_bps < 0` → `ValueError` at `LimitFill` construction.

## Determinism

No RNG, no wall-clock. Resting orders iterated in `sorted(instrument_id)` order;
integer shares; fixed-point arithmetic. Identical inputs → byte-identical outputs
(asserted by a test).

## Test matrix (`tests/research/test_limit_orders.py`, tests-first)

1. `limit_fill=None` path byte-for-byte equals the `NextOpenFill` baseline (zero
   regression).
2. Not touched (`low > limit`) → no fill, order survives to next bar.
3. Through (`low < limit`) → full fill at `min(open, limit)`.
4. Touched (`low == limit`) → partial fill bounded by BOTH `participation` and
   `queue_ahead`; larger `queue_fraction` ⇒ ≤ fill (conservative monotonicity).
5. Queue accumulates across bars: repeated touches decrement `queue_ahead` until a
   later bar fills (the core fidelity claim).
6. `expire_bars` reached unfilled → cancelled; position unchanged.
7. Target reversal → immediate cancel.
8. Re-submit suppressed while an order rests (no double-count: position never
   overshoots desired).
9. **Reconciliation residual ≈ 0** in a mixed scenario (some filled, some partial,
   some unfilled/cancelled) — the platform's core invariant.
10. Determinism: same inputs twice ⇒ equal results.
11. Sell-side mirror: a resting sell limit fills on `bar.high` crossing.

## Files

- `services/research/backtest_engine/limit_orders.py` — `LimitFill`, `RestingOrder`.
- `services/research/backtest_engine/event_engine.py` — additive `limit_fill`
  parameter + resting-book branch (guarded; `None` keeps the existing path).
- `tests/research/test_limit_orders.py` — the matrix above.

## Safety review

- No broker / FIX / CTP / order-submission anything; this is a research backtest
  fill model over historical bars.
- No live surface, no credentials, no agent authority change.
- Pure addition, opt-in; default path unchanged; reconciliation identity preserved
  by construction.
