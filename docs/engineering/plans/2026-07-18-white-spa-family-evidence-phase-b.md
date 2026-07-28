# White RC / Hansen SPA Family Evidence — Phase B Implementation Plan

> **For agentic workers:** Use the repository's subagent-driven implementation workflow (or its plan-execution alternative) task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate ExperimentStore lifecycle authority v4 and make complete, verified family evidence the only non-human route from DRAFT to TESTING and TESTING to VALIDATED, while preserving L2 dry-run safety and burn-first human approval for later states.

**Architecture:** Phase B is an exclusive v3-to-v4 cutover. It snapshots legacy alpha state, introduces optimistic state versions, append-only transition records and evidence pins, closes all legacy state-write bypasses, and centralizes positive transitions in one deferred family-promotion service. Every positive transition rederives family significance and current multiple-testing guards under the store lock, holds stable external-authority heads through commit, and writes state, transition, outbox, pin, and scope outcome atomically as required. No Phase B component enables live trading.

**Tech stack:** Python 3.11+, stdlib SQLite transactions/CAS, Pydantic v2, existing AlphaCatalog, ExperimentStore, multi-party ApprovalStore, event outbox, governance scanner, and Phase A family-evidence verifier.

**Prerequisite:** Every Phase A acceptance item in `docs/engineering/plans/2026-07-18-white-spa-family-evidence-phase-a.md` is GREEN on an exact schema-v3 authority. Phase B must not be implemented against mocks that omit Phase A authority chains or assessment rows.

**Authoritative design:** `docs/engineering/specs/2026-07-18-white-spa-family-evidence-design.md`.

## Global constraints

- Keep all L2 safety flags unchanged. `APPROVED` remains a research-governance state and does not authorize broker connectivity, order submission, or live capital.
- Add no broker SDK, live adapter implementation, network credential path, or AI direct-trading path.
- Activation is explicit and exclusive; opening a v3 store does not silently activate v4.
- The v4 activation marker is inserted last in the same transaction as the state-version column, lifecycle tables, triggers, and complete legacy baseline. A failed activation leaves exact v3.
- After activation, `alpha_state.state` and `state_version` may change only through store-owned versioned transition primitives. Snapshot/catalog APIs become projection-only for existing rows.
- `DeferredFamilyPromotionService` is the only non-human positive-transition orchestrator. Research loops, factories, evaluators, maintenance jobs, recovery workers, and catalog objects cannot call a positive state seam directly.
- Both DRAFT-to-TESTING and TESTING-to-VALIDATED rederive the exact positive family predicate from authority rows. A caller boolean, latest evaluation, summary verdict, selected grid point, or stale evidence ref cannot substitute.
- `TESTING -> VALIDATED` commits state, evidence pin, transition record, canonical outbox event, and `WINNER_PINNED` scope outcome as one transaction.
- The alternative scope result `CLOSED_WITHOUT_PROMOTION` never mutates alpha state and is irreversible.
- Later PAPER_TRADING, SHADOW, and APPROVED transitions reload the same persisted pin and winner. A new evidence ref or later unrelated evaluation cannot replace it.
- Final human approval is burn-first and at-most-once. Failure after ApprovalStore consumption never restores the request.
- No legacy advanced state is backfilled with evidence. An advanced alpha without a valid pin/transition is permanently `LEGACY_EVIDENCE_PIN_MISSING` for further positive advancement.
- Do not stage, commit, push, activate a production authority, stop external writers, or mutate an external approval store without separate operational authorization. Tests use isolated temporary databases.
- Every repository command starts with `rtk`; canonical validation is `rtk python scripts/validate.py`.

## Phase B completion definition

Phase B completes only when:

1. activation failure injection proves every reopened store is exact v3 or complete v4;
2. stale and legacy writers cannot alter state after activation;
3. every positive family predicate and multiple-testing guard is rederived transactionally;
4. DRAFT-to-TESTING, legacy TESTING adoption, TESTING-to-VALIDATED, close-without-promotion, PAPER, and APPROVED seams pass race and kill matrices;
5. every direct positive-transition bypass in intelligence/factory/promotion code is removed and AST-governed;
6. lifecycle and selection-scope events verify through the canonical event catalog and projection;
7. one real observed-PIT governed family is semantically verified from an official live authority or exact offline projection; and
8. the canonical gate is rerun with every feature-owned group GREEN.

## File responsibility map

- `quantos/contracts/research.py`: v4 activation, state row, transition, guard-input, guard-result, close-reason, writer-lease, and evidence-pin contracts.
- `quantos/contracts/events.py` and `quantos/contracts/event_schemas.py`: new versioned lifecycle-transition and selection-scope-outcome events; historical promotion events remain unchanged.
- `services/research/experiment_registry/lifecycle_schema.py`: exact v4 DDL, constraints, triggers, activation and verifier catalogs.
- `services/research/experiment_registry/store.py`: sole lifecycle-state authority and composite transaction owner.
- `services/alpha/positive_transition.py`: family predicate, current DSR/BHY, stable authority-head coordinator, and guard snapshot derivation.
- `services/alpha/family_promotion.py`: unique deferred orchestration service.
- `services/governance/approval_store.py`: append-only burn-first alpha-transition consumption authority.
- `services/alpha/promotion.py`: later-state executor that delegates state authority to ExperimentStore.
- `services/intelligence/agent_loop.py` and `services/alpha/factory.py`: research-only candidate generation; no state advancement.
- `services/governance/top_tier_assessment.py`: semantic `REAL_GOVERNED_FAMILY_V1` capability checker.

