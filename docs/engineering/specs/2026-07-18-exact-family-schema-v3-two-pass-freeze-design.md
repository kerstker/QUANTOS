# QUANTOS Exact Family-Authority Schema v3 — Two-Pass Freeze Design

**Date:** 2026-07-18

**Status:** independently reviewed; user approved for implementation on 2026-07-18

**Readiness boundary:** `L2_DRY_RUN_ONLY`

**Decision:** Freeze all A4–A9 authority contracts and schema semantics before
exposing one exact v2-to-v3 activation.

## 1. Problem

The original Phase A sequence asked Task A3 to create the complete family
authority schema v3 before Tasks A4–A9 had frozen the contracts and state
machines that use it. That order is unsafe.

The table names were known, but several load-bearing properties were not yet
fixed:

- exact columns, affinities, nullability, checks, primary and foreign keys;
- the canonical payload covered by each `record_hash`;
- the partition and genesis of every ordered hash chain;
- terminal-state and late-insert triggers after freeze, closure, assessment,
  exposure consumption, and publication;
- the append-only model for an OOS access attempt that starts and later reaches
  a terminal result;
- the authority-instance lineage bound into publication receipts; and
- the precise cross-table verifier for non-empty authority data.

Adding these later while keeping `PRAGMA user_version=3` would create several
different schemas all claiming to be v3. A verifier could then accept one shape
while a writer assumes another. That is incompatible with a safety-critical,
tamper-evident authority.

## 2. Goals

1. Remediate and independently approve the current A3.1
   transaction/locked-write candidate before retaining it as the v2
   compatibility foundation.
2. Freeze A4–A9 domain contracts, row-hash definitions, SQL constraints, chain
   semantics, and terminal insert guards before any v3 activation exists.
3. Produce exactly one sealed family-schema v3 manifest and one explicit
   v2-to-v3 migration.
4. Keep portable-v2 read verification separate from search-writer and
   family-authority permissions.
5. Preserve every legacy evaluation/search/audit/outbox byte and hash.
6. Keep Phase A diagnostic-only: no alpha lifecycle state/version, approval,
   live trading, broker, order, credential, or network authority.
7. Run all migrations only against temporary v2 copies until the complete Phase
   A authority and validation gate are approved for deployment.

## 3. Non-goals

- No v4 lifecycle activation, `alpha_state.state_version`, evidence pin,
  promotion transition, or approval-consumption schema.
- No automatic migration during an ordinary research write.
- No inferred family data for existing v2 rows.
- No second canonical JSON/hash implementation.
- No broker integration, live order path, or real credential handling.
- No claim that a statistically assessed family can change an alpha state in
  Phase A.

## 4. Chosen architecture

The work is split into four passes.

### 4.1 Pass T — v2 transaction foundation

A3.1 remains independent of schema v3, but its current candidate is not yet
approved. After the transaction-ownership and compatibility findings below are
remediated, Pass T introduces:

- `EvaluationArtifactWrite`;
- one shared pure preparation/validation seam for the legacy evaluation
  artifact;
- store-owned `_immediate_transaction()`;
- an opaque store-owned write-lease token bound to the exact connection,
  context generation, owning thread, and active transaction;
- `_require_write_lock(conn, lease)` validation before any locked SQL;
- `_insert_evaluation_locked(...)`;
- `_append_event_outbox_locked(...)`; and
- one explicit `BEGIN IMMEDIATE` plus one terminal commit or rollback for every
  runtime public mutator.

Before schema checks or transaction control, a public mutator rejects any
caller-owned transaction and any active/reentrant store lease without issuing
`COMMIT` or `ROLLBACK`. `_ensure_search_schema()` may roll back only a migration
transaction that it started itself; it never unconditionally rolls back the
connection. `_immediate_transaction()` rolls back only when its own `BEGIN
IMMEDIATE` succeeded.

Read-like APIs never call a migrator or issue transaction control. In
particular, `pending_evaluation_audits()` fails closed on a legacy store that
lacks the exact search schema, while `evaluation_audit_chain_valid()` returns
`False`; neither may change `user_version`, `sqlite_schema`, `total_changes`, or
the caller's transaction. Search-schema migration remains an explicit write-path
operation only.

`EvaluationArtifactWrite` is not trusted merely because it has the expected
Python type. One shared pure preparation/validation module owns the exact legacy
JSON, `content_hash`, `run_id`, verdict, and audit-intent derivation. Both the
legacy registry path and future family batch path use it, and the store
revalidates every supplied write with that same implementation before locked
SQL. Caller-constructed inconsistent writes fail before mutation.

