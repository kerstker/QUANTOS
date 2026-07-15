# QUANTOS Experiment Registry + Persistence — Design

Date: 2026-06-24 · Status: approved · Scope: single implementation plan
Audit driver: `docs/QUANTOS_AUDIT_2026-06.md` §6 P0 — "No persistence, experiment
registry, artifact store, or reproducibility infrastructure" (catalog-wide DSR
trial count is process-local and lost on exit; no cross-session reproducibility).

## 1. Goal

Make durable, across process/session restarts:
1. **The catalog-wide multiple-testing state** — the distinct-config trial
   population (`global_trial_count`) and the per-alpha Sharpe population
   (`catalog_var_sr`) that Deflated Sharpe relies on. Today these live only in
   `AlphaCatalog`'s memory and reset on exit, so DSR's correction silently
   weakens every new session.
2. **Every `AlphaEvaluation`** as an immutable, queryable artifact (run id,
   config/dataset version, metrics, verdict, content hash) for reproducibility
   and audit.

Constraints: L2_DRY_RUN_ONLY, pure-Python, deterministic, fail-closed, stdlib
only (`sqlite3`), integrate with `services/alpha/catalog.py` and
`quantos/contracts` hashing without changing default behavior or weakening the
current tests/gate.

## 2. Architecture

New isolated package `services/research/experiment_registry/`:

- **`store.py` — `SqliteExperimentStore`**: a thin stdlib-`sqlite3` adapter that
  owns the schema and all SQL. One `.db` file (path supplied by the caller; an
  in-memory `":memory:"` path is used in tests). No domain logic, no imports
  from `services/alpha`.
- **`registry.py` — `ExperimentRegistry`**: the domain API over the store. Two
  responsibilities, deliberately small:
  1. **catalog snapshot**: `save(catalog)` / `load_into(catalog)`.
  2. **immutable evaluation artifacts**: `record_evaluation(...)`,
     `get_evaluation(run_id)`, `list_evaluations(alpha_id)`.
- **catalog port (in `services/alpha/catalog.py`)**: `AlphaCatalog` gains
  `snapshot() -> CatalogSnapshot` (a frozen dataclass of its state) and
  `restore(snapshot)` (rehydrate). The catalog stays pure in-memory and never
  imports `sqlite3`; the registry maps `CatalogSnapshot` ↔ rows. **When the
  registry is not used, AlphaCatalog behaves exactly as today** — so existing
  tests and the gate are untouched.

Dependency direction: `registry` → `store` and `registry` → `alpha` (reads a
snapshot); `alpha` does NOT depend on `registry`/`store`. The governance scanner
(post-M1) already scans all of `services/`, so the new package is covered and the
coverage meta-test enforces it.

## 3. Schema (sqlite — append-only / write-once)

```
trials(config_hash TEXT PRIMARY KEY, first_seen_ts INTEGER)
   -- global_trial_count == COUNT(*); monotonic & dedup'd ACROSS sessions by PK.

alpha_state(alpha_id TEXT PRIMARY KEY,
            definition_json TEXT, state TEXT,
            returns_json TEXT, sharpe REAL, pvalue REAL)
   -- rebuilds _defs/_state/_returns/_sharpes/_pvalues (var_sr, BH-FDR, approved_set).
   -- save() upserts current catalog state (state/returns evolve over a session).

evaluations(run_id TEXT PRIMARY KEY, alpha_id TEXT, dataset_version TEXT,
            recorded_ts INTEGER, verdict TEXT,
            content_hash TEXT, evaluation_json TEXT)
   -- immutable per-run artifacts. write-once: re-recording a run_id with a
   -- different content_hash raises; no UPDATE/DELETE is ever issued.
```

`trials` is the source of truth for the trial count (so re-registering an
identical `config_hash` across sessions cannot inflate it). `alpha_state` is a
materialized current-state snapshot (upserted), distinct from the immutable
`evaluations` log.

## 4. Data flow — the DSR cross-session fix

```
reg = ExperimentRegistry(SqliteExperimentStore(path))
reg.load_into(catalog)                 # rehydrate trial pop + sharpes + pvalues + defs/state
... factory/agent evaluates candidates ...
reg.record_evaluation(evaluation, dataset_version=..., recorded_ts=ts, audit_log=log)
reg.save(catalog)                      # persist evolved state + any new trials
```

After `load_into`, `catalog.global_trial_count()` and `catalog.catalog_var_sr()`
span all prior sessions, so the Deflated Sharpe deflation is no longer reset on
exit. Wiring the factory to do this is **opt-in and documented**, not forced —
the factory's default constructor is unchanged.

