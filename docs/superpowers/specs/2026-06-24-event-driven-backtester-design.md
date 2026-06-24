# Event-Driven Portfolio Backtester — Design (sub-project 1 of N)

Date: 2026-06-24 · Status: approved · Scope: single implementation plan
Audit driver: `docs/QUANTOS_AUDIT_2026-06.md` §6 P1 — "No event-driven
portfolio-level backtester with realistic microstructure ... paper performance
will systematically overstate net alpha."

## 0. Decomposition (the full backtester is multiple sub-projects)

A world-class event-driven backtester is too large for one spec. Decomposition:
- **Sub-project 1 (THIS spec): event-driven portfolio engine core** — deterministic
  event loop over time-ordered multi-instrument bars; a Book (integer-share
  positions w/ signed average-cost, cash, equity) evolving bar-by-bar; target
  weights → delta orders → fills via a pluggable FillModel (baseline: next-bar
  OPEN + slippage, reusing `CostModel`); a per-bar net equity curve with an exact
  reconciliation identity. Long-short.
- Sub-project 2 (later): borrow / financing / short-locate costs.
- Sub-project 3 (later): partial fills / volume caps / VWAP / participation models.
- Sub-project 4 (later): corporate actions mid-sim; intraday funding.

This spec builds ONLY sub-project 1.

## 1. Goal

A deterministic, pure-Python event-driven portfolio backtester that, given a
time-ordered stream of bars and per-timestamp target weights, evolves a Book
bar-by-bar with **next-bar-open** integer-share fills (lot-rounded) and produces a
net-of-cost equity curve whose change each bar reconciles EXACTLY to
`realized_pnl + Δ_unrealized − costs`. This is the core that stops
instantaneous-rebalance overstatement of net alpha.

Constraints: L2_DRY_RUN_ONLY; pure-Python stdlib; deterministic (no RNG);
fail-closed; no lookahead (a decision at bar i uses only info ≤ bar i, fills at
bar i+1 open); preserve the existing `run_backtest`/`run_panel_backtest`
(add a new module); no broker SDK / no live surface.

## 2. Architecture

New modules in the existing package `services/research/backtest_engine/`:
```
fill_model.py    FillModel protocol + NextOpenFill (baseline)
book.py          Book — integer-share positions, signed average-cost, cash, equity, PnL buckets
event_engine.py  run_event_backtest(...) — the deterministic event loop + EventBacktestResult
```
Reuse as-is: `CostModel` (costs.py), `metrics` (metrics.py), `Bar` contract
(quantos/contracts/data.py). The Book's average-cost rule MATCHES
`services/research/paper_trading/domain/reconciliation._apply` (long/short/flip
handling) but is reimplemented locally to keep the engine self-contained (no
cross-module import). Existing `engine.py` is untouched.

## 3. Components

### 3.1 `book.py` — Book
Integer-share position accounting with signed average cost.
- `@dataclass class Lot: qty: int; avg_cost: float`
- `class Book:`
  - `__init__(self, cash: float)`
  - `positions: dict[str, Lot]`, `cash: float`, `realized_pnl: float`, `total_cost: float`
  - `apply_fill(self, instrument_id: str, qty: int, price: float, cost: float) -> None` —
    signed average-cost: same-direction adds to the lot and re-averages; opposite
    direction realizes PnL on the closed quantity and, on a flip, rebases the
    remainder at `price`. Updates `cash -= qty*price + cost` (qty signed),
    `total_cost += cost`, `realized_pnl += closed_pnl`.
  - `equity(self, marks: dict[str, float]) -> float` = `cash + Σ qty·marks[inst]`.
  - `unrealized(self, marks) -> float` = `Σ qty·(marks[inst] − avg_cost)`.

### 3.2 `fill_model.py` — FillModel
- `class FillModel(Protocol): def fill(self, qty: int, bar: Bar, cost_model: CostModel,
  adv_shares: float = 0.0) -> tuple[float, float]` → `(fill_price, cost)`.
- `class NextOpenFill:` baseline — `fill_price = bar.open * (1 + sign(qty)*slippage)`,
  where `slippage = cost_model.slippage_bps/1e4` (buys pay up, sells receive less);
  `cost = |qty|*fill_price * (cost_model.fee_bps + cost_model.impact_bps(|qty|,
  adv_shares)) / 1e4` — fees + impact ONLY (slippage is already embedded in the
  fill price, so it is deliberately NOT taken from `trade_cost`, which would
  double-count it). Deterministic.