---

### Task 1 (B1): v4 lifecycle contracts, writer lease, and canonical events

**Files:**

- Modify: `quantos/contracts/research.py`
- Modify: `quantos/contracts/events.py`
- Modify: `quantos/contracts/event_schemas.py`
- Modify: `services/alpha/events.py`
- Modify: `services/research/ports/persistence.py`
- Create: `services/deployment/lifecycle_activation.py`
- Create: `tests/contracts/test_lifecycle_authority_contracts.py`
- Modify: `tests/contracts/test_event_schema_registry.py`
- Create: `tests/deployment/test_lifecycle_writer_lease.py`

**Produces:**

- `LifecycleWriterLeaseRef`, `LifecycleAuthorityActivation`, `AlphaStateAuthorityRow`, `AlphaEvidencePin`, `AlphaTransitionRecord`, `LegacyAlphaStateBaselineRef`, `AuthorityHeadRef`, `ApprovalConsumptionRef`, `PositiveTransitionGuardRefs`, `VerifiedPositiveTransitionInputs`, `CurrentMultipleTestingDecision`, `SelectionScopeCloseReason`, and `PromotionGuardSnapshot`
- `LifecycleWriterLeaseAuthority.acquire_exclusive`, `verify_exclusive`, and `release`
- canonical event types `research.alpha.lifecycle_transition` and `research.selection_scope.outcome`

- [ ] **B1.1 Write strict v4 contract RED tests.**

Test positive epoch timestamps, non-empty IDs/hashes, exact state/version increments, source/destination compatibility, required evidence pin for VALIDATED and later, required scope outcome binding, required approval consumption only for APPROVED, and forbidden approval consumption on non-human transitions. Test that `LEGACY_TESTING_ADOPTED_FOR_FAMILY` is TESTING-to-TESTING with version 0 to 1 and binds the exact baseline/activation hash.

Run RED:

```powershell
rtk python -m pytest tests/contracts/test_lifecycle_authority_contracts.py -q -p no:cacheprovider
```

- [ ] **B1.2 Add the exclusive writer-lease boundary.**

The lease authority records expected writer classes `research-runner`, `promotion`, `maintenance`, and `recovery`; activation is allowed only when each is proven stopped and one exclusive activation lease remains live. A caller cannot self-report the condition. Tests cover missing writer, duplicate lease, expired lease, wrong authority head, process restart, and release before activation commit.

Run RED then GREEN:

```powershell
rtk python -m pytest tests/deployment/test_lifecycle_writer_lease.py -q -p no:cacheprovider
```

- [ ] **B1.3 Add new event schemas without reinterpreting old events.**

`research.alpha.lifecycle_transition` binds transition ID, alpha, source/destination state, versions, actor/audit identity, evidence-pin hash, scope-outcome hash, guard-snapshot hash, approval-consumption ID/hash when present, previous transition hash, and transition hash. `research.selection_scope.outcome` binds the immutable outcome row and authority head. Retain historical `research.alpha.promoted` v1.x semantics and registrations.

Run GREEN:

```powershell
rtk python -m pytest tests/contracts/test_lifecycle_authority_contracts.py tests/contracts/test_event_schema_registry.py -q -p no:cacheprovider
```

---

### Task 2 (B2): Exclusive v3-to-v4 activation and closure of legacy state writers

**Files:**

- Create: `services/research/experiment_registry/lifecycle_schema.py`
- Modify: `services/research/experiment_registry/store.py`
- Modify: `services/research/experiment_registry/registry.py`
- Modify: `services/research/ports/persistence.py`
- Modify: `services/alpha/catalog.py`
- Modify: `services/alpha/maintenance.py`
- Modify: `services/alpha/retirement.py`
- Create: `tests/research/test_experiment_lifecycle_activation.py`
- Create: `tests/research/test_experiment_lifecycle_writers.py`
- Modify: `tests/research/test_experiment_store.py`
- Modify: `tests/research/test_experiment_registry.py`

**Produces:**

- `SqliteExperimentStore.activate_lifecycle_authority`
- `_ensure_lifecycle_schema` and `verify_lifecycle_authority`
- `insert_alpha_initial`
- projection-only v4 behavior for `save_catalog_snapshot`, `save_evaluation_catalog_snapshot`, and `upsert_alpha`
- locked `_transition_alpha_locked`
- `commit_non_positive_transition` for REJECTED, DEGRADED, PAUSED, and RETIRED changes

- [ ] **B2.1 Write activation RED tests with a checkpoint matrix.**

Start from exact v3 with parameterized legacy alpha states. Inject failure before and after every DDL, every baseline insert, every trigger creation, state-version initialization, activation record construction, marker insert, and commit. Reopen using a new connection and assert only two legal results: exact untouched v3 or complete verified v4. No intermediate columns/tables/rows are accepted.

The one activation transaction must:

1. verify the live exclusive writer lease and exact v3 family authority;
2. canonicalize and hash the ordered pre-cutover `alpha_state` snapshot;
3. add `state_version INTEGER NOT NULL DEFAULT 0`;
4. create `lifecycle_authority_activation`, `legacy_alpha_state_baseline`, `alpha_evidence_pins`, and `alpha_transition_records` plus all indexes/triggers;
5. insert one immutable baseline row for every exact alpha/state/version-0 tuple;
6. bind schema/code-manifest/policy/lease/timestamp and baseline head; and
7. insert the single activation marker last.

The v4 DDL includes a partial unique index on `alpha_transition_records(approval_consumption_hash) WHERE approval_consumption_hash IS NOT NULL`. ApprovalStore uniqueness is not a substitute: ExperimentStore itself must reject reuse of one verified consumption ref by any second transition.

Run RED:

```powershell
rtk python -m pytest tests/research/test_experiment_lifecycle_activation.py -q -p no:cacheprovider
```

- [ ] **B2.2 Implement exact lifecycle schema verification.**

`_ensure_lifecycle_schema` performs only explicit exact v3-to-v4 activation and otherwise requires exact v4. It rejects v2, v3 without activation intent, version 5 or later, missing marker, missing baseline row, extra/missing column, broken trigger, code-manifest drift, or invalid chain before state mutation. Family-only reads still accept exact v3 or v4; portable-v2 reads remain subset-only.

Run GREEN:

```powershell
rtk python -m pytest tests/research/test_experiment_lifecycle_activation.py tests/research/test_experiment_store_v3.py -q -p no:cacheprovider
```

- [ ] **B2.3 Write and implement stale-writer rejection.**

Open a v3 writer connection before activation, activate through a separate authorized connection, then attempt every old state-write API. Each must detect activation/version drift before mutation and fail with zero changes. Under v4:

- `insert_alpha_initial` is the only row-creation path and fixes DRAFT/version 0;
- snapshot/upsert APIs may update projection fields only when supplied state/version equal stored state/version;
- existing-row state or version change is rejected;
- an advanced state cannot be inserted through evaluation snapshot creation;
- `_transition_alpha_locked` requires a store-owned transaction and matching append-only transition record/outbox composition.

Run RED then GREEN:

```powershell
rtk python -m pytest tests/research/test_experiment_lifecycle_writers.py -q -p no:cacheprovider
rtk python -m pytest tests/research/test_experiment_lifecycle_writers.py tests/research/test_experiment_store.py tests/research/test_experiment_registry.py -q -p no:cacheprovider
```

- [ ] **B2.4 Add governance ownership checks for SQL write sites.**

AST/text governance must enumerate every `UPDATE alpha_state`, `INSERT OR REPLACE`, catalog `_set_state`, and public state mutation. Only lifecycle-schema migration, `insert_alpha_initial`, and locked transition primitives are allowed. Test both direct SQL and indirect manager/catalog imports.

- [ ] **B2.5 Route every non-positive state change through versioned authority.**

Add a store-owned `commit_non_positive_transition` wrapper that CAS-checks current state/version, verifies transition legality and actor class, appends the transition record and outbox, then updates state/version in one transaction. It never requires superiority evidence and cannot target TESTING, VALIDATED, PAPER_TRADING, SHADOW, or APPROVED. Migrate rejection, degradation, pause, retirement, maintenance, and recovery callers from `AlphaLifecycleManager`/catalog writes to this wrapper. Failure injection proves state/transition/outbox are all present or all absent, and automated de-risking remains fail-closed and does not require a family pin.

Run focused de-risking regression:

```powershell
rtk python -m pytest tests/alpha/test_maintenance.py tests/alpha/test_retirement.py tests/research/test_experiment_lifecycle_writers.py -q -p no:cacheprovider
```

---

### Task 3 (B3): Transaction-owned family predicate and positive-transition guard snapshot

**Files:**

- Create: `services/alpha/positive_transition.py`
- Modify: `services/alpha/guards.py`
- Modify: `services/alpha/multiple_testing.py`
- Modify: `services/research/experiment_registry/store.py`
- Modify: `services/research/ports/persistence.py`
- Create: `tests/alpha/test_positive_transition_guards.py`
- Create: `tests/alpha/test_positive_transition_coordinator.py`

**Produces:**

- `_verify_promotable_family_locked(conn, evidence_ref, expected_alpha_id)`
- `_derive_current_multiple_testing_locked(conn, winner_evaluation_hash, fdr_q)`
- `PositiveTransitionCoordinator.acquire_verified_guard_snapshot(refs)`
- immutable `PromotionGuardSnapshot`

- [ ] **B3.1 Write the exact positive-family predicate matrix.**

The shared locked verifier must reload family, declaration, closure, exact winner config/evaluation, complete assessment grid, effective aggregate assessment, scope history, publication receipt, execution attestation/lease proof, and evidence ref. The only positive predicate is:

- `assessment_status=ASSESSED`;
- `diagnostic_verdict=SUPPORTS_SUPERIORITY`;
- every preregistered block-grid row is uncertainty-resolved;
- White RC is `REJECT` at every grid point;
- SPA-consistent is `REJECT` at every grid point;
- replication count and assumption/policy status are promotion-eligible; and
- winner/evaluation/ref and current code-verifier policy match exactly.

