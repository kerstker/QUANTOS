# BTCUSDC bounded autonomous research specification

Frozen before this batch's candidate results. User requests QUANTOS-native
calculation, new hypotheses if old candidates fail, then system gap diagnosis.
No claim of guaranteed Alpha or permission to bypass human live gates.

## Data and execution

BTCUSDC USD-M perpetual ONLY; original USDT and spot artifacts retained.
Official trade-price and mark-price minute archives, 2024-01 onward, through
2026-08-01; checksums verified with explicit market identity. Existing frozen
BTCUSDC actual funding settlements reused. Evaluation: 2025-01-01 00:05 UTC
through 2026-08-01 00:05 UTC (577 execution days). All is SEEN_HISTORY, not virgin
OOS. Current rules as a user-authorized hypothetical historical scenario.
Missing evaluation minutes or funding coverage block computation. Incomplete
warmup days excluded; no cross-market fill or future repair.

Initial collateral 1500 USDC, target absolute notional 25% equity or flat;
daily rebalance at next 00:05 minute open, fee 4 bp taker, adverse slippage
2/5/10 bp. Long/flat A/B/C preserve their submitted rules. Previous mature-label
Ridge specification remains fixed (730 samples); BTCUSDC perpetual has a shorter
history, so start only when its own samples suffice. No spot-trained substitution.
Observe mark at minute opens and actual funding events; cumulative loss 150
permanently stops new risk and attempts exit at the next minute execution proxy.
Also report the path without that stop. Market-price and mark-price are distinct.

Reuse the canonical Decimal perpetual ledger for fills, cash, funding, margin
and equity. Hypothetical conservative margin profile: 100% initial margin and
50% maintenance, as in the earlier technical model; NOT a Binance leverage tier
or liquidation calibration. A run that requires unsupported liquidation facts
must fail. Quantity step/minimum .001 BTC, tick .1, minimum notional 50 USDC,
maximum market quantity from the frozen current profile. Capacity at most 1%
of previous complete-day quote volume, shared over the UTC day. No maker fill
claim, account access, paper-fill persistence, promotion, or live order.

## Search, registered before evaluation

First: A (28-day long/flat momentum), B (55/20 close breakout), C (daily Ridge),
cash and daily passive25 benchmark. All six cost/risk cases retained.

Second, if none supports further review: reuse QUANTOS HypothesisSpace and
UCBSearch to enumerate a bounded grammar, not a claim of LLM-generated science.
Factors: completed daily close return, settled daily funding sum; operators:
trend/fade; windows: 3 and 14 days. Eight new hypotheses, each target +25%/-25%
by the sign of its trailing factor mean, inverted for fade, zero at exact zero.
Price trend: information diffusion; price fade: temporary inventory pressure;
funding fade: crowded positioning reversal; funding trend: sustained directional
demand. These are hypotheses, not discovered edges. The window grid and family
are fixed; no extra search until this complete family is reported.

All candidate and baseline cost/stop cases enter append-only trial records
before replay. Rank/feedback only on 2025 cost-5bp results. 2026 results are
descriptive checks, never used to generate new parameters within this batch.
No repeated search until something happens to look profitable. Unknown inputs
and failed trials retained. Multiple-testing population includes all prior
history; no claim of completed DSR/PBO or formal admission.

Further-review screen (not promotion): 2025 and 2026 net PnL positive at 5 bp;
net PnL positive in every registered cost scenario; at least 10 fills; positive
market-adjusted HAC alpha with t > 2.4; minute max drawdown <=10%; net PnL after
removing five best daily PnL increments still positive. Report every failure.

## QUANTOS integration and audit

Expose a bounded diagnostic RunKind through existing JobQueue/RunRegistry,
fixed input/output paths within the development state root, immutable result
identity and source hashes. No arbitrary source/shell/path request parameters.
Autonomous hypotheses are registered and evaluated without operator intervention
within the bounded family; legacy economic paths remain quarantined.
Audit dataset, hypothesis generation, signals, backtest, validation, forward
observation/Paper, recovery/risk and live authorization separately. Runtime
readiness follows actual gates; implementing a diagnostic loop does not make
the institutional blueprint complete or automatically authorize real capital.