Pass T must preserve historical JSON serialization, `content_hash`, `run_id`,
audit event, link-chain, and outbox bytes. It does not change
`_SEARCH_SCHEMA_VERSION`, `user_version`, or the live database schema.

The committed top-tier alpha-trial checker must be updated in the same Pass T
change to understand the reviewed transaction context and locked-helper call
graph.  It may no longer require literal `BEGIN IMMEDIATE`, evaluation SQL, and
candidate-binding SQL to appear textually inside each public wrapper.  The
semantic checker must prove that the public method enters exactly one owned
transaction, the locked helper performs the evaluation/link/candidate checks on
that same connection and lease, and commit occurs only after every required
write.  Negative fixtures must still reject split connections, split commits,
an unregistered candidate, a helper callable without a valid lease, and a
public wrapper that bypasses the locked helper.  Refactoring source layout may
not silently turn the portable trial-ledger requirement red or make the checker
accept weaker persistence.

### 4.2 Pass C — contract and schema-semantic freeze

The contract portions of A4–A9 run before their persistence/service portions.
Each slice freezes:

- frozen Pydantic domain contracts;
- authority-issued identity rules;
- one canonical row payload per persisted record type;
- the SHA-256/hash-chain input and partition key;
- exact columns and SQL types;
- keys, unique constraints, checks, foreign keys, and indexes;
- no-update/no-delete triggers;
- terminal late-insert guards; and
- a non-empty-row verifier algorithm;
- a typed schema/constraint/trigger AST plus the canonical renderer grammar and
  version requirements needed to produce exact SQL in Pass M; and
- the genesis, activation-input, and version-permission contracts needed by the
  final migration.

Pass C may add structured schema specifications, but it must not expose an
activation or family-schema requirement function, render executable family
DDL, change `user_version`, or migrate a database. An incomplete specification
cannot become a writable authority.

### 4.3 Pass M — one exact v3 seal and migration

After all Pass C slices are complete, one seal task composes the specifications,
proves coverage, fixes the manifest hash, renders the DDL, and implements the
explicit v2-to-v3 migration and verifier.

Only this task may expose executable v3 DDL,
`activate_family_schema_v3(...)`, `verify_family_authority(...)`, and the
writer-side `_require_family_schema_v3()`. Activation is the only migration
entry point; the verifier and writer requirement never migrate.

### 4.4 Pass S — authority services and integration

The service portions of A4–A9 then write only through the sealed v3 store
interfaces. They may not add or change v3 columns, indexes, checks, foreign
keys, hash definitions, or triggers. If a service test exposes a missing schema
semantic before deployment, Pass C/M must be reopened and resealed. After a v3
authority is deployed, any structural change requires a new schema version; it
may not silently mutate v3.

A pre-deployment candidate seal that is reopened is invalidated, is never
published, and is used only in temporary stores. Only the finally accepted
manifest may claim canonical schema v3. Once that manifest has activated any
persistent authority, it is immutable.

## 5. Schema specification model

Private component specifications are typed schema AST values, not SQL strings.
Columns, expressions, partial-index predicates, foreign-key actions, and
trigger state machines use closed enums/nodes with no raw-SQL escape hatch.
Pass C freezes that AST model plus the renderer grammar/version contract, but
does not instantiate an executable renderer. In Pass M, public
`family_schema.py` becomes the only module allowed to compose the complete AST,
render it, and seal exact SQL.

Each `AuthorityTableSpec` fixes:

- table name and ordered `ColumnSpec` values;
- primary-key columns;
- exact foreign-key definitions and actions;
- exact unique, ordinary, and partial indexes;
- typed `CHECK` expression trees;
- typed no-update/no-delete trigger policies;
- typed terminal and cross-table insert-guard state machines;
- `RowHashSpec`, including canonical fields and version literal;
- optional `ChainSpec`, including partition key, ordinal, genesis rule,
  previous-hash field, record-hash field, and terminal-head rule; and
- the read verifier for rows and cross-references.

The sealed `FamilySchemaManifest` fixes:

- `schema_version=3`;
- the complete ordered catalog for all 32 business tables plus the genesis
  control table and every owned index/trigger;
- the exact rendered canonical `sqlite_schema.sql` text for every owned object;
- the canonical manifest bytes under `QUANTOS_CANONICAL_JSON_V1` and SHA-256;
- the exact renderer version;
- the contract-to-table traceability matrix; and
- a fixed `SEALED` state that is true only when all required components and
  cross-component constraints are present.