Negative tests cover every other diagnostic verdict, one ambiguous grid, one method disagreement, one non-reject, a favorable max-p summary hiding a bad grid, test replications, stale code, missing lease, wrong winner, and caller boolean substitution.

Run RED:

```powershell
rtk python -m pytest tests/alpha/test_positive_transition_guards.py -q -p no:cacheprovider
```

- [ ] **B3.2 Recompute DSR and BHY inside the store lock.**

Under the same `BEGIN IMMEDIATE`, recompute Deflated Sharpe using the current global DISTINCT-config trial count and recompute the full current BHY population, survivor set, and population head. The caller supplies no precomputed DSR/BHY decision. Race tests insert a new trial or append a population row after outer prechecks; the committed decision must use the new current state or reject.

- [ ] **B3.3 Hold stable external authority heads in fixed order.**

Leakage, PBO/CPCV, regime, research-risk, paper, model-card, dataset, code-attestation, and publication inputs arrive as immutable versioned refs. The coordinator acquires stable verified head leases in one documented global order, holds them through ExperimentStore commit, and rechecks before release. Missing lease, wrong order, changed head, or unverifiable authority causes rollback.

Run GREEN:

```powershell
rtk python -m pytest tests/alpha/test_positive_transition_guards.py tests/alpha/test_positive_transition_coordinator.py tests/alpha/test_multiple_testing.py -q -p no:cacheprovider
```

---

### Task 4 (B4): Deferred winner transition to TESTING and explicit legacy TESTING adoption

**Files:**

- Create: `services/alpha/family_promotion.py`
- Modify: `services/research/experiment_registry/store.py`
- Modify: `services/research/ports/persistence.py`
- Create: `tests/alpha/test_deferred_family_promotion.py`
- Create: `tests/alpha/test_legacy_testing_adoption.py`

**Produces:**

- `DeferredFamilyPromotionService.promote_manifest_winner`
- `SqliteExperimentStore.commit_manifest_winner_to_testing`
- `SqliteExperimentStore.commit_legacy_testing_winner_adoption`

- [ ] **B4.1 Write DRAFT-to-TESTING RED tests.**

Test exact winner only, DRAFT/version CAS, complete positive predicate, current guard snapshot, no scope outcome, no diagnostic sibling OOS race, and canonical transition/outbox append. Non-winners remain byte-identical DRAFT. Any pre-existing non-winner state receives no pin or authority. A crash before commit leaves DRAFT; a crash after commit leaves durable TESTING with exactly one transition/outbox.

Run RED:

```powershell
rtk python -m pytest tests/alpha/test_deferred_family_promotion.py -q -p no:cacheprovider
```

- [ ] **B4.2 Implement the unique deferred service.**

The service accepts a `DataSnoopingEvidenceRef`, actor, guard refs, timestamp, and expected alpha version. It does not accept a verdict boolean or catalog manager. It calls one store transaction which repeats Task B3 verification and writes the DRAFT-to-TESTING state/version, transition record, and outbox atomically. A second invocation resolves idempotently only when the exact transition already committed; otherwise it fails.

- [ ] **B4.3 Write and implement legacy TESTING adoption.**

Only an activation baseline of TESTING, current TESTING/version 0, unchanged activation snapshot, exact family winner, no pin/outcome/sibling race, and the complete positive predicate may produce `LEGACY_TESTING_ADOPTED_FOR_FAMILY`. It is a TESTING-to-TESTING transition to version 1, binds the baseline and family, creates no DRAFT history, no evidence pin, and no scope outcome. Post-activation TESTING, unrelated TESTING, and every baseline state other than TESTING are rejected.

Run GREEN:

```powershell
rtk python -m pytest tests/alpha/test_deferred_family_promotion.py tests/alpha/test_legacy_testing_adoption.py -q -p no:cacheprovider
```

---

### Task 5 (B5): Atomic validation pin, irreversible scope outcome, and total close

**Files:**

- Modify: `services/research/experiment_registry/store.py`
- Modify: `services/research/ports/persistence.py`
- Modify: `services/alpha/family_promotion.py`
- Create: `tests/alpha/test_family_promotion_atomicity.py`
- Create: `tests/alpha/test_selection_scope_close.py`
- Modify: `tests/research/test_experiment_event_outbox.py`

**Produces:**

- `commit_validated_transition_with_evidence_pin`
- `close_selection_scope_without_promotion`
- deferred service outcome `WINNER_PINNED` or `CLOSED_WITHOUT_PROMOTION`

- [ ] **B5.1 Write five-authority validation RED tests.**

Starting from this family's TESTING transition or exact adoption marker, inject failure after each write and at commit. The following five records must all exist or all be absent:

1. CAS-updated TESTING-to-VALIDATED alpha state/version;
2. one append-only `AlphaEvidencePin`;
3. one hash-chained `AlphaTransitionRecord`;
4. one canonical outbox event; and
5. one `WINNER_PINNED` selection-scope outcome binding the same alpha/ref.

The transaction reloads and rederives the family predicate and guard snapshot, requires the current scope history tip, and rejects pre-existing outcome, sibling OOS, stale version, wrong predecessor transition, wrong evaluation, or missing publication proof.

Run RED:

```powershell
rtk python -m pytest tests/alpha/test_family_promotion_atomicity.py -q -p no:cacheprovider
```

- [ ] **B5.2 Implement the atomic validation transaction.**

The evidence pin binds alpha, family/decision/program/scope, effective assessment, closure, exact winner config/evaluation, source-code manifest, verifier policy, current guard snapshot, and transition. SQL uniqueness and service verification must make state-without-pin, pin-without-transition, or outcome-with-wrong-ref impossible.

- [ ] **B5.3 Write close totality and race RED tests.**

Parameterize winner state as DRAFT, this-family TESTING, unrelated TESTING, legacy TESTING, REJECTED, VALIDATED, PAPER_TRADING, SHADOW, APPROVED, RETIRED, PAUSED, and DEGRADED. If this family created no pin or validated-or-later transition, close must append exactly one `CLOSED_WITHOUT_PROMOTION` outcome and leave alpha bytes unchanged. A pre-existing advanced state gains no authority from this family.

Use two SQLite connections and a barrier for close versus adoption and close versus validation. Exactly one transaction wins. A committed close permanently invalidates the family ref, cannot convert to `WINNER_PINNED`, and is the only Phase B alternative that permits diagnostic sibling OOS.

Run GREEN:

```powershell
rtk python -m pytest tests/alpha/test_family_promotion_atomicity.py tests/alpha/test_selection_scope_close.py tests/research/test_experiment_event_outbox.py -q -p no:cacheprovider
```

---

### Task 6 (B6): Pinned PAPER_TRADING/SHADOW/APPROVED transitions and burn-first human approval

**Files:**

- Modify: `services/governance/approval_store.py`
- Modify: `services/governance/approval.py`
- Modify: `services/research/experiment_registry/store.py`
- Modify: `services/research/ports/persistence.py`
- Modify: `services/alpha/promotion.py`
- Modify: `services/promotion_api/main.py`
- Create: `tests/governance/test_approval_consumption.py`
- Modify: `tests/governance/test_approval_store.py`
- Modify: `tests/governance/test_approval_store_tamper.py`
- Modify: `tests/alpha/test_promotion_execute.py`
- Modify: `tests/governance/test_promotion_api_endpoints.py`

**Produces:**

- `ApprovalStore.consume_for_alpha_transition`
- `SqliteExperimentStore.commit_positive_transition_with_pinned_evidence`
- promotion executor with no manager/catalog state authority and no latest-evaluation lookup

- [ ] **B6.1 Write append-only approval-consumption RED tests.**

One `BEGIN IMMEDIATE` in ApprovalStore reloads an APPROVED request, checks subject/action/executor, expected alpha state/version, evidence-pin hash, and exact authority head, appends the CONSUMED step, inserts one immutable consumption row, and derives consumption ID/hash/head. The caller cannot select those values. Unique constraints prevent reuse by another alpha, action, version, or transition.

Race two workers and two store instances. At most one consumption succeeds. Tamper request, prefix, subject, version, pin, action, executor, or head and assert zero consumption.

After obtaining one valid committed consumption ref, bypass the promotion executor in a test and call the ExperimentStore transition seam twice with that same ref. The first may commit once; the second must fail on the v4 partial unique index with no second state, transition, or outbox write.

Run RED:

```powershell
rtk python -m pytest tests/governance/test_approval_consumption.py -q -p no:cacheprovider
```

- [ ] **B6.2 Implement pinned VALIDATED-to-PAPER_TRADING, PAPER_TRADING-to-SHADOW, and human APPROVED transitions.**

The store method accepts alpha, destination, expected state/version, actor, guard refs, coordinator, optional approval consumption, and timestamp. It accepts no evidence ref. Under lock it reloads the alpha's unique pin, `WINNER_PINNED` outcome, exact family/winner/evaluation, current family verification, new guard snapshot, and consumption when APPROVED. It CAS-increments state/version and appends transition/outbox atomically. PAPER_TRADING and SHADOW remain dry-run observation states and never relax the live-trading safety flags.

Any advanced legacy row without a pin fails `LEGACY_EVIDENCE_PIN_MISSING`. Later unrelated evaluations cannot replace the pinned evaluation. A diagnostic suffix may extend scope history only after winner lock and cannot alter outcome, pin, or alpha.

- [ ] **B6.3 Preserve burn-first failure semantics.**

Test exact kill windows:

- before consume commit: request unburned, no transition;
- after consume commit and before ExperimentStore: request burned, no transition;
- after ExperimentStore commit and before response: request burned, one recoverable transition;
- two workers: at most one consumption and one transition.

There is no compensation transaction. A failed post-consume transition requires a new approval request.

- [ ] **B6.4 Remove latest-evaluation and manager-save authority.**

`execute_promotion` performs non-mutating prechecks, consumes approval, then calls the store transition. It never selects the latest evaluation, calls `AlphaLifecycleManager.advance`, or persists catalog state as authority. The API returns committed state/projection loaded from ExperimentStore.

Run GREEN:

```powershell
rtk python -m pytest tests/governance/test_approval_consumption.py tests/governance/test_approval_store.py tests/governance/test_approval_store_tamper.py tests/alpha/test_promotion_execute.py tests/governance/test_promotion_api_endpoints.py -q -p no:cacheprovider
```

---

