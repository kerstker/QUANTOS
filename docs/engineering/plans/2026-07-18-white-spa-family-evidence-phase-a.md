# White RC / Hansen SPA Family Evidence — Phase A Implementation Plan

> **For agentic workers:** Use the repository's subagent-driven implementation workflow (or its plan-execution alternative) task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic White Reality Check / Hansen SPA kernel and a complete, blind, tamper-evident family-evidence authority without changing any alpha lifecycle state or claiming top-tier promotion capability.

**Architecture:** Phase A is diagnostic-only. It introduces one canonical authority codec, additive ExperimentStore schema v3, authority-issued research and deployment identities, pre-parse proposal registration with fail-closed DLP quarantine, governed observed-PIT handles, immutable execution attestation, a one-shot whole-family OOS batch, closure and staged assessment, and an acyclic published artifact plus publication-chain projection. Historical 39/8 material is audited honestly as `NOT_ASSESSABLE`; it is never repaired or promoted.

**Tech stack:** Python 3.11+, Pydantic v2, NumPy float64, stdlib `sqlite3`/`hashlib`/`json`/`unicodedata`, pytest, existing QUANTOS event/outbox and top-tier assessment frameworks.

**Authoritative design:** `docs/engineering/specs/2026-07-18-white-spa-family-evidence-design.md`.

## Global constraints

- Keep `LIVE_TRADING_ENABLED=false`, `REAL_BROKER_ENABLED=false`, `ALLOW_ORDER_SUBMISSION=false`, `ALLOW_AI_DIRECT_TRADING=false`, and `REQUIRE_HUMAN_APPROVAL=true` unchanged.
- Add no broker SDK, network client, credential reader, live order path, or AI-to-order path.
- Phase A may not add schema v4 activation, `alpha_state.state_version`, an evidence-pin transition, a lifecycle verifier, or any positive alpha-state write.
- Phase A assessment rows always store `promotion_effect="NONE"`. The semantic top-tier checker must remain `PARTIAL` for this capability until Phase B is active and a real governed family verifies.
- Do not modify historical `AlphaEvaluation` fields or its hash algorithm. New family contracts and hashes are separate.
- Do not replace `quantos/contracts/hashing.py::stable_hash`; existing v2 chains retain their historical algorithm. Every new v3 family authority uses `QUANTOS_CANONICAL_JSON_V1` only.
- The portable-v2 verifier accepts schema version 2 or later while checking only its five-table subset. A portable snapshot remains exact v2. Mutating writers accept only known versions 2, 3, and 4.
- No raw proposal containing a secret may reach the parser, ExperimentStore, logs, evidence tree, ordinary archive, or public hash chain. Public quarantine metadata cannot expose a raw hash, exact length, result-derived commitment, restricted blob reference, or restricted chain head.
- A fixture may test control logic but can never satisfy `REAL_GOVERNED_FAMILY_V1`. `services/data/catalog.py` is synthetic and permanently ineligible.
- Preserve all unrelated user changes. Do not reset, overwrite, stage, commit, push, refresh external backup evidence, or regenerate supply-chain evidence without separate authorization.
- Every repository command starts with `rtk`. Focused pytest commands use `-p no:cacheprovider`; the canonical gate is `rtk python scripts/validate.py`.

## Phase boundary and completion definition

Phase A completes only when:

1. the canonical codec and statistical kernel pass independent golden, property, permutation, degeneracy, and prefix-stream tests;
2. exact v2-to-v3 migration is additive and atomic while old lifecycle behavior remains unchanged;
3. a declared family can be authority-issued, pre-parse registered, frozen, blindly evaluated once, closed, assessed, exported, and independently verified;
4. all accepted candidates and the benchmark use one common ordered period calendar and exact gross-cost-net reconciliation;
5. the publication envelope and sidecar recompute from exact canonical bytes and complete authority-chain prefixes;
6. the 39/8 historical scope produces only `HISTORICAL_AUDIT_ONLY / NOT_ASSESSABLE` evidence; and
7. no alpha state, evaluator verdict, or top-tier score changes.

## File responsibility map

- `quantos/contracts/canonical.py`: sole canonical float/JSON/hash implementation for v3 and later authorities.
- `quantos/contracts/research.py`: frozen family, statistical, authority, attestation, artifact, and evidence-reference contracts.
- `services/validation/reality_check.py`: canonical SplitMix64, stationary bootstrap, White RC, Hansen SPA, block-grid, and Monte Carlo schedule kernel.
- `services/research/validation/reality_check.py`: compatibility-only re-export.
- `services/research/experiment_registry/family_schema.py`: v3 DDL, trigger catalog, schema verifier, and portable-v2 subset verifier.
- `services/research/experiment_registry/store.py`: migration owner, transaction primitives, append-only family writes, and whole-batch composition.
- `services/research/experiment_registry/registry.py`: prepares immutable evaluation writes once for both legacy and family paths.
- `services/research/data_snooping/`: research authorities, proposal ingress, family orchestration, OOS service, assessment, evidence export, and verification.
- `services/governance/dlp.py`: fail-closed scanner contract and fixed policy implementation.
- `services/governance/code_manifest.py`: authority-derived committed Git-blob manifest verification.
- `services/deployment/code_attestation.py`: immutable-artifact execution attestation and read-lease contract.
- `services/data/research_dataset_authority.py`: observed PIT handle, OOS exposure, consumption CAS, and byte-release boundary.
- `services/alpha/detailed_evaluation.py`: pure detailed evaluator with no catalog/store/lifecycle mutation.
- `tools/data_snooping_evidence.py`: historical audit, close, assess, export, and verify CLI.
- `services/governance/top_tier_assessment.py`: shared semantic checker; never a path-existence shortcut.

---

### Task 1 (A1): Canonical authority codec and frozen domain contracts

**Status (2026-07-18):** implementation, independent review, and fresh focused
verification are complete. The authorization-gated commit checkpoint remains
intentionally deferred; nothing was staged, committed, or pushed.

**Files:**

- Create: `quantos/contracts/canonical.py`
- Modify: `quantos/contracts/research.py`
- Modify: `quantos/contracts/__init__.py`
- Create: `tests/contracts/test_canonical.py`
- Create: `tests/contracts/test_data_snooping_contracts.py`

**Produces:**

- `CanonicalJsonError`
- `canonicalize_float64(value: float) -> float`
- `canonicalize_authority_value(value: object) -> object`
- `float64_hex(value: float) -> str`
- `parse_float64_hex(value: str) -> float`
- `canonical_json_bytes(value: object) -> bytes`
- `load_canonical_json_bytes(payload: bytes) -> object`
- `canonical_sha256(value: object) -> str`
- `sha256_bytes(payload: bytes) -> str`
- `PeriodKey`, `MonteCarloTailResult`, `BootstrapPrefixHash`, `RealityCheckResult`, `SpaVariantResult`, and `SpaResult`, each with `ConfigDict(frozen=True, extra="forbid")`

- [x] **A1.1 Write RED canonical byte tests.**

Add exact-byte assertions for sorted compact UTF-8 JSON, NFC strings, float-hex strings, positive-zero normalization, and one final LF. Add fail-closed cases for duplicate keys, raw JSON floats, non-NFC strings, BOM, CRLF, missing LF, two final LFs, NaN, infinity, and non-canonical float spellings.

