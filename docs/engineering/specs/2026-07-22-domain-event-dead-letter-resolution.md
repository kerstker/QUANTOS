# Domain-event dead-letter resolution (L2)

## Result

The original projection candidate had a hash mismatch for event sequences 1
and 2. The projection was rebuilt from the append-only event authority and
was already exact at authority head `25805`; the consumer journal still
retained two historical `DEAD_LETTERED` rows and five historical `FAILED`
rows.

`tools/domain_event_dead_letter_resolve.py` now provides an explicit,
append-only redrive. It first performs a read-only authority/projection
consistency proof and verifies that each dead-letter event is present in the
projection with the same event ID. Only then does it append a new
`CLAIMED`/`ACKED` pair with the fixed recovery worker ID. It never edits the
projection or event authority and never deletes historical journal rows.

The consumer health/consistency views count only unresolved terminal failures
for the fail-closed gate while retaining `historical_failed_deliveries` as a
separate audit field. After the approved run:

- unresolved dead letters: `0`;
- event consistency: `PASS`;
- event health: `PASS`;
- authority/projection: `25805 / 25805`, prefix mismatch `0`;
- historical failures retained: `5`.

The release worktree uses the same unresolved-dead-letter query so the next
scheduled event-ops run cannot regress the health verdict. This is an L2
paper/research recovery only; it grants no broker, credential, order, or live
trading capability.

## Verification

- `tests/tools/test_domain_event_dead_letter_resolve.py`: 2 passed;
- monitoring/event-health and consumer tests: 17 passed;
- combined targeted gate: 125 passed;
- governance scanner: 325 files, 0 violations.
