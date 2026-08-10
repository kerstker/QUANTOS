# DefiLlama TVL forward-vintage accumulation

Date: 2026-08-11  
Scope: L2 public-data research only

## Problem

The DefiLlama Free API exposes useful chain-TVL history, but the returned
history is retrospective and revisable.  It does not expose the historical
vintages that would prove what value was knowable on an earlier date.  The
existing snapshot therefore correctly carries
`pit_admissible_for_promotion=false`, and the formal evaluator must continue to
block it.

Leaving the source only as a retrospective snapshot creates a permanent gap:
even future observations would never acquire honest point-in-time provenance.

## Decision

Each successful public fetch will continue to archive the complete raw
response and write the retrospective diagnostic snapshot.  In addition, it
will project exactly one forward observation cross-section into a bitemporal
SQLite store:

- `event_time` is the latest completed UTC day represented by at least ten
  valid mapped assets;
- `knowledge_time` is the fetch's `fetched_at_ms`;
- only finite, positive TVL values for that selected day are stored;
- older historical rows from the same response are never projected;
- a later fetch for the same event day is retained as a distinct restatement;
- an exact rerun is idempotent through the existing composite primary key;
- a batch is committed atomically or not at all.

Because these forward observations cannot be reconstructed later, the SQLite
store is an optional member of encrypted L2 backup source contract v6.  It is
optional only to permit a clean installation before its first successful
daily fetch; once present, backup and restore verification include it.

The feature id is `defillama_chain_tvl_forward_vintage`.  The default store is
`data/cache/defillama_tvl_vintages.db`.

## Fail-closed boundary

Projection rejects a snapshot when its source/provenance/revision contract is
not the known DefiLlama diagnostic contract, its fetch time is invalid, or no
completed day reaches the minimum breadth.  The existing retrospective
snapshot remains explicitly non-PIT and cannot be consumed by the formal
admission evaluator.

This slice does not:

- lower the formal history, breadth, DSR, PBO, cost, or forward gates;
- backfill historical knowledge times;
- generate a Testnet intent or start an execution service;
- access credentials, brokers, accounts, or order endpoints;
- claim the new forward series is mature enough for Alpha admission.

## Acceptance

1. Atomic batch storage preserves restatements and rolls back malformed
   batches.
2. Projection stores only the latest sufficiently broad completed day.
3. The fetch command writes the forward-vintage store in addition to the raw
   archive and diagnostic JSON.
4. An allowlisted L2 wrapper schedules the public refresh daily at 09:30 local
   time with a ten-minute single-instance bound.
5. Tests prove no retrospective row is imported and insufficient breadth
   fails closed.
6. Governance and canonical validation remain green.

## Maturity path

The forward store starts accumulating evidence now.  A future, separately
reviewed evaluator may consume it only after sufficient calendar depth and the
existing formal gates are satisfied.  Until then the channel remains
diagnostic and formal Alpha eligibility remains unchanged.
