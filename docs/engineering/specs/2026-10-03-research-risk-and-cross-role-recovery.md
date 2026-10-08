# Original risk evidence and cross-role recovery

Status: reviewed development increment after
`42d4b0913e16025ba546f59a8b884a5b934ee6fa`, not release acceptance,
deployment or complete system delivery. The preceding frozen increment is
[project review persistence](2026-10-03-project-review-persistence.md).

## Research risk from actual retained inputs

The new technical adapter reconstructs only an explicit allowlist of native
input classes. It rebuilds all three original manifest input trees through the
existing producer, repeats the original successful computation, and requires
the complete original result to match. It separately replays the same canonical
ledger and compares each covered risk-history equity point with that ledger.
Events before the available source cut remain uncovered. Same-timestamp event
ordering, funding, fees, short valuation and liquidation remain the existing
ledger and runner's algorithms.

The first regressions demonstrated a real risk-key mismatch and a self-consistent
caller equity history that did not come from the ledger. An explicit key
mismatch is rejected. Matching keys do not prove strategy ownership, multiple
independent strategy legs, model metadata or regime provenance. The adapter
therefore reports INSUFFICIENT or REJECTED and preserves all original false
attestations. Repeating the reviewed implementation proves reproducibility,
not independent mathematics or economic authority.

The first bounded combination passed 217 tests in 33.48 seconds, including
27 new native cases. The registered technical producer now accepts a V3 plan
whose pre-computation policy binds the input, source, original evaluation core
and native trial. It retains risk-replay.json after the original native report
and before its V2 terminal outcome. A completed native computation retains its
PASSED status even when the separate risk diagnostic is REJECTED or INSUFFICIENT.
FAILED and interrupted native attempts remain in the project population.

Interrupted publication can continue only from the same actual durable native
report. Missing terminal history is not repaired; partially written artifacts
remain failures rather than being silently replaced. The fixed snapshot reader
checks the actual source and pinned trust before repeating the risk calculation,
then includes all originals in its final read-set check. The pure validation
layer consumes the retained diagnostic without importing the execution engine.
V3 facts separately prove policy preregistration while preserving the old raw
adapter report and its limitations; its original absent-plan limitation is not
rewritten as if that adapter had consumed the later plan-aware interface.

Independent SOURCE/RESULT statements must postdate the latest actual fact in
the entire project cut, including invocation, outcome and risk-publication times
from another interrupted run. Real fork regressions cover these three boundaries.
V1/V2 runs cannot acquire retrospective risk attachments. A changed risk artifact
requires a new independently signed cut; the existing exception for additional
validated review metadata is unchanged.

The final thirteen-suite lifecycle combination passed 265 tests in 285.36
seconds. It includes original producer/registry compatibility, actual process
interruptions at publication boundaries, fixed source/trust rejection, original
review persistence and cold revalidation. Zero-length and partial risk files
remain intact and reject; these process-interruption checks do not certify
power-loss durability. Separate architecture/import-graph checks passed 23 tests;
the graph has twenty domains, fifty-two edges and no cycles. Full Python E9/F821
checks also passed. No full canonical acceptance was run for this increment.

## Issuer consumption and executor history

`tools/demo_gold_issuer_executor_join.py` reads isolated original issuer and
executor databases through the existing private DB/WAL snapshotter. The native
issuer reader checks its complete event reducer, signatures, budgets and real
claims index; the native dispatch reader checks its reservation and wire chains
and actual reserved-order index. No new cash or allowance ledger is introduced.

Every executor submission must match the exact original SIGNED envelope and
its preceding RESERVED plan, policy, registry and approval identity. Account,
deployment, context and orders must agree. The original proposal leaf must have
the committed digest. Signature verification uses the original executor
reservation time, and the persisted SIGNED time must precede consumption.
Current expiry does not restamp or invalidate an otherwise proven old event.

Unsigned reservations and signed-but-undelivered submissions remain in the
full issuer history and are reported separately. Cancellation delivery is not
inferred from submission rows. A signed cancellation remains explicitly
unchecked until its own executor history is joined. This distinction had a
real failing regression before the reporting correction.

