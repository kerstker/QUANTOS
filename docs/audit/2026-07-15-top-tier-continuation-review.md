# Top-Tier Continuation Review - 2026-07-15

## Decision

QUANTOS remains `NOT_TOP_TIER` and `L2_DRY_RUN_ONLY`. The repository has a
substantial, well-tested institutional research core, but it does not yet have
the external trust domains, operational history, independent infrastructure,
or production execution evidence required for a top-tier institutional AI
quant system.

The 2026-07-07 manual audit is a historical snapshot. Current maturity is
derived only from:

- `config/top_tier_requirements.json`;
- `services/governance/top_tier_assessment.py`;
- `evidence/top_tier_assessment.json`; and
- `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`.

The last two generated artifacts are currently stale. They must not be used to
claim the old `15/22 PASS` result until regeneration succeeds.

## Progress Continued In This Wave

### Artifact admission V4

- Policy registry, receipt-authority registry, and decisions now share one
  append-only SQLite authority sequence.
- `ALLOW` binds both exact registry heads and is rechecked with transactional
  compare-and-swap when the decision is committed.
- Revocation after evaluation but before persistence can no longer leave a
  stale retained `ALLOW`.
- This remains M3/PARTIAL: external bundles, receipts, checkpoints,
  revocation proof, WORM sealing, cross-host reproducibility, and independent
  deployment are not proven.

### Strong identity application authorization

- Token roles are no longer final application authorization.
- Exact `issuer|subject` grants bind HUMAN/WORKLOAD class, enabled/revoked
  state, granted roles, token epoch, and access-review expiry.
- Registry state and authorization decisions share an append-only SQLite
  authority with CAS; the promotion API fails closed when it is absent.
- This advances the local control to M3/PARTIAL. External IdP operation,
  trusted mTLS deployment, managed key custody, joiner/mover/leaver operation,
  independent access reviews, and external workload attestation remain open.

### L2 backup source contract V2

- The backup manifest carries an exact v2 source contract and canonical
  SHA-256 digest.
- Ten required and six optional authority sources are declared. Restore-history,
  approval, and identity-authorization authorities are included explicitly.
- Restore verifies the caller-expected path, role, requiredness, exact
  required/optional partition, artifact digests, and SQLite integrity.
- Count fields remain compatibility fields and no longer grant admission.

### Restore false-green closure

- `OPS.RESTORE.DRILL` no longer consumes the legacy generic
  `evidence/restore_drill_latest.json` artifact.
- Backup and restore assessment now consume only
  `evidence/l2_backup_latest.json` and the exact configured v2 source contract.
- The checker requires L2 authority-state mode, encryption, manifest, source
  contract, isolated restore, SQLite integrity, RPO/RTO thresholds, exact
  source partition, and external destination proof.
- A single exact restore is capped at M3. M4 remains open until append-only
  recurring history, dynamic freshness, retained failures, and external
  operational evidence exist.
- The generic restore tool now defaults to
  `evidence/generic_restore_drill_latest.json`, which is explicitly
  non-authoritative for the L2 restore red line.

### Append-only restore history and external checkpoint verification

- Each scheduled attempt now commits `STARTED` before backup work and one
  immutable `OUTCOME` after success, ordinary failure, or timeout. Hard process
  interruption leaves an open attempt that dynamic health treats as overdue.
- WAL/FULL durability, strict schema/FK/triggers, a contiguous SHA-256 chain,
  global head CAS, single-active-attempt enforcement, exact replay idempotency,
  and future/rollback clock rejection are tested.
- Compatibility status/evidence mirrors update only after the authority event;
  failures replace an old latest PASS and store no exception message, path, or
  passphrase. Existing corrupt history is opened verify-only and left unchanged.
- The scheduler watchdog now merges task metadata with the same dynamic
  history policy. Exit code zero cannot hide missing, corrupt, failed, stale,
  overdue, or cadence-breached restore history.
- M4 uses a dedicated cadence checker and an independent verify-only Ed25519
  checkpoint contract. The checkpoint must bind the exact current local head,
  trusted issuer/key, external object version, freshness, and retention.
- The repository trust registry is deliberately empty, so no checkpoint and no
  M4 claim can be manufactured locally.

### Selection-bearing global trial ledger

- The previous `ALPHA.TRIAL.GLOBAL_LEDGER` assessment was a false-positive
  surface: a `paths_exist` check granted M4 while several real search drivers
  used disposable in-memory catalogs. Failed candidates could disappear after
  selection and understate the DSR/BH/BHY multiple-testing population.
- The experiment registry now retains append-only `STARTED`/`FAILED`/
  `COMPLETED` search-attempt events and `REGISTERED` candidates. Candidate
  provenance and the global `trials` row commit in one `BEGIN IMMEDIATE`
  transaction before scoring. Hash chains, schema versioning, lifecycle
  validation, candidate-to-trial consistency, and database-level no-update/
  no-delete triggers fail closed.
