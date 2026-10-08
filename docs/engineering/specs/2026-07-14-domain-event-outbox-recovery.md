# Domain Event Outbox Recovery Drain (2026-07-14)

## Scope

The six transactional outboxes already protect domain state from the
commit-before-publish crash window. This increment adds the missing independent
recovery driver so pending records do not depend on the original business
process running again.

The driver covers:

- dry-run and its research-risk event;
- alpha promotion / experiment catalog;
- alpha maintenance;
- kill-switch transitions;
- deployment evaluations; and
- market-data refresh history.

## Invariants

- Source databases are opened with `create=False`; a missing source is reported
  as `UNAVAILABLE`, never synthesized.
- If every source and the event authority are missing, the driver returns an
  unavailable snapshot without creating empty databases.
- Each present source is verified by its owner store before dispatch.
- One corrupt source is `BREACH`, but other independent sources still drain.
- Exceptions retain only their type. Database contents, paths beyond the
  configured local path, and free-form errors are not copied into alerts.
- Dispatch remains publish-then-append-ack and at-least-once. Existing
  deterministic event IDs make a crash after publish safe to retry.
- The shared projection advances once after all source attempts, rather than
  once per source.
- The driver never evaluates a strategy, changes domain state, approves a
  deployment, resets a kill-switch, creates an order, or accesses credentials.

## Honest Boundary

This is a single-node recovery scheduler over local SQLite authorities. It is
not a broker, distributed transaction, exactly-once protocol, cross-host
replication mechanism, or independently deployed event service. Scheduling and
external alert-delivery SLA remain operational work.

## Verification

- Recovery drain tests: `7 passed`.
- Broader outbox and governance regression: `77 passed`.
- Unified fail-closed gate: `2322 passed, 1 skipped`; live/trading/tools
  `927 passed`; frontend `68 passed` plus typecheck and production build.
- Governance scanner: `311 files / 0 violations`.
- Event schema catalog (`11 types / 16 definitions`), service import graph
  (`16 domains / 36 edges / 0 cycles`), 320-component supply-chain baseline,
  top-tier assessment, Ruff, and diff checks are current.