The central identity test must assert:

```python
assert float64_hex(-0.0) == "0x0.0p+0"
assert canonical_json_bytes({"z": "é", "x": "0x1.0000000000000p+0"}) == (
    '{"x":"0x1.0000000000000p+0","z":"é"}\n'.encode("utf-8")
)
assert canonical_sha256({"x": "0x1.0000000000000p+0"}) == sha256_bytes(
    b'{"x":"0x1.0000000000000p+0"}\n'
)
```

Run RED:

```powershell
rtk python -m pytest tests/contracts/test_canonical.py -q -p no:cacheprovider
```

Expected RED: import failure for `quantos.contracts.canonical`.

- [x] **A1.2 Implement `QUANTOS_CANONICAL_JSON_V1`.**

Implementation rules are exact:

- normalize typed negative zero before serialization;
- reject non-finite floats and recursively encode typed Python float values through `float64_hex` before serialization;
- reject JSON numeric floats on load and refuse pre-serialized JSON text as an authority object;
- recursively accept only `None`, booleans, integers, NFC strings, lists/tuples, dictionaries with NFC string keys, and Pydantic `model_dump(mode="python")` results after explicit float conversion;
- load JSON with an `object_pairs_hook` that rejects duplicate keys and `parse_float` that raises;
- re-encode a loaded value and require byte equality with the input;
- use `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`, UTF-8 without BOM, and exactly one LF.

Run GREEN and focused regression:

```powershell
rtk python -m pytest tests/contracts/test_canonical.py -q -p no:cacheprovider
rtk python -m pytest tests/contracts -q -p no:cacheprovider
```

- [x] **A1.3 Add only the frozen statistical foundation required by Task A2.**

Do not front-load later authority/artifact models into this task. Each later task adds its own strict contracts and RED tests before its production code. Task A1 adds these exact field groups without touching historical `AlphaEvaluation`:

- `PeriodKey`: `event_time` as positive epoch microseconds, non-empty `frequency_id`, and non-empty `label_horizon`;
- `MonteCarloTailResult`: non-negative `exceedance_count`, positive `replication_count`, `p_numerator=1+C`, `p_denominator=1+B`, canonical-hex `p_value`, fixed conditional-variance formula ID, canonical-hex conservative radius, and `ambiguous`;
- `BootstrapPrefixHash`: fixed algorithm version, positive block length and replication count, and lowercase 64-hex index-stream SHA-256;
- `RealityCheckResult`: algorithm version, objective fixed to `MEAN_NET_RETURN_DIFFERENTIAL`, period and candidate counts, block length, sorted candidate order, canonical-hex column means and observed statistic, tail result, ordered prefix hashes, decision in `AMBIGUOUS|REJECT|NON_REJECT|INVALID`, run status in `PUBLISHABLE|NON_PUBLISHABLE_TEST_RUN|INVALID`, and reasons;
- `SpaVariantResult`: variant in `LOWER|CONSISTENT|UPPER`, canonical-hex centers, tail result, and decision;
- `SpaResult`: algorithm version, objective fixed to `MEAN_NET_RETURN_DIFFERENTIAL`, period and candidate counts, block length, sorted candidate order, canonical-hex observed statistic, gamma-zero, degeneracy floors, long-run variances, thresholds, all three variant results, primary variant fixed to `CONSISTENT`, ordered prefix hashes, run status, and reasons.

Cross-field validators prove plus-one arithmetic, count/order lengths, complete three-variant SPA coverage, consistent primary selection, lower/consistent/upper alignment, and prefix block/count identity. Authority floats are represented as canonical hex strings; contract construction normalizes typed negative zero before encoding.

Run RED then GREEN:

```powershell
rtk python -m pytest tests/contracts/test_data_snooping_contracts.py -q -p no:cacheprovider
rtk python -m pytest tests/contracts/test_canonical.py tests/contracts/test_data_snooping_contracts.py tests/contracts -q -p no:cacheprovider
```

- [ ] **A1.4 Record an implementation checkpoint.**

Suggested commit only after separate authorization:

```powershell
rtk git add quantos/contracts/canonical.py quantos/contracts/research.py quantos/contracts/__init__.py tests/contracts/test_canonical.py tests/contracts/test_data_snooping_contracts.py
rtk git commit -m "feat(evidence): add canonical family contracts"
```

---

### Task 2 (A2): Deterministic stationary bootstrap and per-block White RC/Hansen SPA

**Status (2026-07-18):** implementation, remediation, two independent final
reviews, and fresh root verification are complete. Nothing was staged,
committed, or pushed.

**Files:**

- Create: `services/validation/reality_check.py`
- Create: `services/research/validation/reality_check.py`
- Create: `tests/research/test_reality_check_bootstrap.py`
- Create: `tests/research/test_reality_check.py`
- Create: `tests/research/fixtures/reality_check_v1.json`
- Modify: `tests/governance/test_validation_ownership.py`

**Produces:**

- `RealityCheckInputError`, the fail-closed boundary error that Task A8 maps to
  a `NOT_ASSESSABLE` authority assessment
- `SplitMix64(seed: int)` with `next_u64`, `uniform_below`, and `restart_uniform`
- `block_length_grid_v1(period_count: int) -> tuple of integers`
- `iter_stationary_bootstrap_indices(period_count, block_length, seed, replication_count)`
- `compute_reality_check_and_spa(candidate_net_returns: Sequence[tuple[str, Sequence[float]]], benchmark_net_returns, period_keys, block_length, alpha, seed, replication_count, prefix_replication_counts) -> tuple[RealityCheckResult, SpaResult]`; each pair is `(config_hash, returns)`, duplicates remain observable and are rejected, and the kernel sorts accepted columns by config hash

Task A2 deliberately stops at deterministic per-block-length RC/SPA results.
Task A8 owns the declaration-aware replication schedule, the all-grid effective
verdict, and construction of `DataSnoopingAssessment`; those authority contracts
do not exist before A8 and must not be duplicated here.

- [x] **A2.1 Write RED PRNG and bootstrap golden tests.**

Tests must independently implement the first ten SplitMix64 outputs from the published constants, exercise `uniform_below` rejection rather than modulo bias, lock the inclusive `n=2**64` upper bound, prove restart uses strict `<`, assert little-endian uint64 index bytes, assert all candidates share one index vector, and assert each block length starts from the same declared seed. Prefix hashes for 10, 100, and 1,000 replications must equal the corresponding prefix of a single 1,000-replication run. Replication checkpoints are positive, strictly increasing, unique, never exceed the declared replication count, and must terminate exactly at that count. An instrumented hash/iterator test must prove the production consumer updates one replication before requesting the next, so materializing all `B*T` indices is a test-detectable regression.

Run RED:

```powershell
rtk python -m pytest tests/research/test_reality_check_bootstrap.py -q -p no:cacheprovider
```

- [x] **A2.2 Implement the streaming deterministic bootstrap.**