- The official guided/evolutionary/GP/MCTS composition, all five alpha-factory
  tools, and `crypto_alpha_search` now require durable registration. The latter
  registers its complete candidate pool before full-sample scoring, walk-forward
  selection, ranking, or top-15 presentation.
- The seven legacy direct-loop tools are now migrated to the same durable
  registered-candidate composition: `crypto_alpha_research`,
  `crypto_binance_research`, `crypto_funding_research`,
  `crypto_history_research`, `crypto_smallcap_research`,
  `crypto_volume_research`, and `fx_research`. Each complete evaluable pool is
  registered before backtest scoring, OOS selection, ranking, or presentation,
  and each dataset version binds the bars, panel, funding, universe, or search
  inputs actually consumed.
- A repository-wide AST governance lock now rejects any tool that directly
  imports or constructs `ResearchAgentLoop`, `GuidedSearchLoop`,
  `EvolutionarySearchLoop`, or `MCTSAlphaSearch`; rejects production
  `ephemeral=True`; and requires complete persistent provenance on every
  supported search composition call. The current scan finds zero direct search
  engine constructions under `tools/`.
- Cross-dataset re-evaluation uses a fresh evaluation catalog loaded with global
  trials and historical Sharpe/p-value context. It appends a new immutable
  evaluation artifact without regressing an existing alpha lifecycle row.
- Recorder failures are batch-fatal. If recording a terminal `FAILED` event also
  fails, the original scorer/runtime exception and traceback remain primary and
  the terminal error is attached as a note.
- Factory dataset versions are now stable hashes of the exact panel instruments,
  event times, matrices, and declared source versions rather than static labels.
- The top-tier requirement now uses a semantic checker. Current wiring earns
  M3/PARTIAL only; M4 also requires a versioned two-session history, a matching
  live SQLite ledger, recomputable evaluation hashes, schema version, and all
  append-only guards.
- The local P2 ledger hardening is now implemented: stale `STARTED` attempts are
  recovered only when a caller explicitly supplies a timeout; begin/register/
  finish/recover all reject cross-search global clock rollback; schema migration
  is one fault-injectable transaction; and six concurrent connections with
  1,200 candidates preserve the hash chains, trial count, and terminal states.
- `tools/search_trial_ledger_history.py` can append or verify the exact M4 history
  contract using a read-only stable SQLite snapshot, recomputed evaluation
  hashes, monotonic records, an output lock, and atomic replacement. It has not
  generated the production evidence: two real completed sessions still have to
  be operated and retained, so the maturity claim remains M3/PARTIAL.
- Research-runner kinds now carry explicit `SELECTION_BEARING` or
  `DIAGNOSTIC_ONLY` policy and canonical result provenance. The run registry is
  deliberately marked a non-authoritative mirror: without an exact search ID,
  dataset version, and ledger-head binding, `selection_eligible` remains false
  even for a completed factory run. Remaining local ledger work is evaluation/
  audit outbox recovery, heartbeat/recovery-reason semantics, and longer-running
  concurrency history; SQLite remains a single-node serialized authority.

## Current Verification

Focused verification completed in this wave:

- L2 restore history/checkpoint/assessment/backup focused group: `144 passed`.
- Console assessment composition and API contracts: `30 passed`.
- Strong identity and promotion tests: `68 passed`.
- Top-tier assessor and boundary tests: `63 passed`.
- Supply-chain focused tests: `138 passed`, with one tracked-evidence drift
  failure.
- Selection-trial ledger merged focused group: `216 passed, 1 skipped`.
- Post-migration grouped verification: alpha/binance/history `30 passed`;
  funding/volume `53 passed`; smallcap/FX `46 passed`; registered-batch
  re-evaluation `133 passed`; AST governance lock `2 passed`.
- The four migration-contract and governance-lock files were subsequently
  rerun together: `30 passed`.
- Final merged search-ledger, migration, runner, history-tool, semantic-checker,
  and console-contract regression: `247 passed, 1 skipped`.
- Runner/history/auth/console merge: `67 passed`; post-timeout and precise
  dynamic-import-policy regression: `40 passed, 1 deselected`.
- Ruff on the changed Python scope: no issues.

The full unified gate completed all suites and reported:

- main backend: `2631 passed, 1 skipped, 2 failed`;
- the failures were the service import graph and supply-chain tracked checks;
  the former exposed two shifted, already-approved dynamic import callsites.
  Their exact line permissions were updated without adding an import permission,
  leaving only the generated graph artifact stale;
- live/trading/tools: `1024 passed`;
- frontend: `68 passed`, typecheck passed, production build passed;
- governance scanner: `319 files, 0 violations`.

The 18-stage gate has four failed stages, but only three independent artifact
drifts: service import graph, supply-chain baseline, and top-tier
assessment/matrix. The main pytest stage repeats the first two drift failures.
No new implementation regression was observed.

This unified gate includes the final seven-tool migration, ledger hardening,
runner provenance, and history collector. The precise import-policy line update
and restoration of the SQLite lock timeout to five seconds were applied after
the full run and passed their focused regression; a final green unified gate
still requires evidence regeneration and one more run.