`SEALED` is derived by the manifest constructor from an exact required
component-ID set and coverage checks; it is not a caller-supplied boolean.
Missing, duplicate, unknown, or dependency-cyclic specifications fail before
DDL rendering. Pass M has an independent review gate over the renderer grammar,
rendered SQL catalog, and manifest hash before activation code is reachable.

Implementation-evidence module descriptors derive their dependency set from
normalized source AST imports and the globals actually bound by those imports;
they never enumerate all modules or builtins that happen to exist in a live
module namespace. Statically imported native backends remain artifact-bound,
while a lazy submodule import or an unexecuted conditional import cannot change
the seal. Source or bound native-artifact mutation remains material, and seal
construction uses only per-construction memoization rather than a process-wide
cache that could hide drift.

The verified 2026-07-19 Pass-M temporary-copy candidate seal is
`c1a61a41daa67f58e0d7ede45dff0fe127fc36dba3a8ab3bc4a22657bab98f07`.
Two independent fresh Python processes produced that exact SHA-256 and the same
401 rendered objects (33 tables, 153 indexes, 215 triggers). This records the
reviewed candidate; it is not operational evidence and does not authorize
persistent activation.

Ownership tests forbid executable family DDL, alternate renderers, or private
row-hash implementations outside the canonical schema module.

## 6. Contract-first slices

### 6.1 C4 — questions, lineage, scope, programs, decisions, exposures

This slice freezes the contracts and schema semantics for:

- `research_questions`;
- `research_question_aliases`;
- `strategy_lineage_roots`;
- `strategy_lineage_nodes`;
- `strategy_config_lineage_bindings`;
- `research_authority_events`;
- `selection_scopes`;
- `selection_scope_aliases`;
- `selection_scope_publishable_decisions`;
- `selection_scope_outcomes`;
- `research_programs`;
- `selection_decisions`;
- `global_oos_exposures`; and
- `global_oos_exposure_consumptions`.

One `research_programs` row binds one scope/question/root provenance tuple. A
family may link several programs, so no unfrozen many-to-many junction table is
required. The declaration later freezes the complete program/question/root
sets.

### 6.2 C5 — proposals, hypotheses, DLP dispositions, search seals

This slice freezes:

- `data_snooping_family_searches`;
- `data_snooping_proposal_registrations`;
- `data_snooping_proposals`;
- `data_snooping_hypotheses`;
- `data_snooping_search_emission_seals`; and
- proposal/disposition branch constraints needed by the later disposition
  table.

Proposal ingestion is deliberately split into two committed stages. The
registration row is the only pre-parse public authority row and owns the
ordered proposal chain plus the DLP branch. A clean registration may bind its
restricted clean-blob receipt; a quarantined registration exposes only the
coarse public metadata and opaque quarantine ID allowed by policy. It never
binds secret bytes, a secret-derived hash, exact secret length, scanner-result
commitment, or restricted storage address.

Exactly one `data_snooping_proposals` row then records the terminal parse
result for each registration. `ACCEPTED` binds the canonical proposal,
configuration, and alpha identities; `INVALID` and `SECRET_QUARANTINED` forbid
all parsed fields. The terminal row references the exact registration hash,
cannot precede registration time, and does not create a second proposal chain.
An emission seal proves registration count, terminal-result totality, and the
declared budget bound before a family can freeze. Restricted raw evidence
stays outside the public authority schema.

### 6.3 C6 — observed-PIT, code manifest, execution attestation

This slice freezes the immutable references and their embedded columns in
declarations, exposures, releases, closures, and publications. It adds no
business table: the observed-PIT, code-manifest, and execution-attestation
providers remain external authorities whose exact identity, content hash,
policy/version, and authority head are pinned and reverified. It does not add a
second data catalog or store secrets in the family authority.

The family row's `authority_instance_id` / canonical `record_hash` are not a
substitute for an external provider identity or head.  Every embedded external
reference therefore carries an unambiguous provider-qualified binding:

- observed-PIT references bind `observed_pit_authority_id`, immutable handle
  ID, content hash, applicable knowledge-time policy/version, and
  `observed_pit_authority_head`;
- code-manifest references bind manifest-policy ID/version/hash, evidence hash,
  `code_manifest_authority_id`, and `code_manifest_authority_head`;