Use the exact SplitMix64 transitions and uint64 masking from design §3.5. Stream emitted indices directly into SHA-256; never retain the 200,000-replication byte stream. Validate `0 <= seed < 2**64`, `1 <= block_length <= T // 2`, and `replication_count > 0`. Yield each index tuple for calculation while updating deterministic prefix checkpoints.

Run GREEN:

```powershell
rtk python -m pytest tests/research/test_reality_check_bootstrap.py -q -p no:cacheprovider
```

- [x] **A2.3 Write independent RED statistical fixtures.**

The fixture must include:

- one clearly superior two-candidate family;
- an all-negative family;
- a family with many poor alternatives;
- a column permutation of the first case;
- a constant/zero-variance candidate;
- an omega-squared degeneracy case;
- a case on the inclusive-tail equality boundary;
- an ambiguity case requiring the next replication count;
- lower, consistent, and upper SPA recentering values.

Add a parameterized fail-closed matrix for `K < 2`, duplicate/empty/non-NFC config hashes, ragged candidate columns, NaN, positive/negative infinity, empty keys, duplicate/non-monotone/misaligned period keys, non-`PeriodKey` duck types, zero event time, mixed frequency or label horizon, `T < max(20, 2 * block_length)`, block length below 1 or above `T // 2`, alpha outside `(0, 1)`, seed outside unsigned 64-bit range, invalid replication checkpoints, iterators or custom objects that implement both `Mapping` and `Sequence`, booleans passed for integer/return fields, conversion overflow, and finite inputs that overflow to a non-finite intermediate. Separately test the integer-only block grid at `T=20,27,64,125`. Every negative test must assert the exact `RealityCheckInputError`, not its `ValueError` base. Task A2 must raise `RealityCheckInputError` for every invalid kernel input; Task A8 catches only that typed boundary error and records the family as `NOT_ASSESSABLE`.

Negative zero is valid and receives a separate equivalence test: replacing any `-0.0` input with `+0.0` must produce byte-identical normalized inputs, statistics, decisions, bootstrap prefix hashes, stored contracts, and published artifact bytes. The kernel normalizes every negative zero to positive zero before means, variance, resampling, hashing, or storage.

The golden generator must be a test-only independent scalar reference and may not import the production kernel.

Run RED:

```powershell
rtk python -m pytest tests/research/test_reality_check.py -q -p no:cacheprovider
```

- [x] **A2.4 Implement per-block RC/SPA and the deterministic block grid.**

The production kernel must:

1. sort columns lexicographically by config hash;
2. compute `d[t,k] = candidate_net[t,k] - benchmark_net[t]` as float64;
3. compute White's unstudentized observed maximum and mean-centered bootstrap maximum;
4. compute stationary-bootstrap long-run variance with the exact `kappa` expression;
5. reject the kernel call for any non-finite, non-positive, or degenerate eligible variance, with degeneracy defined as `omega2 <= 64 * float64_epsilon * gamma_zero`;
6. compute all three Hansen variants and designate `consistent` as primary;
7. use inclusive exceedance and `(1+C)/(1+B)`, with a direct mutation-killing test for `abs(p-alpha) == sqrt(B)/(B+1)` so the ambiguity comparison is provably inclusive;
8. store `sqrt(B)/(B+1)` as the load-bearing two-standard-error radius;
9. use integer cube-root construction for `QUANTOS_BLOCK_LENGTH_GRID_V1`;
10. independently validate `K`, `T`, finite float64 values, common period keys, alpha, unsigned seed, replication schedule, and every block-grid point even when callers bypass Pydantic contracts;
11. normalize negative zero before every calculation and emitted result; and
12. raise `RealityCheckInputError` on any invalid input rather than fabricating a result, dropping a column, or intersecting calendars; and
13. expose no declaration-aware all-grid verdict in Task A2. Task A8 must require every declared grid point and both primary methods to be resolved rejects before it can construct `SUPPORTS_SUPERIORITY`.

Run GREEN and ownership regression:

```powershell
rtk python -m pytest tests/research/test_reality_check.py tests/research/test_reality_check_bootstrap.py tests/governance/test_validation_ownership.py -q -p no:cacheprovider
```

- [x] **A2.5 Verify compatibility export identity.**

`services/research/validation/reality_check.py` must only import and re-export canonical production objects. Extend ownership tests to assert object identity and forbid a second implementation.

Suggested commit after authorization:

```powershell
rtk git add services/validation/reality_check.py services/research/validation/reality_check.py tests/research/test_reality_check_bootstrap.py tests/research/test_reality_check.py tests/research/fixtures/reality_check_v1.json tests/governance/test_validation_ownership.py
rtk git commit -m "feat(validation): add deterministic White RC and SPA"
```

---

### Task 3 (A3): Composable ExperimentStore writes and deferred exact schema v3

**Status (2026-07-19):** A3.1 / Pass T and the superseding A3.2/A3.3 Pass-M
scope are implemented, independently reviewed, and verified. Pass M now has the
sealed 33-table/401-object exact-v3 schema, exact v2/v3 read/write permission
matrix, atomic activation with all 405 fault points, non-empty 13-table legacy
compatibility, the sealed fresh-connection family-write runtime, and governance
ownership tests. The focused C4-C9/family/store closure is 1277 tests green;
persistent activation remains forbidden pending Pass S and the separate
deployment decision. The original A3.2/A3.3 ordering below is superseded by
[`2026-07-18-exact-family-schema-v3-two-pass-freeze-design.md`](../specs/2026-07-18-exact-family-schema-v3-two-pass-freeze-design.md):
A4-A9 contract/schema semantics must complete Pass C before one Pass M may
render DDL, seal the manifest, or expose the exact v2-to-v3 activation. The
superseded text is retained as historical requirements input, not an executable
order of work.

**Files:**

- Create: `services/research/experiment_registry/family_schema.py`
- Modify: `services/research/experiment_registry/store.py`
- Modify: `services/research/experiment_registry/registry.py`
- Modify: `services/research/ports/persistence.py`
- Modify: `tools/search_trial_ledger_history.py`
- Create: `tests/research/test_experiment_store_locked_primitives.py`
- Create: `tests/research/test_experiment_store_v3.py`
- Modify: `tests/research/test_experiment_store.py`
- Modify: `tests/research/test_experiment_registry.py`
- Modify: `tests/tools/test_search_trial_ledger_history.py`

**Produces:**

- `EvaluationArtifactWrite`
- store-owned `_immediate_transaction()` and opaque connection/generation/thread-bound write lease
- `_require_write_lock(conn, lease)`
- `_insert_evaluation_locked(conn, lease, write, search_id=None, config_hash=None)`
- `_append_event_outbox_locked(conn, lease, messages)`
- `prepare_evaluation_artifact(evaluation, dataset_version, recorded_ts)`
- `verify_portable_v2_subset(connection, require_evaluations=False)`
- exact v2-to-v3 `_ensure_family_schema()` and read-only `verify_family_authority()`

- [x] **A3.1 Extract locked primitives under legacy tests.**

Write RED tests that call a locked primitive outside a store-owned transaction, pass a foreign connection, trace SQL for nested `BEGIN`, and inject failure after each evaluation/link/audit/outbox write. Each public write must emit one `BEGIN IMMEDIATE` and one terminal commit or rollback. A locked primitive may not migrate, open another connection, or issue transaction control.