Evidence regeneration is currently blocked by the execution sandbox. Running
the repository generators outside the sandbox was rejected because the
automatic approval service failed internally. The JSON artifacts were not
hand-edited or regenerated through an indirect path.

## Dimension Review And Remaining Additions

| Dimension | Current strength | Missing for top-tier institutional status |
|---|---|---|
| Safety and readiness | L2 pins, fail-closed gates, no broker SDK or credentials, zero governance violations | Independent process trust domains, external policy authorities, reviewed post-L2 activation procedure |
| Data moat | PIT stores, quality admission, lineage, survivorship controls, real public crypto/FX/macro data | Contracted tick/order-book/vendor data, off-host immutable raw archive, long-running data SLO evidence, broader PIT fundamentals and corporate-action operations |
| Alpha research | Real factor/backtest/search/GP/OOS/forward-track engines and honest negative findings | Durable full alpha genealogy, larger independent forward sample, regime/capacity evidence across more universes, externally reviewed research reproducibility |
| Validation and model risk | CPCV, purged walk-forward, DSR, leakage controls, model-risk artifacts and history | Independent model validation function, external reviewer identity, production model inventory/change control, continuous red-team and challenger operations |
| Portfolio construction | Constrained optimization, factor risk, sleeves, crowding, impact/capacity diagnostics | Venue-calibrated impact and liquidity, firm-wide cross-book constraints, funding/margin/collateral model, independent risk sign-off on calibrated limits |
| Risk management | Fail-closed research/pre-trade gates, persistent kill switch, recovery drill | Independent risk process/service, multi-account firm risk, market/credit/liquidity limits calibrated from operational data, externally monitored recovery controls |
| Execution | Reconciled dry-run pipeline, realistic costs, adapter boundary structurally rejects live use | Institutional OMS/EMS, venue-certified adapters, real order-state reconciliation, TCA from real fills, HA and disaster recovery; all are post-L2 and require explicit approval |
| AI agents | Guardrailed research loop, tool/runtime governance, human-only promotion path | Externally attested workload identity, out-of-process policy enforcement, isolated tool workers, independent audit review, production model/provider operations |
| Governance and security | Local M3 identity and artifact-admission authorities, multi-party approval, append-only decisions | External IdP/mTLS/key custody, IAM lifecycle and reviews, external Sigstore/receipt/revocation authorities, WORM seals and independent deployment |
| Platform and eventing | Acyclic service graph, durable single-node event/outbox/projection stack, container evidence | Replicated event platform, cross-host membership and consensus, HA stores, production observability/SLOs, immutable archival and alert-delivery SLA |
| Reliability operations | Exact encrypted L2 backup/restore implementation and scheduler watchdog | Real external backup batch, managed secret delivery/rotation, recurring append-only restore history, dynamic freshness, retained failure evidence, WORM/object-lock receipts |
| Research console | Typed 30-page workflow, real read-only API surfaces, PKCE client, 68 tests | Browser E2E against deployed services, accessibility and visual regression, production auth/telemetry/error budgets, operator workflow evidence |

## Ordered Work Program

1. Regenerate the service graph, strong-identity baseline, supply-chain
   baseline/SBOM, and top-tier assessment/matrix; require the unified gate to
   pass before publishing new maturity counts.
2. Operate two real completed research sessions and retain their versioned
   trial-ledger history with the new collector. Do not manufacture the M4
   artifact from fixtures. Then add evaluation/audit outbox recovery, heartbeat
   and explicit timeout reasons, and longer-running shared-ledger soak evidence.
3. Operate the append-only L2 restore-history authority for the configured
   30-day window and provision an independent signed checkpoint authority.
4. Provision and retain real external L2 backup, restore, heartbeat, and
   object-lock/WORM evidence through operator-owned infrastructure.
5. Complete external strong-identity operations: IdP, trusted mTLS, managed
   keys, lifecycle feed, independent access reviews, and HUMAN/WORKLOAD
   attestation.
6. Complete external supply-chain operations: real signed bundles and receipts,
   independent checkpoint/revocation sources, external sealing, cross-host
   reproducibility, and independently deployed admission.
7. Expand the data moat and forward evidence before adding more model
   complexity: contracted microstructure data, immutable raw archive, longer
   forward tracks, and calibrated liquidity/capacity history.
8. Move single-node event, state, risk, and agent authorities into independent,
   replicated trust domains with measurable SLOs.
9. Keep OMS/EMS, venue adapters, credentials, and any real order submission
   strictly post-L2. They remain prohibited until the external governance and
   process-isolation prerequisites are independently approved.

## Safety Statement

`LIVE_TRADING_ENABLED=false`, `REAL_BROKER_ENABLED=false`,
`ALLOW_ORDER_SUBMISSION=false`, and `ALLOW_AI_DIRECT_TRADING=false` remain
unchanged. Nothing in this wave authorizes live trading, broker connectivity,
credentials, or AI-directed order placement.
