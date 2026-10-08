# Production Paper operational-window evidence

Status: approved implementation slice for WP2.  This document does not weaken
the eight-period Alpha promotion gate and does not authorize broker access.

## Problem

The realtime shadow-paper loop has current-cycle receipts, append-only order and
mark ledgers, a scheduler watchdog and a dynamic console.  Those point-in-time
checks do not prove an unattended operating window, recovery across a planned
release restart, or fail-closed behavior under injected faults.  WP2 therefore
cannot be called complete from a single green screenshot.

## Contract

`paper_operational_evidence.v1` adds an append-only hash-chained SQLite authority
at `data/cache/paper_operational_evidence.db` and a bounded read-only projection
at `data/cache/paper_operational_evidence.json`.

Every five minutes, after `paper_shadow_realtime`, the collector:

1. consumes immutable `paper_shadow_realtime` release receipts;
2. binds only the receipt whose start/finish envelope contains the current
   shadow artifact's `upstream_cycle` timestamps;
3. records release identity, current-cycle refresh, 10-candidate coverage,
   ledger/reconciliation and the paper-only safety boundary;
4. treats missing, malformed, conflicting or unbound evidence as non-success;
5. reruns an isolated fault matrix against temporary stores, never the live
   paper ledger; and
6. publishes smoke-window and production-window maturity separately.

The window is evaluated on the active release cohort: the release identity on
the newest cycle defines the current operating window.  Cycles from predecessor
releases remain append-only and are reported as retained history, but do not
poison the current release's success-ratio denominator.  A newest failed cycle
still belongs to the active cohort and therefore fails closed immediately.
Release-transition recovery evidence is evaluated across retained accepted
cycles, so cohort isolation does not hide a restart or deployment failure.

The pre-registered policy is fixed in code:

- cadence: 5 minutes; maximum accepted observation gap: 12 minutes;
- smoke window: at least 30 minutes and 6 accepted observations;
- Production Paper window: at least 24 hours and 274 accepted observations;
- accepted-cycle ratio: at least 99%;
- planned release restart: an accepted cycle on each side, recovery within
  15 minutes;
- required isolated drills: exact retry idempotency, conflicting duplicate
  rejection, hash-chain tamper detection, stale-cycle rejection, unsafe broker
  boundary rejection and incomplete-candidate rejection.

The 30-minute result is an engineering smoke gate only.  It cannot make
`production_window_ready=true`.  The 24-hour clock accumulates while other WP2,
WP3 and WP4 development continues; no development task waits for it.

## Safety and honesty boundaries

- Public market data and simulated paper fills only.
- No credentials, private endpoints, broker adapters or order submission.
- Fault injection uses temporary databases and synthetic artifacts only.
- The collector never mutates official paper admission, Alpha lifecycle,
  forward evidence or promotion state.
- A successful engineering smoke window is not Alpha evidence and is not a
  substitute for formal Execution Paper admission.
