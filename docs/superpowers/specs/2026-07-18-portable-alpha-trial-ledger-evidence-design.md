# Portable Alpha Trial-Ledger Evidence Design

Date: 2026-07-18  
Status: proposed for implementation  
Safety level: `L2_DRY_RUN_ONLY`

## 1. Context

QUANTOS already has a real append-only experiment registry at
`data/cache/experiment_registry.db`. Its search-attempt, candidate, trial,
evaluation, and exact search-to-evaluation chains are machine verified. A real
offline `tools.crypto_factory_run` execution produced a new completed session on
the 115-instrument by 2680-period panel. The live authority now contains 40
trials and 10 immutable evaluations, and
`tools.search_trial_ledger_history --check` verifies two retained cross-session
history records.

That evidence is not yet repository-portable. `data/cache/` is intentionally
Git-ignored, so a fresh checkout does not receive the live SQLite authority.
The repository can therefore report `M4/PASS` on the originating workstation
but only `M3/PARTIAL` in CI. A top-tier evidence claim must not depend on an
untracked workstation cache.

## 2. Decision

Publish a version-controlled, read-only, data-minimized SQLite evidence snapshot
and a strict JSON seal manifest. Keep the mutable production authority under
`data/cache/` and Git-ignored.

The three artifacts form one portable evidence bundle:

- `evidence/alpha_trial_ledger_history.json`: the existing append-only history
  records and their record-hash chain;
- `evidence/alpha_trial_ledger_snapshot.db`: a derived, read-only SQLite snapshot
  containing only the five tables required to prove the global trial ledger;
- `evidence/alpha_trial_ledger_snapshot_seal.json`: hashes and counts binding the
  history file to the exact snapshot bytes and history head.

The top-tier repository assessment verifies the portable bundle. The operator
command `--check` continues to verify the mutable live authority. Repository
capability evidence and live operational health remain separate claims.

## 3. Alternatives considered

### 3.1 Commit `data/cache/experiment_registry.db`

Rejected. It conflates mutable runtime authority with versioned evidence,
causes ordinary research runs to dirty the worktree, and can copy unrelated
alpha state, bandit state, and event outboxes into Git.

### 3.2 Keep `M3/PARTIAL` and retain evidence only on the workstation

Safe but incomplete. It leaves a red-line capability dependent on one machine
and does not satisfy the goal of a CI-reproducible global trial ledger.

### 3.3 Export the entire ledger as JSON

Rejected. A JSON reimplementation would duplicate SQLite schema, trigger, and
integrity semantics. A minimized SQLite snapshot lets the existing verifier
check the real database contract while the JSON seal remains reviewable.

## 4. Artifact contracts

### 4.1 Portable SQLite snapshot

The snapshot contains exactly these application tables:

1. `trials`
2. `evaluations`
3. `search_attempt_events`
4. `search_candidates`
5. `search_evaluations`

`sqlite_sequence` is permitted as SQLite-owned metadata. The snapshot excludes
`alpha_state`, `bandit_state`, heartbeat/recovery state, promotion/audit
outboxes, dispatch state, broker data, credentials, and every other runtime
table.

The snapshot preserves the source rows and the UPDATE/DELETE denial triggers for
all five application tables. It sets `PRAGMA user_version=2`, passes
`PRAGMA integrity_check`, and contains the original immutable
`evaluation_json` required to recompute each evaluation content hash. It is
never opened by a runtime research service and is never accepted as a production
registry.

### 4.2 Seal manifest

`evidence/alpha_trial_ledger_snapshot_seal.json` has a strict schema with no
extra fields:

```json
{
  "schema_version": 1,
  "mode": "ALPHA_TRIAL_LEDGER_PORTABLE_SEAL",
  "ledger_path": "data/cache/experiment_registry.db",
  "history_path": "evidence/alpha_trial_ledger_history.json",
  "snapshot_path": "evidence/alpha_trial_ledger_snapshot.db",
  "history_sha256": "<64 lowercase hex>",
  "snapshot_sha256": "<64 lowercase hex>",
  "history_head_hash": "<64 lowercase hex>",
  "record_count": 2,
  "trial_count": 40,
  "evaluation_count": 10,
  "sealed_at_ms": 1784320000000
}
```

`sealed_at_ms` is a non-negative Unix epoch millisecond timestamp not earlier
than the final history record. Counts and the history head are recomputed from
the files; they are never trusted as declarations.

### 4.3 Git treatment

`.gitignore` remains unchanged so `data/cache/` stays excluded.
`.gitattributes` marks `evidence/alpha_trial_ledger_snapshot.db` as binary. The
snapshot is small, contains only the allowlisted research-ledger tables, and is
reviewed through its verifier and manifest rather than a textual binary diff.

## 5. Publication flow

The CLI gains two explicit modes:

- `--seal-snapshot`: publish a new portable snapshot and seal;
- `--check-snapshot`: verify the committed portable bundle without accessing
  `data/cache/experiment_registry.db`.

Publication proceeds as follows:

1. Acquire the existing history output lock.
2. Run the existing live `check_history`; refuse sealing unless the history
   exactly matches the current live authority.
3. Read the five allowlisted tables from the source in one stable read
   transaction.
