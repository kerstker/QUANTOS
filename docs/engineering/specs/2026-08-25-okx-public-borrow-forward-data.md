# OKX Public Borrow-Cost Forward Data

**Status:** implemented data foundation; L2 research only; not an Alpha, gate,
promotion artifact, execution input, or trading permission.

## 1. Mechanism before signal

The economic payer under study is a capital-constrained short seller or
cross-venue arbitrageur who must borrow an asset. Borrow cost can reflect the
interaction of short demand, lender supply, disagreement, and balance-sheet
constraints. The prior is deliberately broad:

- Atmaz, Basak and Ruan, *Dynamic Equilibrium with Costly Short-Selling and
  Lending Market*, RFS 2024, DOI `10.1093/rfs/hhad060`;
- Duong, Huszar, Tan and Zhang, *Information Value of Stock Lending Fees: Are
  Lenders Price Takers?*, Review of Finance 2017, DOI `10.1093/rof/rfw075`.

Both papers concern equity lending. They motivate measuring the mechanism; they
do not establish a crypto direction, holding period, or executable return.

## 2. Reviewed public source

The collector uses only the official, keyless OKX Savings market endpoints:

- `GET /api/v5/finance/savings/lending-rate-summary`;
- `GET /api/v5/finance/savings/lending-rate-history?ccy=...&limit=100`.

OKX documents `avgRate`, `preRate`, and `estRate` as annual borrowing rates.
Hourly history contains source timestamp `ts`, annual borrowing rate `rate`,
and annual lending rate `lendingRate`. The public `avgAmt`, `avgAmtUsd`, and
`amt` fields are deprecated and empty.

The implementation never calls an account endpoint, accepts credentials,
signs a request, transfers assets, or places orders.

## 3. Honest capability boundary

This source provides `BORROW_RATE`. It does **not** provide
`BORROW_AVAILABILITY`.

`services/data/research_surfaces.py` therefore advances the combined
`borrow_survivorship` surface from `MISSING` to `PARTIAL`, while leaving these
capabilities missing:

- actual lendable quantity or account-specific quota;
- delisting history;
- historical constituents.

A non-empty deprecated amount field fails the collector closed. It is not
silently relabelled as a locate or borrow quota.

## 4. Time and provenance contract

Each daily run uses a single actual fetch time `observed_at_ms`.

- Current summaries use `event_time = knowledge_time = observed_at_ms`.
- Hourly history retains OKX `ts` as event time but uses
  `knowledge_time = observed_at_ms`.
- A future source timestamp is rejected.
- Raw JSON responses enter the content-addressed raw archive first and their
  SHA-256 digests are bound into the snapshot.
- Only the latest settled hourly observation per currency enters the
  bitemporal forward-vintage database on each run.

Downloaded history is useful for schema and descriptive diagnostics, but is
not back-dated into formal forward evidence. The first successful DGX fetch is
the earliest possible formal-forward knowledge time.

## 5. Artifacts and operations

- Snapshot: `data/cache/okx_public_borrow_rates.json`
- PIT vintages: `data/cache/okx_borrow_vintages.db`
- Raw authority: `data/cache/raw_market_data/`
- Fetcher: `tools/okx_borrow_fetch.py`
- Scheduled wrapper: `tools/okx_borrow_forward_refresh.py`

The worker is scheduled daily at 09:55 Asia/Shanghai with a five-minute bound,
is monitored by the L2 watchdog, and its SQLite forward vintages are an
optional encrypted-backup source. Missing optional data cannot invalidate
existing core L2 authorities, but stale or failed scheduled collection is
reported by the watchdog.

## 6. Explicit non-goals

This slice does not:

- define a long/short rule or inspect future returns;
- claim that a high borrow rate means borrow is available;
- promote an Alpha or alter any lifecycle state;
- change paper fills, risk gates, broker adapters, or live flags;
- authorize small-capital validation.

A predictive experiment may be proposed only after this data accumulates and
must separately freeze payer, sign, horizon, universe, costs, independent bet
unit, controls, trial ledger, and terminal decision rule before outcomes are
opened.