### 3.3 `event_engine.py`
- `@dataclass(frozen=True) class EventBacktestResult:` `timestamps: list[int]`,
  `equity_curve: list[float]`, `cash_curve: list[float]`, `total_cost: float`,
  `realized_pnl: float`, `turnover: float`; methods `final_equity()`,
  `reconciliation_residual()` (max bar-by-bar `|Δequity − (realized_step +
  Δunrealized_step − cost_step)|`).
- `run_event_backtest(timeline, targets, *, initial_capital, cost=CostModel(),
  fill=NextOpenFill(), lot_size=1) -> EventBacktestResult`:
  - `timeline: Sequence[tuple[int, dict[str, Bar]]]` — time-ordered `(ts, {inst: Bar})`.
  - `targets: Sequence[dict[str, float]]` — target weight per instrument decided at
    each timeline index (same length as `timeline`).
  - Loop per index `i`:
    1. **Open of bar i**: execute orders queued at `i-1` at this bar's OPEN via
       `fill` → `book.apply_fill`. (i=0 has no queued orders.)
    2. **Close of bar i**: `marks = {inst: bar.close}`; `equity_i = book.equity(marks)`.
       Compute desired integer shares per instrument from `targets[i]` on `equity_i`
       (`desired = round(w*equity_i/close/lot)*lot`), queue `delta = desired −
       current_qty` for fill at `i+1` open.
    3. Record `timestamps/equity_curve/cash_curve`; accumulate per-bar PnL buckets
       for the reconciliation residual.
  - The last index queues nothing (no next bar to fill).

## 4. Data flow

```
(ts, {inst: Bar}) stream + per-ts target weights
        │  decide at close i  ──────────────►  delta orders
        │  fill at open i+1 (NextOpenFill + CostModel)
        ▼
      Book (avg-cost, cash, equity)  ──►  per-bar net equity curve + reconciliation
```

## 5. Error handling — fail-closed + correctness

- **No lookahead**: orders decided at close i fill at open i+1; i=0 fills nothing;
  the final decision is never filled. A test asserts the fill price equals the
  NEXT bar's open (± slippage), never the same bar.
- **Reconciliation**: the per-bar identity `Δequity == realized_step +
  Δunrealized_step − cost_step` must hold to < 1e-6; the result exposes
  `reconciliation_residual()` and a test asserts it across shorts and flips.
- **Slippage not double-counted**: `NextOpenFill` puts slippage in the fill price;
  `cost` covers fees + impact only (a dedicated test asserts cost excludes slippage,
  and that net equity still reconciles with slippage embedded in price).
- **Fail-closed inputs**: `len(targets) != len(timeline)` raises `ValueError`;
  non-positive `initial_capital` or `lot_size < 1` raises; a target referencing an
  instrument with no bar at that timestamp raises (no silent skip); non-finite
  weights raise.
- **Determinism**: fixed iteration order, deterministic `round` (banker's rounding
  is fine as long as it is consistent), no RNG → identical inputs yield identical
  curves (a determinism test asserts byte-equality).

## 6. Testing (tests-first) — `tests/research/test_event_backtest.py`

1. **Next-bar-open fill, no lookahead**: a 1-instrument 3-bar run; target set at
   bar 0 fills at bar 1's OPEN (assert exact fill price = open*(1+slippage)).
2. **Per-bar reconciliation across long/short/flip**: 2 instruments, a long, a
   short, and a flip; `reconciliation_residual() < 1e-6`.
3. **Costs make net worse and still reconcile**: same run with fees/impact > 0 has
   lower final equity than the zero-cost run, residual still < 1e-6.
4. **Lot rounding**: `lot_size=100` → every position quantity is a multiple of 100;
   residual cash is tracked (equity still reconciles).
5. **Determinism**: two runs → identical `equity_curve` and `total_cost`.
6. **Fail-closed**: mismatched `targets`/`timeline` length raises; a target naming
   an instrument absent from that bar raises; `lot_size=0` raises.
7. **Book unit tests** (`tests/research/test_event_book.py`): `apply_fill`
   average-cost add / partial close (realized PnL) / full flip; `equity`/`unrealized`.
- Wire: new test files under `tests/research` (already in `scripts/validate.py`);
  governance scanner auto-covers the new modules; no live-surface imports.

## 7. Out of scope (later sub-projects / YAGNI)

Borrow/financing/short-locate costs; partial fills / volume caps / VWAP /
participation; corporate actions; intraday funding; limit orders / queue
position; multi-currency. Sub-project 1 is the event loop + Book + next-open
integer-share fills + exact reconciliation only.