### Task 7 (B7): Remove autonomous positive-transition bypasses

**Files:**

- Modify: `services/intelligence/agent_loop.py`
- Modify: `services/intelligence/research_session.py`
- Modify: `services/alpha/factory.py`
- Modify: `services/alpha/lifecycle.py`
- Modify: `services/research/session.py`
- Modify: `tests/intelligence/test_agent_loop.py`
- Modify: `tests/alpha/test_factory.py`
- Create: `tests/governance/test_deferred_family_promotion_boundary.py`

- [ ] **B7.1 Write behavioral RED tests.**

Across multiple searches and rounds before family freeze, PASS/WARN/FAIL only produce inner research evaluations. Every generated candidate remains DRAFT; lifecycle manager spies receive no call. After family closure, only the deferred service may act on the deterministic winner. Non-winners remain DRAFT even when their individual legacy evaluation says PASS.

Run RED:

```powershell
rtk python -m pytest tests/intelligence/test_agent_loop.py tests/alpha/test_factory.py -q -p no:cacheprovider
```

- [ ] **B7.2 Remove direct advancement from research code.**

Delete direct positive `AlphaLifecycleManager` use from agent loop and factory flows. Retain the in-memory manager only for explicitly human-controlled legacy/projection workflows that governance allows, and make it non-authoritative after v4. Research sessions return candidates, evaluations, and family handles; they never return a state mutation side effect.

- [ ] **B7.3 Add an AST governance boundary.**

Scan all `services/intelligence`, `services/research` orchestrators, `services/alpha/factory.py`, maintenance/recovery paths, and promotion code. Forbid unauthorized lifecycle imports, positive `.advance`, catalog `_set_state`, direct `UPDATE alpha_state`, caller-computed promotable booleans, and calls to locked transition primitives. Permit only `DeferredFamilyPromotionService` and approved store methods at exact files/functions.

Run GREEN:

```powershell
rtk python -m pytest tests/intelligence/test_agent_loop.py tests/alpha/test_factory.py tests/governance/test_deferred_family_promotion_boundary.py -q -p no:cacheprovider
rtk python apps/research-console/scripts/run_paper_to_live_governance_check.py
```

---

### Task 8 (B8): Event outbox, projections, recovery, and operational cutover runbook

**Files:**

- Modify: `services/alpha/events.py`
- Modify: `services/monitoring/event_projection.py`
- Modify: `services/monitoring/domain_event_runtime.py`
- Modify: `tools/generate_event_schema_catalog.py`
- Modify: `evidence/event_schema_catalog.json` through the generator only
- Modify: `tests/research/test_experiment_event_outbox.py`
- Modify: `tests/contracts/test_event_schema_registry.py`
- Modify: `tests/governance/test_event_schema_catalog.py`
- Create: `tests/monitoring/test_lifecycle_transition_projection.py`
- Create: `docs/runbooks/lifecycle-authority-v4-cutover.md`

- [ ] **B8.1 Prove state/outcome and outbox atomicity.**

Inject failure before and after every transition/outcome event construction and outbox insert. No state, pin, transition, or outcome may commit without its canonical event; no event may commit without its authority row. Event IDs and payload hashes are deterministic from committed authority identity.

Run RED then GREEN:

```powershell
rtk python -m pytest tests/research/test_experiment_event_outbox.py tests/monitoring/test_lifecycle_transition_projection.py -q -p no:cacheprovider
```

- [ ] **B8.2 Add idempotent projections and recovery.**

Projection keys on transition/outcome identity and can replay at least once without duplicate state. Recovery after commit-before-publish drains the ExperimentStore outbox; it does not reconstruct transitions from `alpha_state`, reuse approval, or rerun promotion logic.

- [ ] **B8.3 Regenerate and verify the event schema catalog.**

```powershell
rtk python tools/generate_event_schema_catalog.py
rtk python tools/generate_event_schema_catalog.py --check
rtk python -m pytest tests/contracts/test_event_schema_registry.py tests/governance/test_event_schema_catalog.py -q -p no:cacheprovider
```

- [ ] **B8.4 Write the operational activation runbook.**

Document backup and restore prerequisites, stopping four writer classes, acquiring exclusive lease, dry verification, activation, marker verification, v4 writer rollout, stale-writer probe, projection replay, rollback-before-marker only, and irreversible post-marker recovery. This runbook authorizes no actual production activation.

---

### Task 9 (B9): Real governed family driver and semantic top-tier credit

**Files:**

- Create: `config/real_governed_family.json` only when its observed-PIT authority paths and content hashes are available
- Create: `tools/run_governed_family.py`
- Create: `tests/integration/test_real_governed_family.py`
- Modify: `quantos/contracts/research.py`
- Modify: `services/research/data_snooping/evidence.py`
- Modify: `services/research/data_snooping/verification.py`
- Modify: `services/research/experiment_registry/lifecycle_schema.py`
- Create: `tests/research/test_data_snooping_lifecycle_projection.py`
- Modify: `services/governance/top_tier_assessment.py`
- Modify: `config/top_tier_requirements.json`
- Modify: `tests/governance/test_top_tier_assessment.py`
- Modify: `tests/governance/test_top_tier_boundary.py`

- [ ] **B9.1 Write the semantic checker negative matrix.**

