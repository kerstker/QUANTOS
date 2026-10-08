# Original-registry persistence of independent project reviews

Status: development after `39f01d20ec48716d2cca2e2807dfb084fa57edc3`;
not release acceptance, deployment, economic admission or complete delivery.

The [registered-project increment](2026-10-03-registered-project-research-closure.md)
binds all native attempts and a pre-execution evaluation plan. This increment
retains independent reviews through the original registry and verifies their
relationship to the current project before reuse.

## Review time and the original transaction

A registered computation finishes its search before an independent review can
occur. The original `search_evaluations` chain describes activity during the
search and correctly rejects activity after its terminal event. A V2 project
review therefore uses the existing seven-column `evaluations` row and original
audit outbox in one store-owned transaction, with no search-activity link. Its
signed subject, exact native trial, original search identity and terminal event
provide the binding. No table, parallel registry or backdated event is added.

`QUANTOS_PROJECT_RESEARCH_EVIDENCE_ARTIFACT_V2` contains the original signed
snapshot, recomputed assessment, search identity and configuration hash. The
selected computation must genuinely be finalized. FAILED computations can retain
diagnostic reviews; other failed or unfinished attempts remain in the population.
Both signed statements must be issued after the latest retained factual event.
The review's actual check time remains separate from computation time.

The identity binds the subject and both envelopes. A valid retry may observe the
same proof later, but returns the exact first row, timestamp and audit unchanged.
Changing signed facts, trust, selection or source conflicts. A missing audit or
an unexpected activity link is corruption; retry does not repair it.

## Signed history and current use

A persisted row embeds only the original signed cut. It cannot embed a cut that
already contains itself. The current project reader includes every evaluation's
metadata without recursively embedding its body.

Fresh preflight checks both complete cuts. Every run, raw original, native ledger,
validation receipt, trial, provenance, OOS access and search activity must remain
exact. Evaluation metadata is keyed by run ID rather than list-prefix order.
Only additional fully validated V2 reviews, with their exact original audit and
no activity link, are allowed. Any new trial, ordinary evaluation, changed native
input or OOS access requires another independent review. A historical getter can
still verify the old proof, explicitly without current admission.

The fixed reader continues to select its source tree, database and pinned trust
file from application configuration. Retained JSON chooses none of those paths.
It rereads the actual source/trust and all project files, then verifies the final
clock after expensive work. An expired or changed proof rejects the transaction.

## SQLite lease and actual original-file reads

Real integration first reproduced `TRIAL_ROLLBACK_JOURNAL`: inserting the pending
row under the original default SQLite journal mode creates the writer's rollback
journal. Copying that database through a second connection cannot represent the
uncommitted transaction safely. The ordinary reader's journal rejection remains.

The store creates a bounded read capability only inside its actual write lease.
It binds the real database path and file identity, rejects use after the lease,
and verifies that the current registry equals the original view or exactly that
view plus the pending review/audit. It supplies the original committed view to
the project reader. The reader still checks database custody and rereads every
native run/ledger, complete source tree and trust file. This capability is absent
from ordinary/fresh reads. No WAL-only test substitution or external journal
bypass is used.

## Evidence and limits

The original missing-artifact API produced 16 failing regressions, followed by
16 passes. Two correctly re-signed pre-fact statements then reproduced an actual
time-binding omission; strict statement time checks fix it. The initial real
registry combination exposed four rollback-journal failures with 25 passes.
After the lease integration, the artifact, fixed reader, registry facade and
authority combination passed 39 tests. The native store checks passed 23 tests,
including four actual process interruptions and cold-read compatibility. The
final twelve-suite combination passed 258 tests in 257.13 seconds; its 23 selected
source/test files have identical before/after hashes. Eight additional persistence
port and governance boundary checks passed. These groups overlap and are not a
full release gate. Source checkpoint and deployment remain separate.

This is proof persistence and fresh verification. The candidate gate still needs
its actual configured consumer; locked OOS, benchmark/multiple-testing policies
and independent risk replay remain incomplete. Qualification remains INSUFFICIENT
or REJECTED and both promotion/submission flags remain false. Full Gold/research
recovery, final-source acceptance, authorized installation and continuous field
verification are still separate required parts of complete system delivery.

## Measured repeated work in the delivery checks

A separate exact-5ae, three-node diagnostic completed its two native delegation
operations and all 8,732 origin-retention frames without changing source or the
original full-gate time budget. It confirmed that V2 delegation omitted the
existing definition-read session. This increment passes that session through the
original V2 material reader and all checks within one REGISTER/ENABLE operation.
Source, archive, trial and runtime rechecks remain at their original boundaries;
there is no global cache or cross-operation reuse.

In one isolated measurement per original operation, with the JSON implementation
held constant and no profiler, REGISTER took 5.200 then 2.241 seconds; ENABLE took
14.392 then 6.021 seconds. Definition construction counts fell from eight and 21
to one each. These are local measurements, not field latency or full-gate claims.

The shared strict JSON parser now skips its Python surrogate scan only for ASCII
strings; non-ASCII strings use the original check. Duplicate keys, invalid UTF-8,
non-finite numbers and escaped surrogates remain rejected. Five batches of 2,000
parses of one actual 7,254-byte retained origin had median times 0.50435 and
0.14398 seconds with identical parsed-result hashes. The 27 related JSON checks
and nine native V2/session-change checks passed. The final eight-suite native
and compatibility combination passed 115 tests in 351.32 seconds on the exact
parent plus these six files, with the complete Python tree unchanged across the
run. Original failing regressions remain retained. This does not replace the
original full-gate failure or establish the final version's acceptance.