Run RED:

```powershell
rtk python -m pytest tests/research/test_experiment_store_locked_primitives.py -q -p no:cacheprovider
```

Refactor `insert_evaluation` and existing outbox writes to preserve public signatures and exact legacy hashes while delegating SQL bodies to locked primitives. Move evaluation serialization, `content_hash`, `run_id`, and audit intent preparation into one registry helper used by both legacy and family paths.

Run GREEN and legacy atomicity tests:

```powershell
rtk python -m pytest tests/research/test_experiment_store_locked_primitives.py tests/research/test_experiment_store.py tests/research/test_experiment_event_outbox.py tests/research/test_experiment_registry.py -q -p no:cacheprovider
```

- [x] **A3.2 Define and test exact schema v3 (moved to Pass C then Pass M).**

`family_schema.py` owns the complete DDL and expected column/trigger catalogs for:

`research_questions`, `research_question_aliases`, `strategy_lineage_roots`, `strategy_lineage_nodes`, `strategy_config_lineage_bindings`, `research_authority_events`, `selection_scopes`, `selection_scope_aliases`, `selection_scope_publishable_decisions`, `selection_scope_outcomes`, `research_programs`, `selection_decisions`, `global_oos_exposures`, `global_oos_exposure_consumptions`, `data_snooping_declarations`, `data_snooping_family_searches`, `data_snooping_proposal_registrations`, `data_snooping_proposals`, `data_snooping_hypotheses`, `data_snooping_search_emission_seals`, `data_snooping_family_freezes`, `data_snooping_oos_releases`, `data_snooping_oos_access_attempts`, `data_snooping_candidate_dispositions`, `data_snooping_evaluation_batches`, `data_snooping_family_oos_evaluations`, `data_snooping_evaluation_receipts`, `data_snooping_benchmark_returns`, `data_snooping_family_closures`, `data_snooping_grid_assessments`, `data_snooping_assessments`, and `artifact_publication_receipts`.

Every authority table gets no-update/no-delete triggers. Ordered authorities store `prev_record_hash` and `record_hash`. Unique constraints enforce one question per canonical scope, one root per source manifest, one root per config, one publishable decision per selection scope, one outcome per scope, one family per decision, one consumption per exposure, one receipt per config, one benchmark per family, one grid-assessment row per declared block/checkpoint/version, and one separate family-assessment conclusion per family.

Run RED migration tests:

```powershell
rtk python -m pytest tests/research/test_experiment_store_v3.py -q -p no:cacheprovider
```

Implement exact v2-to-v3 inside one `BEGIN IMMEDIATE`. A fault at every DDL/checkpoint must reopen as exact v2. Migration creates only empty family tables and never infers historical family data. `alpha_state` columns and bytes remain unchanged.

- [x] **A3.3 Split portable/read and family/write verification (Pass M).**

Rules:

- `verify_portable_v2_subset` accepts every `PRAGMA user_version >= 2`, verifies only the portable search/evaluation subset, and authorizes no mutation;
- `_ensure_search_schema` migrates below 2, accepts only exact known 2/3/4, and rejects 5 or later before any write;
- `_ensure_family_schema` migrates exact 2 to 3 and verifies the family subset on exact 3 or 4;
- Phase A contains no lifecycle schema function or v4 verifier;
- the tracked portable snapshot remains exact v2.

Run GREEN plus portability regression:

```powershell
rtk python -m pytest tests/research/test_experiment_store_v3.py tests/tools/test_search_trial_ledger_history.py tests/research/test_experiment_store.py -q -p no:cacheprovider
```

Suggested commit after authorization:

```powershell
rtk git add services/research/experiment_registry/family_schema.py services/research/experiment_registry/store.py services/research/experiment_registry/registry.py services/research/ports/persistence.py tools/search_trial_ledger_history.py tests/research/test_experiment_store_locked_primitives.py tests/research/test_experiment_store_v3.py tests/research/test_experiment_store.py tests/research/test_experiment_registry.py tests/tools/test_search_trial_ledger_history.py
rtk git commit -m "feat(evidence): add experiment family authority v3"
```

---

### Task 4 (A4): Authority-issued questions, lineage, selection scope, programs, decisions, and exposures

**Files:**

- Modify: `quantos/contracts/research.py`
- Modify: `tests/contracts/test_data_snooping_contracts.py`
- Create: `services/research/data_snooping/__init__.py`
- Create: `services/research/data_snooping/program_authority.py`
- Modify: `services/research/ports/persistence.py`
- Modify: `services/research/experiment_registry/store.py`
- Create: `tests/research/test_data_snooping_program_authority.py`
- Create: `tests/governance/test_data_snooping_authority_boundary.py`

**Produces:**

- `StructuredResearchQuestion`, `ResearchQuestionRef`, `ResearchQuestionAlias`, `SourceManifest`, `StrategyLineageRootRef`, `StrategyLineageNodeRef`, `DeploymentMandate`, `SelectionScopeRef`, `SelectionScopeAlias`, `SelectionScopeOutcome`, `ResearchProgramRef`, and `SelectionDecisionRef`
- `ResearchQuestionAuthority.issue_question`, `register_alias`, `register_lineage_root`, `register_lineage_node`, and `bind_config`
- `SelectionScopeAuthority.issue_scope`, `register_alias`, `issue_program`, `reserve_decision`, and `issue_oos_exposure`
- store append/CAS methods that accept canonical structured input but no caller-selected authority ID

- [ ] **A4.1 Write authority-identity and alias RED tests.**

Test exact canonical input idempotency, globally unique aliases, source-manifest root uniqueness, descendant root inheritance, one root per config, one publishable slot per scope, monotone decision IDs, and globally unique exposure fingerprints. Test rejection when owner text, requester, free-text title, question, strategy class, alias, descendant node, or caller-provided ID attempts to reset an existing economic mandate.

Every question, question alias, scope, scope alias, lineage root, lineage node, and config binding action requires a verified existing multi-party human grant and appends a `research_authority_events` record. Tests must prove non-human, reused, subject-mismatched, or tampered grants fail with zero writes.

Run RED:

```powershell
rtk python -m pytest tests/research/test_data_snooping_program_authority.py tests/governance/test_data_snooping_authority_boundary.py -q -p no:cacheprovider
```

- [ ] **A4.2 Implement authority-owned identity derivation.**

Derive IDs from canonical content plus the current authority-chain predecessor; do not accept IDs as parameters. Search/program APIs receive a structured question and source manifest, resolve them through registries, and fail on unknown or ambiguous identity. The scope binds sleeve/capital/risk budget, market/universe, label horizon, benchmark, and cost-policy class, while complete question/root sets are frozen by the family declaration.

The first reservation in a scope is `PUBLISHABLE`; all later decisions are `DIAGNOSTIC_ONLY`. Diagnostic OOS release remains forbidden until Phase B writes either `WINNER_PINNED` or `CLOSED_WITHOUT_PROMOTION`; Phase A stores and verifies the rule but cannot create either outcome.

Run GREEN plus store tamper tests:

```powershell
rtk python -m pytest tests/research/test_data_snooping_program_authority.py tests/governance/test_data_snooping_authority_boundary.py tests/research/test_experiment_store_v3.py -q -p no:cacheprovider
```