Reject synthetic catalog, zero-byte database, fixture, demo, manual import, detached envelope, v3-only authority, missing activation/baseline, `NOT_ASSESSABLE`, test replication count, absent publication scan, absent execution lease, missing transition chokepoint, stale code, tampered projection, and any evidence whose family cannot be recomputed.

Run RED:

```powershell
rtk python -m pytest tests/governance/test_top_tier_assessment.py tests/governance/test_top_tier_boundary.py -q -p no:cacheprovider
```

- [ ] **B9.2 Add an acyclic lifecycle-authority wrapper around the frozen family artifact.**

Keep the Phase A `PublishedArtifactEnvelope` and `DataSnoopingEvidenceRef` byte-for-byte frozen. That ref is the input consumed by lifecycle transitions and can never depend on a later pin or outcome. Introduce a separate content-addressed `REAL_GOVERNED_FAMILY_LIFECYCLE_WRAPPER_V1` plus strict `LifecycleAuthorityProjection`. The wrapper is published only after one immutable selection-scope outcome commits, references the exact Phase A envelope hash and publication-projection hash, and carries the v4 activation/baseline/schema/outcome proof. It never changes, replaces, or re-hashes the inner family ref.

`LifecycleAuthorityProjection` is a discriminated union on the immutable scope outcome:

- `WINNER_PINNED`: requires the exact evidence pin, complete family-bound transition-chain prefix through the claimed state, `WINNER_PINNED` outcome, lifecycle/outcome outbox bindings, approval-consumption binding when present, and all terminal heads;
- `CLOSED_WITHOUT_PROMOTION`: requires the close outcome and outbox, the winner's observed state/version and activation baseline, and complete authoritative absence proofs for any family evidence pin or family-created validated-or-later transition.

Both branches embed and verify the exact activation record, complete ordered legacy baseline, lifecycle table/column/index/partial-index/trigger catalog, inner-envelope/publication bindings, and terminal authority heads. The close branch must reject any pin; the winner branch must reject a missing or mismatched pin. A Phase A envelope remains valid only as family statistical evidence and can never be relabeled or inferred as a lifecycle wrapper.

Wrapper publication reuses Task A9's acyclic fail-closed DLP protocol with a distinct wrapper artifact kind and policy binding: canonicalize the lifecycle-wrapper core; scan those exact bytes; embed an ALLOW-only core scan receipt; canonicalize the final wrapper envelope; scan those exact bytes; append an `artifact_publication_receipts` row; then write the content-addressed wrapper and a separately content-addressed publication-receipt projection containing the complete genesis-to-row chain. Scanner deny, error, unavailable state, policy/version mismatch, non-ALLOW receipt, missing sidecar, truncated chain, or wrong exact-byte binding prevents publication and semantic top-tier credit. No actor/audit/approval field bypasses the same secret-safe public projection rules used by the inner artifact.

Write tamper and type-confusion tests for missing baseline rows, altered activation snapshot, missing partial unique index, truncated transition chain, wrong pin/outcome, pin present on the close branch, pin absent on the winner branch, incomplete absence proof, stale trigger catalog, inner artifact relabeled as a wrapper, and wrapper hashes paired with different inner envelope/publication bytes. Add exact-byte scanner tests for core deny/error/unavailable, envelope deny/error/unavailable, policy mismatch, missing publication receipt, missing/truncated sidecar chain, and a sidecar bound to different wrapper bytes; every case must leave no public wrapper or top-tier credit.

Run RED then GREEN:

```powershell
rtk python -m pytest tests/research/test_data_snooping_lifecycle_projection.py -q -p no:cacheprovider
```

- [ ] **B9.3 Implement an official observed-PIT family driver.**

The driver accepts paths/config to an already admitted observed PIT dataset authority and isolated v4 ExperimentStore. It performs the exact authority sequence: issue scope/question/lineage/program/decision/exposure; declare; pre-parse ingest; finish and seal searches; freeze; acquire execution lease; consume/release once; evaluate the complete family; close the family manifest; run the full assessment schedule; export and verify the immutable Phase A family publication and obtain its evidence ref; then either invoke deferred winner transition when the positive predicate holds or close the selection scope without promotion; finally publish and verify the separate lifecycle wrapper for the committed outcome.

The driver cannot download data, relabel synthetic data, fabricate missing lineage, or construct an evidence ref from a manual matrix. A valid `ASSESSED / DOES_NOT_SUPPORT_SUPERIORITY` real run proves the statistical control operated but creates no alpha promotion. A positive real result still remains L2 research-only.

`config/real_governed_family.json` contains no secret or network credential. It binds only repository-relative or explicitly configured local authority paths, observed dataset/universe/calendar/content hashes, authority IDs, policy IDs, cost/benchmark contracts, and expected schema/artifact versions. It is created only after those exact non-zero authorities exist; a template or guessed hash cannot satisfy the semantic checker.

- [ ] **B9.4 Add one real governed family projection to configured evidence.**

Run only after a non-zero admitted observed-PIT handle exists. Persist the official content-addressed envelope/projection and authority projection without committing mutable databases or restricted raw proposal bytes. Verify the artifact from a clean checkout.

Example verification command:

```powershell
rtk python -m tools.run_governed_family verify --config config/real_governed_family.json
```