An optional original cancellation database now adds a separately versioned V2
grant report. Each native cancellation GRANT must match its original issuer
RESERVED/SIGNED pair, retained cancellation proposal, exact original submission
reservation and order IDs. The signature must already exist at executor
consumption. Full native claims and results remain in the history digest;
unclaimed grants, interrupted claims, UNKNOWN results and observed responses
have separate counts. An observed cancellation response is not cash or OMS
reconciliation. The original query-prefix proof is still explicitly unchecked.
Omitting the cancellation database preserves the V1 submission-only report and
its unchecked-cancellation limitation.

The initial ten tests failed on the missing API, then passed. The expanded
native tests include an actual Gold entry and exit with five issuer events
(including one unsigned consumed reservation), two executor reservations,
nonempty original WAL, missing original claims, missing SIGNED ancestry,
signature/consumption ordering and identical-byte inode replacement. The final
bounded combination passed 52 tests in 4.82 seconds; static checks passed.
These tests create no real credentials or network orders. Original fixture
files remain byte-identical across successful reviews.

The cancellation extension first produced seven missing-API failures. Its next
check had twenty passes and one test-only error: IssuerStore has no context
manager protocol. Wrapping that fixture in the existing closing context fixed
the setup; no production validation changed for it. That four-suite combination
passed 65 tests in 6.31 seconds. Independent review then reproduced a protected
path bypass: a sibling/../protected path passed the literal protected-root
comparison. A new native regression failed before the fix; the reader now
rejects parent traversal before filesystem inspection. The final four-suite
combination passed 67 tests in 6.63 seconds, with static checks passing. All
protected roots in this regression are isolated temporary directories. This
does not supersede the original full-gate failure or prove complete recovery.

The operation-local handle returns detached original records and rechecks
original files before reuse. Its registry comes from issuer history; independent
fixed-public custody and registry continuity are still required before treating
that registry as trusted input to the complete checkpoint. The result explicitly
sets `whole_checkpoint_set_verified`, `submission_enabled` and
`economic_authority` to false.

## Fixed originals and historical position recovery

The fixed public provider reads actual mirrored authority registries, policy,
partition records, installation intent, activation and installation-history
identities. It distinguishes a verified absence from missing or partial
installation evidence. Capture rechecks original bytes, parent identities and
leaf identities. An archive must bind the exact public snapshot, cut, inventory,
role receipt and complete original index, including its native shard hashes.
It uses archived originals when restored and does not read today's fixed paths.
The provider's four-suite combination passed 90 tests in 11.82 seconds, including
a real parent-directory replacement regression. Those tests isolate root/role
custody and accepted-release inputs; they do not prove a real writer barrier.

The historical position reader matches the original held-position commitment
against complete native reservation, wire, recovery and order-event prefixes.
Original signed grants are verified at their original reservation time against
an exact registry append prefix. Every required order event is rebuilt with the
native factory and compared field by field. It reuses the original canonical
position/cash reducer and privately snapshots original DB/WAL input; it does not
reconstruct earlier holdings from today's flat balance or rewrite journal paths.

New V2 semantic inventories record the fixed-public commitment only after an
actual operation-local verified handle was consumed. Restore revalidates this
commitment and the complete archive before rebuilding that handle. V1 archives
and originally partial V2 archives keep their original semantics; embedding
unverified public JSON cannot improve a partial capture on restore.

The native unpartitioned Gold held-to-flat fixture, a later original query,
archive transport and different-path restoration passed, including restoration
after the original fixed-public directory was removed. The eleven-suite joint
check passed 203 tests in 466.63 seconds; all thirteen owned files and two fixed
provider dependencies were byte-identical before and after. Existing installed
BTC/Gold partitions and reported-event cuts remain explicitly unproven until
their actual original histories are covered by a corresponding combined test.
The test groups overlap and are not summed into a full-gate claim.

## Remaining integration

The complete cross-role set, research-state recovery, real writer barrier and
root-to-role worker protocol remain required. The current native Gold inventory
does not yet cover the research role's complete Gold state. Historical cancellation
query replay, source-version transitions, final same-version acceptance, authorized field
installation/recovery and continuous operation are also outstanding. No partial
component report or signed research diagnostic closes those requirements.