---

### Task 5 (A5): Pre-parse proposal ingestion and fail-closed DLP quarantine

**Files:**

- Modify: `quantos/contracts/research.py`
- Modify: `tests/contracts/test_data_snooping_contracts.py`
- Create: `config/data_snooping_dlp_policy.json`
- Create: `services/governance/dlp.py`
- Create: `services/research/data_snooping/proposal_ingest.py`
- Create: `services/research/data_snooping/restricted_raw_store.py`
- Modify: `services/intelligence/research_session.py`
- Modify: `services/intelligence/agent_loop.py`
- Modify: `services/intelligence/guided_search.py`
- Modify: `services/intelligence/evolutionary_search.py`
- Modify: `services/intelligence/mcts.py`
- Modify: `services/research/alpha_factory.py`
- Modify: `services/research/experiment_registry/store.py`
- Create: `tests/research/test_data_snooping_proposal_ingest.py`
- Create: `tests/governance/test_data_snooping_dlp_boundary.py`
- Modify: `tests/research/test_search_trial_provenance.py`
- Modify: `tests/intelligence/test_registered_candidate_batch.py`

**Produces:**

- `CandidateProposalRecord`, `CandidateIdentity`, `CandidateEmissionSeal`, `CandidateDisposition`, `RegisteredEnvelopeHandle`, and fixed proposal/disposition enums
- `DlpScanner.scan(payload, policy_id, policy_version) -> DlpScanResult`
- `RestrictedRawEnvelopeStore.put_clean` and `quarantine`
- `ProposalIngestionService.register_envelope` and `append_parse_result`
- `RegisteredEnvelopeHandle`, the only token accepted by a family parser

The policy file fixes maximum envelope bytes, coarse public length buckets, scanner implementation/version, secret and restricted-data rule IDs, fixed public reason codes, restricted clean/quarantine retention authority, fail-closed behavior, and the public core/envelope scan policy. The declaration binds the complete policy hash; callers cannot weaken or replace rules.

- [ ] **A5.1 Write RED ordering, quarantine, and leakage tests.**

Instrument scanner, restricted store, parser, data provider, evaluator, ExperimentStore, logger, and public exporter. Assert the successful order is bounded-bytes check, in-memory length/hash, DLP scan, restricted write, safe proposal-row commit, decode, canonical parse, identity append. Assert deny and scanner-error order terminates after safe quarantine metadata and parser/data/evaluator are never called. For the quarantine branch, public `proposal_id` is derived only from `family_id`, `search_id`, `emission_ordinal`, and the fixed `SECRET_QUARANTINED` marker. In two isolated authorities, different secret bytes at the same family/search/ordinal must produce the same public proposal ID while restricted records remain independently internally verifiable; a raw hash or scanner-result commitment must never be read during public-ID derivation.

Secret-shaped fixtures must be assembled at runtime from harmless fragments so repository text never contains a usable credential pattern. Assert public tables and every artifact object omit the restricted raw hash, exact length, matched-result hash, restricted blob address, and restricted head. Assert quarantine forces family `NOT_ASSESSABLE` and creates exactly one `SECRET_QUARANTINED` disposition.

Run RED:

```powershell
rtk python -m pytest tests/research/test_data_snooping_proposal_ingest.py tests/governance/test_data_snooping_dlp_boundary.py -q -p no:cacheprovider
```

- [ ] **A5.2 Implement the one legal proposal boundary.**

Accept only bounded `bytes`, media type, family/search identity, and monotone emission ordinal. For clean input, restricted storage keeps exact bytes and the public row binds only an approved clean-blob receipt. For deny/error, derive public `proposal_id = canonical_sha256({"family_id": family_id, "search_id": search_id, "emission_ordinal": emission_ordinal, "status": "SECRET_QUARANTINED"})`; this branch is forbidden from receiving the raw/result hash. The restricted authority derives a separate opaque quarantine ID independently of secret bytes, while ExperimentStore stores only the coarse length bucket, media type, fixed policy/version and reason code.

After safe-row commit, canonical parsing rejects invalid UTF-8, malformed JSON, duplicate keys, non-NFC text, syntax, schema, and type errors as `PRE_EVALUATION_INVALID`. Accepted duplicates remain separate proposals but map to one config and one alpha.

- [ ] **A5.3 Route every official generator through the sink before parsing.**

Family-mode generator callbacks must emit an opaque envelope to the sink before creating `AlphaDefinition`, scoring, requesting data, or evaluating. Existing non-family search remains backward-compatible, but governance tests must prove family mode has no alternative callback. Emission seals bind ordered proposal IDs, safe non-quarantine hashes, safe opaque quarantine IDs, accepted-config digest, counts, chain head, budget, and terminal reason.

Run GREEN and legacy provenance regression:

```powershell
rtk python -m pytest tests/research/test_data_snooping_proposal_ingest.py tests/governance/test_data_snooping_dlp_boundary.py tests/research/test_search_trial_provenance.py tests/intelligence/test_registered_candidate_batch.py -q -p no:cacheprovider
```

---

### Task 6 (A6): Governed observed-PIT handles, code manifest, and execution lease

**Files:**

- Modify: `quantos/contracts/research.py`
- Modify: `tests/contracts/test_data_snooping_contracts.py`
- Create: `config/code_manifest_policy.json`
- Create: `services/governance/code_manifest.py`
- Create: `services/deployment/code_attestation.py`
- Create: `services/data/research_dataset_authority.py`
- Modify: `services/data/research_adapter.py`
- Modify: `services/research/ports/market_data.py`
- Create: `tools/code_manifest_evidence.py`
- Create: `tests/governance/test_code_manifest.py`
- Create: `tests/deployment/test_execution_code_attestation.py`
- Create: `tests/data/test_research_dataset_authority.py`
- Create: `tests/governance/test_real_governed_family_data_boundary.py`

**Produces:**

- `CodeManifestPolicyRef`, `CodeManifestEvidence`, `ExecutionCodeAttestation`, `ImmutableArtifactLease`, `ObservedPitDatasetHandle`, `OosExposureRef`, and `OosExposureConsumption`
- authority-derived `derive_code_manifest_path_set` and pure `verify_code_manifest`
- `ExecutionCodeProvider.acquire_read_lease`, `terminal_attestation`, `lease_is_live`, and `release`
- `ResearchDatasetAuthority.issue_search_handle`, `build_oos_handle`, and `release_oos_snapshot`
- ExperimentStore-only `_consume_oos_exposure_locked(conn, exposure, decision_id, family_id, access_attempt_id, expected_version)`

- [ ] **A6.1 Write RED code-manifest completeness tests.**

The versioned policy fixes roots for search, strategy, detailed evaluation, return/cost, canonical codec, bootstrap, family authority/verifier, lifecycle guards, promotion, event schemas, top-tier entrypoint, dynamic registries, DSL/operator catalogs, dependency lockfiles, and the policy itself. Tests add a transitive helper, dynamic registry entry, config file, unresolved import, and extra caller path; omission, unknown dynamic input, path-set drift, dirty tree, or worktree-byte hashing must fail.