## 5. Error handling — fail-closed (key safety property)

- **`load_into` on a missing / corrupt / unreadable / schema-mismatched store
  RAISES** (`ExperimentStoreError`). It must NEVER silently yield an empty trial
  population: a silent reset would drop the multiple-testing count and
  **un-deflate DSR**, letting a noise alpha pass. Losing the trial population is
  loud, by design. (A genuinely fresh store is distinct from a corrupt one:
  `SqliteExperimentStore(path, create=True)` initializes an empty schema
  explicitly; `load_into` against an uninitialized/corrupt store raises, and
  loading an empty-but-initialized store yields an empty catalog legitimately —
  trial_count 0 is honest there, vs. a corrupt store which must never read as 0.)
- **Immutability**: `record_evaluation` is write-once. Re-recording the same
  `run_id` with a matching `content_hash` is idempotent (no-op); with a
  different `content_hash` it raises `ImmutableArtifactError`. No `update`/
  `delete` method exists on the registry or store public API.
- **Determinism**: timestamps are caller-supplied (`recorded_ts`, `first_seen_ts`)
  — no hidden wall-clock enters any hash. `content_hash` is `stable_hash` over the
  evaluation's canonical fields (reuses `quantos/contracts/hashing`). Same inputs
  + same ts → identical `content_hash` and identical DB rows.
- **Audit**: when an optional `services/monitoring` `GlobalAuditLog` is supplied,
  `record_evaluation` appends one `AuditEvent` (`action="EXPERIMENT_RECORDED"`,
  carrying `run_id`, `alpha_id`, `content_hash`) — reusing the existing
  tamper-evident chain rather than building a second ledger. Absent a log, the
  artifact is still recorded (audit is additive, not required).

## 6. Public API (minimal — YAGNI)

```python
# services/alpha/catalog.py
@dataclass(frozen=True)
class CatalogSnapshot:
    trials: tuple[tuple[str, int], ...]            # (config_hash, first_seen_ts)
    alphas: tuple[AlphaStateRow, ...]              # def/state/returns/sharpe/pvalue per alpha
class AlphaCatalog:
    def snapshot(self) -> CatalogSnapshot: ...
    def restore(self, snap: CatalogSnapshot) -> None: ...   # replaces in-memory state

# services/research/experiment_registry/registry.py
class ExperimentRegistry:
    def __init__(self, store: SqliteExperimentStore): ...
    def save(self, catalog) -> None: ...
    def load_into(self, catalog) -> None: ...      # raises ExperimentStoreError if not loadable
    def record_evaluation(self, evaluation, *, dataset_version: str,
                          recorded_ts: int, audit_log=None) -> str:  # returns run_id
    def get_evaluation(self, run_id: str) -> dict | None: ...
    def list_evaluations(self, alpha_id: str) -> list[dict]: ...
```

No migration framework (single `CREATE TABLE IF NOT EXISTS` init), no query DSL
beyond the two accessors, no concurrency model beyond sqlite defaults.

## 7. Testing (tests-first) — `tests/research/test_experiment_registry.py`

1. **Round-trip fidelity**: build a catalog (≥2 alphas w/ returns, pvalues,
   varied states, extra trials) → `save` → `load_into` a fresh catalog →
   identical `global_trial_count`, `catalog_var_sr`, `bh_fdr_survivors`,
   `approved_set`, and per-alpha `state`.
2. **Cross-session monotonic dedup**: save → load → register an already-seen
   `config_hash` + a new one → save → reload → count rose by exactly 1.
3. **Immutability**: `record_evaluation(run_id=X)` twice — same content is a
   no-op; different content raises `ImmutableArtifactError`; no update/delete API.
4. **Fail-closed**: `load_into` against a missing/corrupt/uninitialized store
   raises `ExperimentStoreError` (never an empty catalog).
5. **Audit emission**: `record_evaluation(audit_log=log)` appends one
   `EXPERIMENT_RECORDED` `AuditEvent`; `log.chain_valid()` stays True.
6. **Determinism**: identical evaluation + `recorded_ts` → identical
   `content_hash` across two registries.
7. **Wiring/gate**: add the new test file to `scripts/validate.py`; governance
   scanner clean (auto-covered).

## 8. Out of scope (roadmap, not this plan)

Real market-data lake / vendor ingestion; event-driven backtester; factor risk
model + constrained optimizer; agent research intelligence; replacing the
`walk_forward_passed` placeholder. This plan delivers only the registry +
persistence foundation and the cross-session DSR integrity fix.