4. Build a temporary SQLite snapshot with the fixed five-table schema, copy the
   rows, install the ten append-only triggers, set `user_version=2`, and run
   integrity verification.
5. Run `check_history` against the temporary snapshot while retaining
   `ledger_path="data/cache/experiment_registry.db"` as the authority label.
6. Hash the exact history bytes and snapshot bytes, then create a strict
   temporary seal manifest.
7. Recheck that the live authority and history did not change during export.
8. Atomically replace the snapshot, then atomically replace the seal manifest.

There is no cross-file atomic rename on Windows. Publication order is therefore
fail-closed: a crash can leave a hash mismatch, but can never leave a mismatched
bundle that passes verification. The next `--seal-snapshot` invocation repairs
the incomplete publication after revalidating the live source.

## 6. Verification flow

`--check-snapshot` and the top-tier checker perform the same semantic checks:

1. strict manifest fields, path labels, digest formats, and timestamp bounds;
2. SHA-256 of the exact history and snapshot files;
3. existing history schema, record chain, monotonic trial/evaluation sets, and
   second-session-after-first-snapshot clock rule;
4. snapshot integrity, `user_version`, exact table allowlist, and all ten
   append-only triggers;
5. complete search-event, candidate, and exact evaluation-link hash chains;
6. recomputed evaluation content hashes;
7. equality between the final history trial/evaluation snapshot and the SQLite
   snapshot;
8. equality between every retained session provenance record and the SQLite
   search chains;
9. manifest counts and head hash recomputed from verified contents.

The top-tier checker must not read the ignored live database. This makes
`scripts/assess_top_tier.py --check` deterministic in the originating worktree,
CI, and a fresh clone. Live divergence is instead detected by the unchanged
`tools.search_trial_ledger_history --check` operator command.

## 7. Failure semantics

- Missing history: `PARTIAL/M3`, as today.
- Valid history but missing portable snapshot or seal: `PARTIAL/M3` with an
  explicit portability gap.
- Present but malformed, tampered, over-broad, hash-mismatched, or semantically
  inconsistent portable evidence: `FAILED/M2`.
- Valid portable evidence: `PASS/M4`, regardless of whether the ignored live
  authority exists in the checkout.
- Live `--check` failure never gets masked by a valid historical seal; it remains
  a separate operational failure requiring a new capture or seal.

No failure path creates a registry, edits history records, deletes trials,
relaxes append-only triggers, or silently falls back to an empty ledger.

## 8. Code boundaries

- `tools/search_trial_ledger_history.py` owns export, seal publication, and both
  live and portable CLI checks. It may read the live registry but has no network,
  broker, credential, or order path.
- `services/governance/top_tier_assessment.py` verifies repository evidence only.
  It does not import `tools`, create SQLite files, or mutate evidence.
- `config/top_tier_requirements.json` declares `snapshot_path` and `seal_path` in
  addition to the existing runtime `ledger_path` label.
- Tests use temporary SQLite files except for one committed-evidence assertion,
  which must pass with the live `data/cache/experiment_registry.db` absent.

## 9. Testing strategy

Tool tests prove:

- a real two-session temporary live registry can be captured, sealed, and
  verified;
- the portable snapshot contains only the five allowlisted tables;
- the live registry can advance after sealing without changing the portable
  snapshot;
- corruption of history, snapshot bytes, manifest hashes, counts, table set,
  triggers, evaluation JSON, or session links fails closed;
- a crash-equivalent stale snapshot/new manifest or new snapshot/stale manifest
  cannot pass;
- `--check` still requires the live authority while `--check-snapshot` does not.

Governance tests prove:

- a valid portable bundle yields `M4/PASS` with no live registry;
- missing portable evidence yields `M3/PARTIAL`;
- invalid present evidence yields `M2/FAILED`;
- the committed root assessment does not depend on an ignored cache;
- all five official factory tools and persistent search identity wiring remain
  required.

Regression verification includes the focused trial-ledger suites, governance
scanner, `scripts/assess_top_tier.py --check`, and the canonical
`scripts/validate.py` gate. The known Docker/Trivy external red line remains
reported honestly and is not changed by this work.

## 10. Migration and evidence generation

1. Preserve the two existing version-2 history records unchanged.
2. Implement and test portable sealing.
3. Run live `--check` against the 40-trial/10-evaluation authority.
4. Run `--seal-snapshot` to generate the minimized snapshot and seal manifest.
5. Run `--check-snapshot` from a copy of the repository with the live database
   unavailable.
6. Regenerate `evidence/top_tier_assessment.json` and
   `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`.
7. Update the paper-gap report to describe a sealed historical authority
   snapshot, not an eternally current live database.

## 11. Acceptance criteria

The work is complete only when all of the following are true:

- fresh checkout/CI can verify the two-session ledger bundle and report
  `ALPHA.TRIAL.GLOBAL_LEDGER = M4/PASS` without `data/cache/`;
- the originating workstation's live `--check` still passes independently;
- the portable snapshot contains exactly the allowed evidence surface and no
  runtime-only tables;
- changing any history, snapshot, seal, trigger, chain, count, or evaluation
  payload causes a deterministic failure;
- the committed capability matrix reports 15/22 and three red lines without
  environment-dependent tests;
- all L2 safety flags remain false, no broker or credential path is added, and
  the governance scanner reports zero violations.