Tools may inspect Git, but the service verifier consumes evidence without invoking Git. Manifest bindings use exact committed Git blob object identities and SHA-256 of blob bytes, never checkout-translated bytes.

Run RED:

```powershell
rtk python -m pytest tests/governance/test_code_manifest.py -q -p no:cacheprovider
```

- [ ] **A6.2 Implement immutable execution attestation and lease.**

An attestation binds commit, policy, exact path-set and blob manifest, runtime/dependency locks, immutable artifact ID/digest, entrypoint, process-image identity, lease ID, and authority head. Acquisition rejects mutable worktrees and missing providers before OOS release. Terminal attestation must match start identity byte-for-byte and the lease must remain live through commit; drift or lease loss leads only to an irreversible incomplete attempt.

Run GREEN:

```powershell
rtk python -m pytest tests/governance/test_code_manifest.py tests/deployment/test_execution_code_attestation.py -q -p no:cacheprovider
```

- [ ] **A6.3 Write RED observed-PIT data authority tests.**

An eligible handle must bind observed provenance class, raw archive digest, normalized PIT-store digest/version, quality admission/head, PIT universe digest, ordered UTC period calendar, knowledge-time policy, label horizon, purge/embargo, cost and benchmark contracts. Test rejection for synthetic catalog, zero-byte database, raw `fetch_bars`, missing knowledge time, survivorship-biased universe, non-admitted quality, mutable handle, duplicate exposure fingerprint, overlap with sibling, consumption mismatch, release before freeze/consumption/attestation, and second release.

Run RED:

```powershell
rtk python -m pytest tests/data/test_research_dataset_authority.py tests/governance/test_real_governed_family_data_boundary.py -q -p no:cacheprovider
```

- [ ] **A6.4 Implement the OOS byte-release chokepoint.**

Use `SqlitePITStore.as_of` and `latest_as_of` only as lower-level PIT primitives; never classify `fetch_bars` as PIT-safe. The data authority creates and resolves immutable handles but has no public exposure-consumption method. `SelectionScopeAuthority.issue_oos_exposure` registers the authority-derived handle/fingerprint. Exposure availability is absence of one append-only consumption row. The only consumption implementation is ExperimentStore `_consume_oos_exposure_locked`, callable only by `FamilyOosEvaluationService.begin_evaluation_batch` inside the same outer `BEGIN IMMEDIATE` that appends the family `STARTED` attempt. The data authority releases an immutable snapshot only after independently verifying that exact committed exposure/decision/family/attempt tuple and the still-live exact execution lease.

Run GREEN:

```powershell
rtk python -m pytest tests/data/test_research_dataset_authority.py tests/governance/test_real_governed_family_data_boundary.py tests/data/test_persistent_store.py tests/data/test_research_adapter.py -q -p no:cacheprovider
```

---

### Task 7 (A7): Pure detailed evaluation and one-shot whole-family OOS batch

**Files:**

- Modify: `quantos/contracts/research.py`
- Modify: `tests/contracts/test_data_snooping_contracts.py`
- Create: `services/alpha/detailed_evaluation.py`
- Modify: `services/alpha/evaluator.py`
- Create: `services/research/data_snooping/evaluation.py`
- Create: `services/intelligence/family_search.py`
- Modify: `services/research/experiment_registry/store.py`
- Modify: `services/research/experiment_registry/registry.py`
- Modify: `services/research/ports/persistence.py`
- Create: `tests/alpha/test_detailed_evaluation.py`
- Create: `tests/research/test_family_oos_evaluation.py`
- Create: `tests/research/test_family_oos_evaluation_batch.py`
- Create: `tests/intelligence/test_family_search.py`

**Produces:**

- `EvaluationSeriesReceipt`, `CandidateReturnSeries`, `BenchmarkReturnSeries`, `FamilyEvaluationBatchReceipt`, `OosAccessAttempt`, and `OosReleaseReceipt`
- `DetailedAlphaEvaluation`
- pure `evaluate_alpha_detailed(definition, snapshot, cost, evaluator_config)`
- `FamilyOosEvaluationService.begin_evaluation_batch`, `evaluate_frozen_family`, `commit_evaluation_batch`, `finalize_incomplete`, and `recover_orphan_attempts`
- `SqliteExperimentStore.commit_family_evaluation_batch`

- [ ] **A7.1 Write RED pure-evaluator tests.**

Assert exact `PanelResult` gross/cost/net/turnover extraction, positive-zero normalization, non-null ordered event times, common calendar, finite float64, exact cost reconciliation, and deterministic output. Pass a catalog/store/lifecycle spy and prove the pure function cannot receive or mutate it. Preserve all legacy `AlphaEvaluator.evaluate` output by calling the pure core and then performing its existing projection mutation only in the legacy wrapper.

Run RED then GREEN:

```powershell
rtk python -m pytest tests/alpha/test_detailed_evaluation.py -q -p no:cacheprovider
rtk python -m pytest tests/alpha/test_detailed_evaluation.py tests/alpha/test_evaluator.py tests/alpha/test_panel_backtest.py -q -p no:cacheprovider
```

- [ ] **A7.2 Write RED begin/release/failure/recovery tests.**

`begin_evaluation_batch` must atomically append one `STARTED` attempt and consume the exact exposure. Only after commit may bytes be released. Test reused ID/fingerprint, second attempt, mismatched decision/family/config digest, missing freeze, missing or changed attestation, lease loss, and diagnostic sibling before scope outcome. Test restart recovery marks every orphan `STARTED` as `INCOMPLETE` before accepting new work and never allocates a replacement exposure.

Run RED:

```powershell
rtk python -m pytest tests/research/test_family_oos_evaluation.py -q -p no:cacheprovider
```

- [ ] **A7.3 Implement isolated whole-family computation.**

Evaluate the lexicographically sorted unique config set exactly once, and calculate the declared benchmark on the same immutable snapshot. All results remain isolated in memory until every candidate succeeds. No result is returned to a generator. Any exception produces `PERFORMANCE_ATTEMPTED_INCOMPLETE` for every accepted proposal, including duplicates; pre-evaluation-invalid and quarantined dispositions remain unchanged.

- [ ] **A7.4 Write and implement the atomic commit transaction.**

Failure injection after every write must prove all-or-none persistence of:

- existing immutable `evaluations` rows and audit intents through `_insert_evaluation_locked`;
- dedicated family-to-evaluation links, never `search_evaluations`;
- one batch receipt;
- one candidate receipt per unique config;
- one benchmark receipt;
- duplicate proposal dispositions pointing to the same receipt;
- exact period keys and gross/cost/net/turnover series;
- start and terminal execution identity and lease hashes; and
- the `COMPLETED` terminal attempt.

Test duplicate run ID with a different hash, second receipt for a config, missing benchmark, calendar mismatch, reconciliation failure, terminal attestation mismatch, and lease loss. The latter two commit only incomplete finalization and no return/evaluation rows.

Run GREEN and legacy evaluation regression:

```powershell
rtk python -m pytest tests/research/test_family_oos_evaluation.py tests/research/test_family_oos_evaluation_batch.py tests/intelligence/test_family_search.py tests/research/test_experiment_registry.py tests/research/test_experiment_store.py -q -p no:cacheprovider
```

---