- execution references bind the attestation hash,
  `execution_attestation_authority_id`, and its authority head; and
- immutable-artifact leases bind lease ID/hash,
  `immutable_artifact_lease_authority_id`, and its authority head.

`OosExposureRef` uses provider-qualified observed-PIT authority names; the
global exposure row's own authority wrapper remains the C4 row authority.
`OosExposureConsumption` separately names its consumption authority, canonical
row hash, and authority-event head.  A coverage mapping may not alias any of
those three meanings merely because their values are all SHA-256 shaped.
For release/closure/publication references, `exposure_authority_id` resolves to
the exact global-exposure row's `authority_instance_id` and
`exposure_authority_head` resolves to that row's canonical `record_hash`.
For a consumption reference, `consumption_record_hash` resolves to the
consumption row's canonical `record_hash`, while `consumption_authority_head`
resolves through `authority_event_id` to the matching
`research_authority_events.record_hash`; neither is a generic `authority_head`
alias.
Release, closure, and publication rows similarly pin the exposure, execution,
and lease authority identities as well as their hashes/heads.  Pass M must
reject a component whose embedded columns are present but whose typed external
provider verification rules are absent.

### 6.4 C7 — one-shot OOS release, attempts, batches, series, receipts

This slice freezes:

- `data_snooping_oos_releases`;
- `data_snooping_oos_access_attempts`;
- `data_snooping_candidate_dispositions`;
- `data_snooping_evaluation_batches`;
- `data_snooping_family_oos_evaluations`;
- `data_snooping_evaluation_receipts`; and
- `data_snooping_benchmark_returns`.

An OOS access attempt is an append-only event stream, not a mutable status row:

- `attempt_event_id` is the row primary key;
- `attempt_id` identifies the logical attempt;
- a unique family binding permits exactly one logical attempt for the family;
- `sequence_no=0` is the unique `STARTED` event for that attempt;
- `sequence_no=1`, when present, is exactly one of `COMPLETED` or `INCOMPLETE`;
- a partial unique index permits at most one terminal event per attempt;
- each event binds the previous event hash; and
- triggers reject sequence gaps, identity drift, a second start, and every
  event after a terminal event.

The row-level `OosAccessAttempt`, `OosReleaseReceipt`, and
`FamilyEvaluationBatchReceipt` hashes are the canonical authority-row hashes,
not hashes of a smaller similarly named business payload.  Their frozen
contracts therefore carry the record type, authority-instance ID, and genesis
record hash required to recompute the same `RowHashSpec`; field aliases such as
`previous_event_hash` must resolve to the exact persisted `prev_record_hash`.
Candidate and benchmark return-receipt hashes remain separate content hashes
stored explicitly inside their authority rows.

`data_snooping_oos_releases` owns the core `oos_exposure_id`; C6 extends that
row with the remaining provider-qualified exposure, execution-attestation, and
lease bindings.  The consumption-to-attempt relationship is not represented as
an invalid SQLite foreign key to logical `attempt_id`, because the event stream
contains both STARTED and terminal rows for that ID.  It is an exact typed
STARTED-row insert guard plus read-verifier obligation over the complete
authority/family/decision/exposure tuple.

A durable `STARTED` row consumes the one-shot family attempt and blocks closure
until deterministic recovery appends `INCOMPLETE`. Neither `INCOMPLETE` nor a
crash can authorize a replacement attempt or exposure.

`performance_access_attempt_count` is frozen as the count of logical starts,
`COUNT(DISTINCT attempt_id)` over `sequence_no=0`, not the event-row count. The
unique family binding therefore makes it exactly 0 or 1; the terminal row never
increments it. `ASSESSED` requires one logical attempt whose sequence is exactly
`STARTED(0), COMPLETED(1)`. A lone `STARTED` or an `INCOMPLETE(1)` forces
`NOT_ASSESSABLE` after closure/recovery.

### 6.5 C8 — declaration, freeze, closure, grid assessment, family conclusion

This slice freezes:

- `data_snooping_declarations`;
- `data_snooping_family_freezes`;
- `data_snooping_family_closures`;
- `data_snooping_grid_assessments`; and
- `data_snooping_assessments`.

The grid/conclusion split was introduced while Pass C was still private and
non-executable: no v3 manifest had been sealed, no DDL renderer or activation
entrypoint existed, and no operator database had been migrated. It therefore
reopens the unactivated Pass-C specification instead of mutating a deployed v3
authority.

