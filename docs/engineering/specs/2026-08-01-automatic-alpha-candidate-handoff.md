# Automatic Alpha Candidate Handoff

Date: 2026-08-01
Readiness: `L2_DRY_RUN_ONLY`

## Problem

The console previously exposed only the three execution-paper books even though the
forward ledger contained ten challengers and the scheduled factory continuously
evaluated a much larger search population. This collapsed three distinct populations:

1. factory trials and verified survivors;
2. attested forward-paper challengers;
3. nine-engine-approved execution-paper books with real-time public marks.

The factory artifact also stated that it drove no lifecycle, so a genuinely new PASS
survivor had no durable, machine-verifiable handoff into forward paper.

## Contract

`tools.crypto_factory_loop` publishes schema v2 and records the complete evaluated
count, search identity, dataset identity, discovered chain, and the subset eligible for
handoff. Eligibility requires an immutable experiment-registry receipt whose signature
chain verifies and whose verdict, alpha, search, dataset, and definition identities all
match. A search result cannot self-assert eligibility.

`tools.alpha_candidate_admission` is the only factory-to-forward seam. It re-verifies
the experiment receipt and appends the candidate to
`services.research.paper_candidate_registry.PaperCandidateRegistry`. The registry is a
SQLite hash chain with database triggers that reject updates and deletes. Exact replay
is idempotent; conflicting identity fails closed.

`tools.forward_track_advance` merges its pre-registered challengers with verified
registry entries on every scheduled run. A missing registry is an honest empty dynamic
population. A broken chain, malformed expression, or collision with a static Alpha is a
hard failure. Dynamic candidates enter only the attested forward-paper ledger.

## Stage separation

The read-only console projection reports all stages independently:

- factory survivor count and search dimensions;
- full forward-paper population;
- execution-paper population with public real-time marks;
- candidates still waiting at the forward-to-execution handoff.

The current verified state is 0 new factory survivors, 10 forward challengers, 3
execution-paper books, and 7 forward-only challengers. This is not a promise that all
parameter variants deserve simulated fills. Each forward-only candidate currently has
one of eight required held-out periods and lacks an independently verified evaluator
row plus a complete candidate-specific nine-engine packet.

## Safety boundary

Admission grants forward-paper observation only. It does not change lifecycle state,
grant promotion, construct a human actor, enable broker connectivity, access credentials,
or submit an order. All artifacts explicitly retain:

- `paper_only=true`;
- `live_allowed=false`;
- `order_submission_allowed=false`.

The next slice must make evaluator metadata and nine-engine inputs candidate-generic,
then let the existing fail-closed execution-paper path consume only candidates that meet
the same evidence and maturity contracts. It must not weaken the eight-period forward
gate or reinterpret a family of parameter variants as independent discoveries.