### Task 8 (A8): Declaration, freeze, closure, staged assessment, and immutable trial proof

**Files:**

- Modify: `quantos/contracts/research.py`
- Modify: `tests/contracts/test_data_snooping_contracts.py`
- Create: `services/research/data_snooping/family.py`
- Create: `services/research/data_snooping/assessment.py`
- Modify: `services/research/experiment_registry/store.py`
- Create: `tests/research/test_data_snooping_family.py`
- Create: `tests/research/test_data_snooping_assessment.py`
- Modify: `tests/tools/test_search_trial_ledger_history.py`

**Produces:**

- `DataSnoopingDeclaration`, `FamilyFreezeReceipt`, `TrialLedgerProof`, `DataSnoopingFamilyManifest`, `DataSnoopingGridAssessment`, and the separate family-conclusion `DataSnoopingAssessment`; Phase A fixes `promotion_effect` to `NONE`
- `declare_family`, `attach_search`, `seal_search`, `freeze_family`, and `close_family`
- `run_declared_assessment_schedule` and `derive_family_assessment_conclusion`
- immutable content-addressed copies of the verified trial-ledger history/snapshot/seal used by closure

- [ ] **A8.1 Write RED declaration/freeze/closure completeness tests.**

Test that declaration precedes every search and proposal and binds the exact scope/program/question/root set, search/OOS separation, exposure, period/calendar, cost, benchmark, algorithm/grid/seed/schedule, code manifest, DLP policy, search budget/stopping rule, and all four counter definitions. A search ID is globally unique across family links, can be attached only before that search begins, and cannot be relinked. Freeze rejects open searches, missing or mismatched emission seals, late external proposals, budget/stopping-rule mismatch, unknown lineage, and code/data drift.

Closure independently recomputes:

- `raw_proposal_count` from every opaque emission, including invalid, duplicate, and quarantined proposals;
- `unique_performance_hypothesis_count` from distinct accepted config hashes and therefore the RC/SPA column count;
- `global_unique_config_count` at the immutable global-ledger history head; and
- `performance_access_attempt_count`, exactly one for an assessed family.

It also verifies exact proposal dispositions, one completed batch or one incomplete attempt, benchmark/candidate calendars, trial proof, scope history, sibling/exposure history, execution lease, and the deterministic winner rule. Any new proposal after freeze or any authority write after closure must be rejected by SQL triggers and service guards.

Run RED:

```powershell
rtk python -m pytest tests/research/test_data_snooping_family.py -q -p no:cacheprovider
```

- [ ] **A8.2 Implement immutable content-addressed trial proof.**

At closure, verify the live portable bundle and copy the exact history/snapshot/seal bytes under SHA-256-addressed paths or embed those exact bytes in the family bundle. Store the bound history head. Later global trials may extend the ledger but cannot rewrite the family's proof. `.gitattributes` must mark the SQLite proof binary and all byte-hashed JSON proof files as LF-stable in fresh Windows checkouts.

- [ ] **A8.3 Write RED staged assessment tests.**

For each declared block length and replication count, append one unique hash-chained `DataSnoopingGridAssessment` row. A larger row must prove the previous index stream is an exact prefix. All unresolved grid points advance under the declared schedule; selective favorable omission, new seed, reordered schedule, or operator-selected block length fails. Derive one separate `DataSnoopingAssessment` family conclusion from the latest row of every declared block, and forbid later grid rows once that conclusion exists.

Parameterize final outcomes: supports superiority, does not support, method disagreement, pending, unresolved, invalid variance, assumption unreviewed, non-publishable replication count, incomplete family, and quarantined proposal. Only the first may be Phase-B eligible, while all retain `promotion_effect="NONE"` in Phase A.

Run RED then GREEN:

```powershell
rtk python -m pytest tests/research/test_data_snooping_assessment.py -q -p no:cacheprovider
rtk python -m pytest tests/research/test_data_snooping_family.py tests/research/test_data_snooping_assessment.py tests/research/test_reality_check.py tests/tools/test_search_trial_ledger_history.py -q -p no:cacheprovider
```

---

### Task 9 (A9): Acyclic published artifact and publication-receipt projection

**Files:**

- Modify: `quantos/contracts/research.py`
- Modify: `tests/contracts/test_data_snooping_contracts.py`
- Create: `services/research/data_snooping/evidence.py`
- Create: `services/research/data_snooping/verification.py`
- Modify: `services/research/experiment_registry/store.py`
- Create: `tests/research/test_data_snooping_evidence.py`
- Create: `tests/research/test_data_snooping_publication_projection.py`

**Produces:**

- `DataSnoopingEvidenceRef`, `ArtifactCore`, `ArtifactCoreScanReceipt`, `PublishedArtifactEnvelope`, `ArtifactPublicationReceipt`, and `ArtifactPublicationReceiptProjection`
- `build_artifact_core`
- `verify_artifact_core`
- `export_published_artifact`
- `verify_published_artifact(envelope_path, projection_path, live_authority=None)`
- content-addressed `PublishedArtifactEnvelope` and `ArtifactPublicationReceiptProjection`

- [ ] **A9.1 Write RED exact-byte publication tests.**

The test pipeline is fixed:

1. canonicalize `ArtifactCore`;
2. DLP scan those exact core bytes;
3. create `ArtifactCoreScanReceipt` for ALLOW only;
4. canonicalize `PublishedArtifactEnvelope` containing core plus core receipt;
5. DLP scan exact envelope bytes;
6. append the complete publication receipt row;
7. write the content-addressed envelope;
8. write a separately content-addressed projection containing the complete canonical publication row and genesis-to-row authority chain.

Test that no object points to a hash which recursively depends on itself. Tamper every core, scan, envelope, publication row, chain, lineage, policy, terminal head, path, and sidecar field. Test missing predecessor, truncated chain, wrong terminal head, non-addressed filename, secret-derived quarantine field, exact length leak, and opaque raw envelope leak.

Run RED:

```powershell
rtk python -m pytest tests/research/test_data_snooping_evidence.py tests/research/test_data_snooping_publication_projection.py -q -p no:cacheprovider
```

- [ ] **A9.2 Implement one shared semantic verifier.**

The self-contained core embeds the declaration, complete safe authority projection, exact trial proof, candidate and benchmark series, assessment grid, selected winner, code and data identities, execution attestation/lease proof, and policy versions required for recomputation. The verifier replays canonical bytes, all hashes and chains, complete authority projections, scope/question/lineage/exposure history, trial proof, candidate/benchmark reconciliation, execution attestation and lease, assessment grid, winner selection, DLP receipts, envelope binding, and publication projection. The live verifier additionally checks the live authority row and any required suffix; the offline verifier trusts only the official embedded authority projection and complete sidecar chain. Neither trusts a summary boolean.

Run GREEN and deterministic re-export check:

```powershell
rtk python -m pytest tests/research/test_data_snooping_evidence.py tests/research/test_data_snooping_publication_projection.py -q -p no:cacheprovider
```

Export the same family twice into separate temporary directories and assert byte-identical artifact names and contents.

---

### Task 10 (A10): Honest 39/8 historical audit and semantic top-tier checker

**Files:**