It also freezes every late-insert guard that depends on a declaration, freeze,
closure, family assessment conclusion, or selection-scope outcome. These guards are
part of the initial v3 seal; A8 service code may not add them later.

`promotion_effect` is constrained to `NONE` in Phase A.

`closure_status=COMPLETE` means the one-shot OOS evidence bundle committed; it
does not guarantee that the statistical kernel is assessable. Fewer than two
candidates, fewer than 20 common periods, an invalid preregistered block grid,
degenerate variance, malformed exact series, or deterministic kernel failure
must instead produce the unique `NOT_ASSESSABLE` family conclusion while
retaining the complete closure evidence for independent replay. A closure that
is itself `NOT_ASSESSABLE` has no performance bundle and cannot manufacture
grid evidence.

### 6.6 C9 — artifact and publication receipt chain

This slice freezes `artifact_publication_receipts`, its authority-instance
lineage, previous-record hash, exact artifact/sidecar byte hashes, and unique
publication identity.

## 7. Authority genesis

The original 30 business tables are retained, C8 adds the separate
`data_snooping_grid_assessments` authority, and the C5 pre-parse/terminal split
adds `data_snooping_proposal_registrations`, for 32 business tables in total.
One additional control table,
`family_authority_genesis`, is required so every family and publication chain
has an explicit authority-instance origin instead of an implicit empty head.

The migration inserts exactly one genesis row with a fixed singleton key. This
is activation control evidence, not inferred research data. It binds:

- injected `authority_instance_id`;
- `v2_origin_fingerprint`, computed from a canonical ordered projection of the
  verified exact-v2 schema catalog plus every preserved v2 table's logical rows
  in primary-key order; SQLite file bytes, page layout, and checkout EOL bytes
  are never inputs;
- sealed v3 manifest hash;
- authority-issued activation `CodeManifestEvidence` hash;
- fixed migration ID and an injected activation epoch-microsecond timestamp;
- a fixed `record_type=FAMILY_AUTHORITY_GENESIS_V1` domain separator; and
- canonical genesis `record_hash`.

`v2_origin_fingerprint` uses a dedicated versioned projection specification in
the sealed manifest. It is streamed in table/row order, includes table names,
row counts, canonical logical values, and existing authority chain heads, and
is independently recomputed inside activation before commit and once on the
reopened temporary migration copy. It is a historical activation commitment:
later legitimate v3 search writes do not require current mutable v2 projections
to equal that origin digest. The projection specification type-tags SQLite
`NULL`, `INTEGER`, IEEE-754 `REAL`, `TEXT`, and `BLOB` values so it never
normalizes a legacy stored value or reserializes legacy JSON.

The genesis payload excludes its own hash field; its `record_hash` is SHA-256
over the exact canonical payload bytes. That completed hash becomes the empty
head for every v3 authority chain and the required `prev_record_hash` of each
partition's first row, including the first publication receipt. Table and
partition identities remain inside each first-row payload. Every v3 business
row, ordered or unordered, includes `record_type`, `authority_instance_id`, and
`genesis_record_hash` in its canonical row-hash payload and carries the exact
composite reference to the singleton genesis. This prevents transplanting a
row or chain between tables, partitions, or authority instances while retaining
the explicit physical-clone limitation below. The singleton check plus
no-update/no-delete triggers forbid a second or mutable genesis, without a
self-referential hash field.

Randomness and wall-clock time are supplied at the deployment boundary. Tests
inject fixed values. Ordinary callers cannot mint another genesis row, and
publication receipts must chain to this row.

Runtime verification also requires the deployment-configured expected
`authority_instance_id`; a missing or different expected identity fails closed.
This binds lineage but does not claim physical-clone uniqueness: a byte-for-byte
copy presented under the same trusted deployment identity is indistinguishable
inside the L2 single-node SQLite threat boundary. External sealing, strong
worker identity, and replicated/HA uniqueness remain post-L2 work.

## 8. Exact migration and version rules

### 8.1 Explicit activation only

`create=True` continues to create exact v2. Family activation is a separately
named operator/deployment action. No ordinary family writer implicitly migrates
the database.

`activate_family_schema_v3(...)` is absent from the research-runtime
`ExperimentStorePort`. Only the designated offline deployment/operations
entrypoint may call it with explicit genesis inputs. Governance tests forbid
calls from the console API, research runner, intelligence/agent paths, and
ordinary family services.

Versions 0/1 must first complete the existing search-schema migration to exact
v2. Family activation accepts only exact v2. It never chains two schema
migrations in one call.