- [ ] **B9.5 Enable top-tier credit only through semantic recomputation.**

The checker calls the Phase A family verifier for the exact inner evidence ref, then verifies the separate lifecycle wrapper/projection and actual store chokepoints. `VALIDATION.ANTIOVERFIT.GATE` no longer uses `paths_exist`. It may PASS only when at least one `REAL_GOVERNED_FAMILY_V1` `ASSESSED` inner family artifact is bound by a valid `REAL_GOVERNED_FAMILY_LIFECYCLE_WRAPPER_V1` and all v4 controls verify. The family need not support superiority to prove the control capability; only a positive transition requires the superiority predicate.

Run GREEN:

```powershell
rtk python -m pytest tests/integration/test_real_governed_family.py tests/governance/test_top_tier_assessment.py tests/governance/test_top_tier_boundary.py -q -p no:cacheprovider
rtk python scripts/assess_top_tier.py --check
```

---

### Task 10 (B10): Living documentation, full regression, and canonical gate

**Files:**

- Modify: `docs/PROJECT_STATE.md`
- Modify: `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`
- Modify: `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`
- Modify: `docs/engineering/specs/2026-07-17-paper-to-quantos-gap-analysis.md`
- Modify: `docs/runbooks/l2-research-operations.md`
- Modify: `QUANTOS_MASTER_BLUEPRINT.md` only if its maturity statement is stale

- [ ] **B10.1 Update the living truth.**

Document v4 activation and threat boundary, legacy baseline/adoption rules, unique deferred service, five-authority validation transaction, close totality, pinned later transitions, burn-first approval, real-family result, and remaining external/operational gaps. Do not state that APPROVED is live trading or that a statistically positive family guarantees profitability.

- [ ] **B10.2 Run feature regression.**

```powershell
rtk python -m pytest tests/research/test_experiment_lifecycle_activation.py tests/research/test_experiment_lifecycle_writers.py tests/alpha/test_positive_transition_guards.py tests/alpha/test_positive_transition_coordinator.py tests/alpha/test_deferred_family_promotion.py tests/alpha/test_legacy_testing_adoption.py tests/alpha/test_family_promotion_atomicity.py tests/alpha/test_selection_scope_close.py tests/governance/test_approval_consumption.py tests/alpha/test_promotion_execute.py tests/intelligence/test_agent_loop.py tests/governance/test_deferred_family_promotion_boundary.py tests/research/test_experiment_event_outbox.py tests/monitoring/test_lifecycle_transition_projection.py tests/integration/test_real_governed_family.py tests/governance/test_top_tier_assessment.py -q -p no:cacheprovider
```

- [ ] **B10.3 Run broad backend and governance regression.**

```powershell
rtk python apps/research-console/scripts/run_paper_to_live_governance_check.py
rtk python -m pytest tests/contracts tests/research tests/validation tests/alpha tests/intelligence tests/governance tests/monitoring tests/integration -q -p no:cacheprovider
```

- [ ] **B10.4 Run canonical validation.**

```powershell
rtk python scripts/validate.py
```

Report the exact current group count and failures from this run. Feature completion requires every Phase B-owned test and assessment check to pass. Independently stale backup or container evidence remains a separately named external-evidence issue and is not silently refreshed.

## Plan self-review matrix

| Design requirement | Owning task | Required proof |
|---|---|---|
| Exclusive all-or-none v3-to-v4 activation | B1, B2 | writer lease and checkpoint-by-checkpoint reopen tests |
| State versions and append-only legacy baseline | B2 | exact snapshot hash, per-alpha baseline, stale-writer rejection |
| No legacy state-write bypass | B2, B7 | SQL/API/AST ownership scan |
| Shared exact positive family predicate | B3 | every-grid and every-verdict negative matrix |
| Current DSR/BHY and stable external heads | B3 | TOCTOU and lease-drift races |
| DRAFT-to-TESTING only for winner | B4 | winner CAS and non-winner byte identity |
| Honest legacy TESTING adoption only | B4 | baseline-bound TESTING-to-TESTING marker |
| Atomic state/pin/transition/outbox/outcome | B5 | five-write kill matrix |
| Irreversible close with no alpha mutation | B5 | all-state totality and concurrent CAS tests |
| Pinned later positive transitions | B6 | no latest evaluation/new ref and legacy-pin rejection |
| Burn-first at-most-once approval | B6 | consume/commit crash windows and multi-worker race |
| No autonomous positive transitions | B7 | multi-round behavior and AST governance scan |
| Canonical lifecycle/outcome events | B8 | atomic outbox and idempotent projection replay |
| Real observed-PIT governed family | B9 | official driver, semantic artifact and lifecycle verification |
| Semantic top-tier credit | B9 | fixture/synthetic/detached/v3 negative matrix |
| L2 live-trading boundary unchanged | all tasks | safety flags and governance gate |

## Independent review gate

Before Task B1 implementation begins, an independent reviewer must compare this plan against design §§5.2, 5.4, 5.5, 7, 8.3, 10, and 11 and report every P1/P2 omission. Phase B implementation may start only after Phase A is complete and no P1/P2 remains. Actual v4 activation of any non-test authority requires a separate operational approval and the cutover runbook.