- Create: `tools/data_snooping_evidence.py`
- Create: `tests/tools/test_data_snooping_evidence.py`
- Modify: `services/governance/top_tier_assessment.py`
- Modify: `config/top_tier_requirements.json`
- Modify: `scripts/assess_top_tier.py`
- Modify: `tests/governance/test_top_tier_assessment.py`
- Modify: `tests/governance/test_top_tier_boundary.py`

**Produces:**

- CLI subcommands `audit-historical`, `close`, `assess`, `export`, and `verify`
- top-tier checker `data_snooping_family_evidence`
- optional independent requirement `ALPHA.DATA_SNOOPING.FAMILY_CONTROL`

- [ ] **A10.1 Write the historical-audit RED test.**

Read retained session `search-e9e0e7a0f15611573906ae2a` from portable evidence and produce a safe non-authority artifact with exactly:

- `registered_candidate_count=39`;
- `linked_evaluation_count=8`;
- `missing_disposition_count=39`;
- `missing_period_return_count=39`;
- `authority_class="HISTORICAL_AUDIT_ONLY"`;
- `assessment_status="NOT_ASSESSABLE"`; and
- no evidence ref.

Compare every ExperimentStore family-table count before and after to prove zero writes. Reject pooling the global 40/10 ledger, the earlier 1/1 session, or different dataset versions into a family. Never synthesize missing period returns or dispositions.

Run RED then GREEN:

```powershell
rtk python -m pytest tests/tools/test_data_snooping_evidence.py -q -p no:cacheprovider
```

- [ ] **A10.2 Replace path existence with semantic evidence checking.**

Register `data_snooping_family_evidence` in the assessment checker map. It must call the same production verifier from Task A9 and reject fixture/demo/manual/detached evidence, test replication counts, synthetic data, zero-byte data, dirty/mutable execution, absent lease proof, absent publication scan, `NOT_ASSESSABLE`, wrong schema, and invalid authority chains.

Phase A must return at most `PARTIAL/M3`, even for a valid v3 `ASSESSED` diagnostic artifact, because v4 activation and promotion chokepoints do not exist yet. Preserve the independent global trial-ledger requirement and its current credit.

Run GREEN:

```powershell
rtk python -m pytest tests/governance/test_top_tier_assessment.py tests/governance/test_top_tier_boundary.py tests/tools/test_data_snooping_evidence.py -q -p no:cacheprovider
rtk python scripts/assess_top_tier.py --check
```

Expected assessment: the repository remains honestly `NOT_TOP_TIER`; Phase A cannot increase the data-snooping capability to PASS.

---

### Task 11 (A11): Documentation, governance scan, and Phase A validation

**Files:**

- Modify: `docs/PROJECT_STATE.md`
- Modify: `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`
- Modify: `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`
- Modify: `docs/runbooks/l2-research-operations.md`
- Modify: `docs/engineering/specs/2026-07-17-paper-to-quantos-gap-analysis.md`
- Modify: `.gitattributes`
- Create or refresh only through generators: approved evidence outputs owned by Tasks A8–A10

- [ ] **A11.1 Update living truth without claiming promotion readiness.**

Document:

- Phase A schema v3 is diagnostic-only;
- exact RC/SPA method and assumptions;
- observed-PIT and immutable execution requirements;
- public/restricted DLP split;
- historical 39/8 `NOT_ASSESSABLE` result;
- Phase B remains required for lifecycle enforcement;
- current full-gate status is not changed unless a fresh canonical run proves it.

- [ ] **A11.2 Run focused feature regression.**

```powershell
rtk python -m pytest tests/contracts/test_canonical.py tests/contracts/test_data_snooping_contracts.py tests/research/test_reality_check_bootstrap.py tests/research/test_reality_check.py tests/research/test_experiment_store_locked_primitives.py tests/research/test_experiment_store_v3.py tests/research/test_data_snooping_program_authority.py tests/research/test_data_snooping_proposal_ingest.py tests/data/test_research_dataset_authority.py tests/governance/test_code_manifest.py tests/deployment/test_execution_code_attestation.py tests/alpha/test_detailed_evaluation.py tests/research/test_family_oos_evaluation.py tests/research/test_family_oos_evaluation_batch.py tests/research/test_data_snooping_family.py tests/research/test_data_snooping_assessment.py tests/research/test_data_snooping_evidence.py tests/research/test_data_snooping_publication_projection.py tests/tools/test_data_snooping_evidence.py tests/governance/test_top_tier_assessment.py -q -p no:cacheprovider
```

- [ ] **A11.3 Run governance and compatibility regressions.**

```powershell
rtk python apps/research-console/scripts/run_paper_to_live_governance_check.py
rtk python -m pytest tests/governance tests/research/test_experiment_store.py tests/research/test_experiment_registry.py tests/tools/test_search_trial_ledger_history.py tests/alpha/test_evaluator.py tests/intelligence/test_agent_loop.py -q -p no:cacheprovider
```

- [ ] **A11.4 Run the canonical gate and classify failures honestly.**

```powershell
rtk python scripts/validate.py
```

Do not call Phase A complete until all feature-owned tests pass. A retained container recipe-byte mismatch or stale external backup may remain a separately documented canonical-gate failure; neither may be hidden, regenerated without authority, or attributed to this feature without evidence.

## Plan self-review matrix

| Design requirement | Owning task | Required proof |
|---|---|---|
| Canonical float/JSON/hash identity | A1 | exact-byte, duplicate, NFC, LF, negative-zero tests |
| White RC, all SPA variants, deterministic bootstrap | A2 | independent golden fixtures, stream-prefix and permutation tests |
| v2 portable compatibility and exact additive v3 | A3 | fault-injected migration and subset/full verifier separation |
| No nested transactions | A3, A7 | SQLite trace plus all-or-none kill tests |
| Authority-owned question/root/scope/decision/exposure | A4 | alias, split, reroot, grant and CAS attacks |
| Raw registration before parsing and safe quarantine | A5 | call-order spies and public-field absence tests |
| Observed PIT only | A6 | synthetic, zero-byte, knowledge-time, universe and quality negative tests |
| Actual execution identity and unbroken lease | A6, A7 | start/end attestation and lease-loss tests |
| One blind complete-family OOS attempt | A7 | durable STARTED, exposure CAS, atomic batch, orphan recovery |
| Four independent counters and immutable closure | A8 | store recomputation and post-freeze/closure rejection |
| Replication schedule and any-grid decision | A8 | prefix-chain, simultaneous advancement and unresolved tests |
| Acyclic scanned artifact and complete sidecar | A9 | byte replay, chain replay, tamper matrix and deterministic export |
| Historical 39/8 honesty | A10 | read-only 39/8 `NOT_ASSESSABLE` audit |
| Semantic top-tier check without score inflation | A10 | fixture/synthetic/v3 negative matrix |
| No lifecycle or live-trading effect | all tasks | Phase A contract, governance scan, unchanged alpha-state schema |

## Independent review gate

Before Task A1 implementation begins, an independent reviewer must compare this plan line-by-line with design §§3–6, 8, 10, and 11 and report every P1/P2 omission. Implementation starts only when no P1/P2 remains. Each later task receives a fresh code review before its changes are merged into the shared branch.