### 8.2 Exact v2 activation-source verification

Activation does not rely on `verify_portable_v2_subset`. A dedicated
`verify_exact_v2_activation_source()` freezes and verifies the complete legacy
store before any DDL:

- every official v2 table, column/default/PK, index, trigger, foreign key, and
  canonical `sqlite_schema.sql` entry from `_SCHEMA` and `_SEARCH_SCHEMA`;
- the exact allowed object namespace, rejecting unknown non-SQLite tables,
  indexes, triggers, or views rather than sealing them accidentally;
- `PRAGMA integrity_check`, `foreign_key_check`, and the exact owned
  `sqlite_sequence` entry set without resetting sequence values;
- all evaluation/search link chains, search attempt/candidate/heartbeat/recovery
  chains, evaluation audit/outbox/dispatch chains, event outbox/dispatch chains,
  idempotency links, content hashes, and referential totality;
- legacy `alpha_state`, trial, and bandit rows as preserved logical state even
  where v2 has no row hash; and
- a deterministic row-order and type-tagged origin projection for every
  preserved table and owned `sqlite_sequence` row.

Any missing, extra, stale, malformed, hash-invalid, chain-invalid, or
referentially incomplete legacy object/row rejects activation. The verifier's
expected schema catalog and semantic checks are independently tested against
tampered full-v2 stores; the portable reader remains a deliberately weaker,
read-only subset verifier.

### 8.3 Transaction sequence

The v2-to-v3 activation:

1. enables and verifies `PRAGMA foreign_keys=ON` outside the transaction;
2. rejects an existing transaction or owner marker;
3. starts one `BEGIN IMMEDIATE`;
4. rereads `user_version` and runs the complete exact-v2 activation-source
   verifier above;
5. rejects every pre-existing v3 object-name collision;
6. executes each sealed DDL statement individually, without `executescript`,
   `IF NOT EXISTS`, or hidden transaction control;
7. inserts the single genesis control row;
8. sets `PRAGMA user_version=3`;
9. runs the exact in-transaction schema/genesis verifier;
10. reaches a final fault-injection checkpoint; and
11. commits once, otherwise rolls back once.

Every DDL statement, the genesis insert, the version change, verification, and
the pre-commit point has a named kill checkpoint. Reopening after any injected
failure must show exact v2, no v3 objects, unchanged v2 rows/chain heads, and an
unchanged `alpha_state` schema and ordered values.

### 8.4 Version permission matrix

- `verify_portable_v2_subset`: accepts every `user_version>=2`, verifies only
  the frozen portable-v2 subset, opens read-only/query-only, and authorizes no
  mutation.
- search writers: migrate below 2 using the existing path; in Phase A accept
  only the explicitly verified exact versions 2 and 3. On v2 they run the exact
  v2 search-writer verifier. On v3 they must, before `BEGIN`, run both that
  preserved search verifier and full `verify_family_authority()` with the sealed
  manifest/genesis and expected authority identity. Thus a database labeled v3
  but containing only v2 tables is read-only portable evidence and is rejected
  by the search writer. Phase A rejects 4+ before `BEGIN` or write. A later
  lifecycle release may add exact v4 only through a reviewed compatibility
  contract and verifier change.
- family verifier/writers: reject below 2; activate exact 2 only through the
  explicit deployment action; verify and write the family subset only on exact
  3 in Phase A; reject 4+ before mutation. A later lifecycle release may permit
  exact v4 only after independently proving that the sealed v3 family subset is
  unchanged.
- Phase A exposes no lifecycle-schema function and cannot verify or write v4
  lifecycle semantics.

A database with `user_version=3` but only v2 tables is deliberately valid for
portable-v2 inspection and invalid for every family-authority operation.

## 9. Verification semantics

`verify_family_authority()` never equates a version number or table existence
with validity. It checks:

- exact ordered column metadata, affinity, PK, nullability, and defaults;
- exact canonical `sqlite_schema.sql` text, indexes, partial predicates,
  foreign keys, checks, and trigger bodies;
- `foreign_key_check`;
- the genesis payload and manifest binding;
- every canonical row hash;
- every ordered chain, partition, ordinal, genesis, and terminal head;
- cross-table identity and content-hash references;
- state-specific nullability and branch constraints;
- selection-scope, exposure-consumption, freeze, closure, assessment, and
  publication totality; and
- absence of unknown family-authority objects.

Non-empty authority data is supported from the first v3 seal. An
empty-table-only verifier is not acceptable.

### 9.1 Transaction-scoped SQLite function runtime

The nine Python SQLite functions used by persistent checks and triggers cannot
remain installed on a caller-owned or pooled connection. The standalone
`verify_family_authority()` therefore verifies the caller's exact pre-trust
catalog, clones the caller-visible committed or uncommitted view into a fresh
private connection, performs semantic verification there, and closes the clone.
It rejects a caller connection that already contains any `quantos_*` function
and returns with the caller transaction, `total_changes`, and function registry
unchanged.

All Pass-S family-table writes must use
`run_family_authority_transaction(...)`. That runtime accepts an absolute
filesystem path rather than a connection and implements the exact sealed
`QUANTOS_SQLITE_RUNTIME_PROFILE_V1` transition:

1. open one fresh exact `sqlite3.Connection` with `cached_statements=0`, disable
   extension loading, set `trusted_schema=OFF`, and require `main` plus an empty
   `temp` namespace;
2. take `BEGIN IMMEDIATE`, acquire the process owner token by CAS, and verify the
   exact sealed catalog/genesis/version/identity without application functions;
3. install a deny-by-default authorizer, enable trust once inside that owned
   transaction, reread the version, then register and inspect exactly the nine
   manifest-bound functions;
4. run full semantic verification before and after the callback, while exposing
   only a revocable session that can append exact ordered columns to the 32
   business tables; and
5. permit one store-owned terminal commit or rollback, unregister the functions,
   return trust to OFF, remove the authorizer, release the owner token, and close
   the connection on every exit.

The authorizer rejects updates, deletes, schema changes, attached databases,
extension loading, dangerous pragmas, unknown functions, legacy/search/genesis
writes, nested transaction control, and savepoints. Activation remains a
separate offline DDL privilege path and does not run under this DML authorizer.
The ordinary ExperimentStore may continue to write its inherited v2/search
tables on exact v3 only after both the preserved search gate and full family
verification; it is not a family-table writer.

## 10. Legacy compatibility

The v3 authority uses `QUANTOS_CANONICAL_JSON_V1`. Existing evaluation/search,
audit, and outbox rows retain their historical serializers and hashes.

Golden compatibility tests hard-code expected values for non-ASCII strings,
enums, floats, negative zero, and nested structures. The expected values may
not be generated by `prepare_evaluation_artifact()` or another new helper.

For the same logical evaluation, exact v2 and migrated v3 stores must produce
identical:

- `evaluation_json`;
- `content_hash`;
- `run_id`;
- search link row/hash;
- audit event row/hash and first-write timestamp; and
- event outbox payload/hash/order.

## 11. Deployment boundary

During development, every migration test operates on a temporary copy or a
fresh temporary v2 store. The real `data/cache/experiment_registry.db` and the
tracked portable v2 snapshot are never migrated by unit or integration tests.
No candidate manifest, genesis, or v3 store is published as operational
evidence before the persistent-deployment decision below.

`FamilySchemaActivationInputs.activation_code_manifest_hash` is only a
content binding to an **already verified and approved** code-manifest evidence
record. A syntactically valid 64-hex digest is not proof of provenance,
freshness, approval, or deployment authority, and the core activation primitive
does not pretend to perform those external checks. The future designated
offline deployment tool must verify the complete `CodeManifestEvidence` through
an independently configured governance provider before it may construct these
inputs. Pass M provides neither that tool nor that provider; consequently the
current activation API is limited to temporary-copy verification and persistent
activation remains forbidden/unimplemented.

Applying v3 to a persistent operator database requires a separate deployment
decision after:

- all Pass C/M/S tests pass;
- the semantic verifier passes on a temporary migration copy;
- the canonical full gate is rerun;
- a backup and restore drill is fresh; and
- the authority-instance/genesis inputs are explicitly approved.

## 12. Failure handling

- Unknown schema versions fail before transaction control or writes.
- A nested/manual transaction is never committed or rolled back by the store.
- Locked primitives reject foreign connections, inactive/foreign lease tokens,
  context-generation mismatch, reentrancy, and wrong-thread use before SQL.
- After its own successful `BEGIN IMMEDIATE`, the store attempts rollback for
  every escaping `BaseException`; a commit/rollback failure invalidates and
  closes that store connection before propagating a fail-closed error.
- Migration or family writes never catch an unknown exception and reinterpret
  it as success, an empty family, or a normal `NOT_ASSESSABLE` result.
- Constraint, hash, chain, or verifier failures roll back the entire store-owned
  transaction.
- SQLite file-byte equality is not used as rollback proof; reopened logical
  schema, rows, heads, and version are the authority evidence.

## 13. Test and review gates

### 13.1 Pass T

- owner-token matrix: outside, manual transaction, foreign connection, stale or
  forged token, nested/reentrant context, wrong thread, and cleanup after
  exception;
- exactly one `BEGIN IMMEDIATE` and terminal commit/rollback per runtime public
  mutator;
- `pending_evaluation_audits()` and `evaluation_audit_chain_valid()` prove zero
  DDL, migration, transaction control, `user_version`, or `total_changes`
  mutation on a legacy store;
- failure after evaluation, link, audit, and outbox writes;
- close-and-reopen all-or-none proof on a file-backed temporary store;
- direct proof that both locked primitives cannot open connections, migrate, or
  issue transaction control;
- inconsistent caller-built `EvaluationArtifactWrite` rejection before SQL;
  and
- independent hard-coded legacy golden bytes/hashes covering non-ASCII, enums,
  precise floats, negative zero, nested values, search links, audit rows, and
  outbox rows without using production JSON/hash helpers to form expectations.
- semantic top-tier wiring fixtures that follow the public-wrapper -> owned
  transaction -> locked-helper call graph, pass the reviewed A3.1 layout, and
  fail for split transactions/connections, missing candidate binding, invalid
  lease, or a bypassed helper.

### 13.2 Pass C

- every domain-contract field maps to a column, canonical payload field, or an
  explicitly non-persisted derivation;
- every table has exact append-only and terminal guards;
- every ordered authority has one unambiguous chain partition/genesis/head;
- every foreign reference has a verifier and SQL constraint when it is in the
  same database; and
- the closed schema AST and renderer grammar/version contract contain no raw-SQL
  escape hatch; and
- no renderer, executable v3 DDL, or migration is reachable before all
  component specifications are complete.

### 13.3 Pass M

- fault injection after every DDL and activation checkpoint;
- version matrix 0/1/2/3/4/5+, including the deliberate difference between the
  read-only portable verifier and Phase-A writers;
- independent review and golden snapshots of the canonical renderer output,
  complete `sqlite_schema.sql` catalog, and manifest hash;
- exact schema-catalog tamper matrix;
- 33-table UPDATE/DELETE rejection;
- exact 17-step fresh-connection runtime transition plus authorizer escape,
  owner-CAS, SQLite writer-lock, rollback, function-cleanup, and source-race
  tests;
- FK, check, unique, partial-index, hash-chain, and late-insert tests;
- two-connection migration race; and
- v2/v3 legacy byte compatibility; and
- governance ownership proving activation is unreachable from runtime research,
  console, and agent entrypoints.

### 13.4 Pass S

- authority-issued IDs only;
- one-shot exposure/OOS consumption under concurrent writers;
- freeze/closure/assessment/publication totality;
- non-empty live-authority verification;
- no schema mutation by services; and
- Phase A proves zero alpha-state or promotion effect.

### 13.5 Final gate

- task-specific focused tests;
- complete contracts/research/governance/tool regressions;
- canonical `rtk python scripts/validate.py`;
- exact top-tier assessment without score inflation; and
- review of all remaining known 19-gate failures as independent evidence or
  environment/deployment blockers.

## 14. Process hygiene

Tests and review commands must not leave background Python, Node, browser,
database, or helper processes running. Any service started for integration
verification must record its process ID, be stopped in a `finally` path, and be
confirmed absent at the task boundary. Review agents are released after their
final report. Cleanup must target only processes created by the current task.

## 15. Acceptance criteria

This design is satisfied only when:

1. A3.1 is independently approved and remains schema-v2 compatible.
2. All C4–C9 contracts and schema semantics are frozen and traceable.
3. One sealed manifest covers the 32 business tables plus
   `family_authority_genesis`.
4. No incomplete manifest can render DDL or migrate a store.
5. Exact v2-to-v3 activation is atomic under every injected failure.
6. Portable reads, search writes, and family writes enforce different version
   permissions exactly as specified.
7. Standalone verification leaves no SQLite functions on its caller, and every
   family write uses the sealed fresh-connection runtime transition.
8. Non-empty v3 data is semantically verifiable from the initial seal.
9. A4–A9 services add no schema drift.
10. Persistent deployment remains a separately approved action.
11. No background process from the work remains after verification.
