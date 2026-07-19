# QUANTOS — Full Development Program Plan

Authoritative spec: [`QUANTOS_MASTER_BLUEPRINT.md`](../QUANTOS_MASTER_BLUEPRINT.md)
(11 layers, phases 0–14, §1 safety, §8 rejection reasons, §9 quant validation,
§15 Definition of Done).

**Current state (Phase 0 audit, verified):** ~10% built; safety posture strong
and code-enforced. See [`README.md`](../README.md) and [`CLAUDE.md`](../CLAUDE.md).

**Operating principle:** tests-first, smallest safe change, research/paper/live
isolation, and live trading provably impossible without multi-step human
approval — at every phase.

---

## 1. Critical path

```
0.5 → 1 → 2 → 3 → 5 → 6 → 7 → 8 → 9 → 10 → 13 → 14
            └─ 4 (backtest) runs parallel to 2 but is a mandatory feeder into 5
```

The credibility bottleneck is the data→feature→validation chain (2 → 3 → 5):
until real point-in-time data exists, phases 4–7 run on fixtures and are **not
production-ready** (enforced at gates G4–G7).

## 2. Parallel lanes (after the serial roots 0.5 → 1)

| Lane | Phases | Owns |
|---|---|---|
| A — research-science spine | 2 → 3 → (5) | `services/data/**` |
| B — backtest/alpha/portfolio | 4 → 5 → 6 → 7 | `services/research/backtest`, `services/alpha`, `services/portfolio` |
| C — trading/risk/execution | 8 → {9 ‖ 11} → 10 | `services/risk`, `services/trading`, `paper_trading` |
| D — frontend | 12 (mock-first from the start) | `apps/research-console/src` |

Shared hotspot: `services/console/api/main.py` is edited by many
phases — consider decomposing it into per-domain routers in Phase 1.

## 3. Phases

| # | Phase | Effort | Closes |
|---|---|---|---|
| 0.5 | Safety & Governance Baseline | M | reproducibility, fail-closed CI, machine-enforced §1 |
| 1 | Architecture Contracts & Domain Models | L | one canonical `RejectionReason` (§8), `OrderState` (§7), config §1.2 |
| 2 | Data Layer (point-in-time / bitemporal moat) | XL | **Gap 1 — data moat** |
| 3 | Feature Layer (leakage-free) | L | static + dynamic leakage detection |
| 4 | Research OS & Backtest Engine | XL | deterministic backtest, real costs, PnL reconciliation |
| 5 | Validation Gate (anti-overfitting) | L | **Gap 2 — anti-self-deception** (Deflated Sharpe, PBO, purged/embargoed CV) |
| 6 | Alpha Factory | L | governed alpha lifecycle |
| 7 | Portfolio Layer | XL | factor risk model + optimizer, cost-aware |
| 8 | Risk Layer (pre/post-trade) | L | all 20 §8 reasons + kill-switch + enforced limits |
| 9 | Paper Trading | L | real PnL reconciliation (replaces `notional*0.001`) |
| 10 | Execution Boundary | L | broker adapter abstraction; live disabled |
| 11 | AI Agent Layer | XL | **Gap 3 — guardrailed AI research loop** |
| 12 | Frontend Console | XL | wire 25+3 pages to real backends; truthful provenance |
| 13 | Monitoring & Audit | L | tamper-evident audit chain |
| 14 | Deployment Governance | L | layered env config; live impossible in-repo |

## 4. Cross-cutting invariants (every phase, every gate)

- **CT-1 Data integrity** — point-in-time / no-lookahead by construction.
- **CT-2 Anti-overfitting** — net-of-cost metrics; known-overfit MUST fail;
  injected leakage MUST be caught.
- **CT-3 Reproducibility** — content-addressed hashes; versioned datasets/configs.
- **CT-4 Safety & live-isolation** — governance scanner in CI; each phase extends
  `SCAN_DIRS` to cover its new package.
- **CT-5 AI guardrails** — agents never reach live/credentials, never self-approve,
  never bypass validation (negative-security tests written first).

## 5. Milestone gates (human sign-off — blueprint §15)

Each phase ends at a hard gate (G0.5 … G14). Every gate requires: tests-first
passing, governance scanner green over the package allow-list (with a planted
positive control), no live capability enabled, no secrets/broker SDK, and a
changed-files / tests-run / risks / rollback summary. The next phase is not
authorized until sign-off is recorded.

## 6. Adversarial-review hardening (applied to the gates)

These corrections came from a completeness critique and are folded into the
gate acceptance criteria above:

- **G0.5 scanner scope** — assert coverage by *package allow-list* (incl.
  `services/risk`), not `files_scanned>0`; require a planted positive control in
  each scanned tree. *(Implemented in Phase 0.5.)*
- **G4** — backtest must not be signed off on lookahead-contaminated fixtures;
  it must pass against the real Phase-2 point-in-time store with a
  future-knowledge-unreachable test.
- **G5** — Deflated Sharpe must consume a persistent **total trial count**; a
  single-regime strategy and a capacity-failing strategy must NOT pass.
- **G8** — name the single enforced risk chokepoint; add a
  `test_advisory_limit_now_blocks` regression and a kill-switch first-short-circuit
  test.

## 7. Phase 0.5 — first increment (in progress)

Status: **landed** (commits establishing the baseline + the fail-closed
validation harness). Delivered:

1. `.gitignore` + initial git baseline (dependencies/artifacts excluded).
2. `scripts/validate.py` — fail-closed gate (governance scanner + all runnable
   pytest suites), proven to exit non-zero on a planted violation.
3. Governance scanner repaired (repo-root anchored, covers the real
   safety-critical trees, secret-literal credential detection) +
   `tests/governance/` (coverage + positive controls + no-false-positive +
   quarantine checks).
4. Broken `pre_trade_risk` quarantined (preserved for Phase 8).
5. `pyproject.toml` / `requirements*.txt` (pinned), `CLAUDE.md`, and CI.
6. README/docs reconciled to verified reality.

**G0.5 acceptance:** `python scripts/validate.py` green and fail-closed; zero
domain-logic change; human sign-off authorizes **Phase 1**. — DONE.

## 8. Phase 1 — Architecture Contracts & Domain Models (in progress)

Decisions (human-approved):
- **Modeling standard: pydantic v2** for data-bearing domain entities; enums and
  the order state machine remain stdlib. All contracts live in the single
  collision-free `quantos.contracts` namespace.
- **Unify the two `services` trees**: DONE — all backend services now live under
  the repo-root `services/`; `apps/research-console/` is frontend-only; the test
  gate runs from one rootdir.
- **`console_api/main.py` decomposition: deferred.** It is ~3,680 lines of mock
  snapshot code that Phase 2+ will progressively replace with real backends;
  it will be split into per-domain routers incrementally as real implementations
  land, behind its existing contract tests. (Refactoring mock-only code now is
  high-risk, low-ROI.)

Landed so far:
- `quantos/contracts/`: SafetyConfig (§1.2), RejectionReason (§8, 20 reasons) +
  legacy crosswalk, OrderState machine (§7), RiskDecision, reproducibility hash.
- Trading domain entities (§6.6): OrderIntent / Order / Fill / ExecutionReport
  (pydantic, validated, intent-only / no live capability).
- 25 contract tests; full suite (128+ tests) green from the unified rootdir.

All §6 domains landed (pydantic): Data (`data.py` — Instrument, Bar, Tick,
TradingCalendar, CorporateAction, DatasetVersion, DataQualityReport),
Feature (`feature.py`), Research (`research.py` — Factor, Alpha+AlphaState,
Strategy, Experiment, Backtest*, ValidationResult+verdict), Portfolio
(`portfolio.py`), Governance (`governance.py` — AuditEvent hash-chain,
ApprovalRecord, *Version, DeploymentRecord [LIVE requires approval],
IncidentReport). Bitemporal `temporal.py` enforces the CT-1 no-lookahead
invariant. Canonical `events.py` event envelope. JSON Schemas generated under
`schemas/` via `scripts/gen_schemas.py` with a drift-guard test; `proto/`
scaffolded.

**G1 acceptance:** all core entities exist and are typed; invalid states are
rejected at construction; serialization round-trips; 52 contract tests + full
suite green; safety invariants hold (live impossible, AI cannot be live actor,
no-lookahead by construction). — DONE.

## 9. Phase 2 — Data Layer (in progress; closes Gap 1 foundations)

Landed under `services/data/` (pure-Python, deterministic, no real feed):
- **PIT bitemporal store** (`pit_store.py`): `as_of()` returns only records
  knowable at the query time — lookahead cannot be read out.
- **Survivorship-free universe** (`universe.py`): point-in-time interval
  membership; delisted and delisted-then-readded names handled.
- **Dataset versioning** (`versioning.py`): reproducible, order-independent
  content hash -> stable `DatasetVersion`.
- **Quality** (`quality.py`): missing / duplicate / out-of-order / future
  detection -> `DataQualityReport`.
- **Normalization/alignment** (`normalization.py`): validated Bar construction,
  timezone->UTC-epoch alignment, fail-closed future-data rejection, calendar
  alignment.
- **PIT-correct corporate actions** (`corporate_actions.py`): split adjustment
  applied only if knowable by `as_of` (no retroactive leak). Contract fix:
  `CorporateAction` is a forward announcement (knowledge may precede event).
- **Ingestion interface** (`ingestion.py`): `MarketDataSource` Protocol +
  synthetic in-memory source.

Tests: 16 (`tests/data/`) incl. adversarial survivorship + PIT split-leak guards.

Self-review fixes applied: PIT dividend accrual (`cash_dividend_total`),
restatement test, `normalize_bar` guard, ISO-date validation.

Console wiring (G2): the research console's Data Catalog now sources its
content hashes and point_in_time_safe flags from the real data layer
(`services/data/catalog.py`) and advertises `data_backend="PIT_DATA_LAYER_v1"`
— no longer hardcoded. Response shape preserved; frontend untouched.

**G2: DONE.** Acceptance met — bad data detected; dataset versions tracked +
reproducible; time alignment testable; data quality report generated;
PIT / survivorship-free / no-leak proven by tests; console catalog backed by the
real layer. ~23 data tests + full suite green. Data remains synthetic at L2
(no real feed); a real PIT data source is an external dependency for higher
readiness levels. **G2: DONE.**

## 10. Phase 3 — Feature Layer (DONE / G3)

`services/features/` on top of the PIT data layer:
- `calculation.py`: rolling features use only bars at-or-before each event_time;
  a value's knowledge_time = max knowledge_time of its window (PIT). Deterministic.
- `leakage.py`: dynamic detector (perturb all post-target bars; if the value
  changes, it leaked → FAIL) + static lookback guard.
- `registry.py` (versioned + status), `feature_set.py` (rejects unknown features),
  `validation.py` (missing / non-finite / alignment + leakage → FeatureValidationResult).
- 12 tests incl. the key guard: a future-peeking feature MUST fail detect_leakage;
  a past-only feature passes. **G3 met.**

## 11. Phase 4 — Backtest Engine (in progress; increment 1)

`services/research/backtest_engine/`:
- `engine.py`: deterministic, event-driven, single-instrument. Decisions at bar i
  execute at bar i+1 close (next-bar fill, no lookahead). Mark-to-market equity
  reconciles EXACTLY to (mtm_pnl − total_cost) — `reconciliation_residual()` ~0.
- `costs.py`: CostModel (fee + slippage bps); PnL always net-of-cost.
- `metrics.py`: returns / Sharpe / Sortino / max-drawdown / Calmar / vol.
- 7 tests incl. determinism, next-bar-fill, exact PnL reconciliation, accounting.

G4 increment 2: optional sqrt-law market impact (participation-scaled),
`to_backtest_result` (BacktestResult contract, reproducible) + capacity estimate,
and a test running the engine on bars served from the real PITStore as-of view
(review P0-2). **G4 core met.** Deferred refinements: fill-model variants,
date-aware annualization, multi-instrument portfolios, replacing the console's
synthetic backtest path.

## 12. Phase 5 — Validation Gate (DONE / G5) — closes Gap 2

`services/research/validation/`:
- `walk_forward.py`: sequential walk-forward + purged k-fold with embargo.
- `deflated_sharpe.py`: Deflated Sharpe Ratio + expected-max-Sharpe(N trials) +
  min track-record length + PBO (CSCV). DSR decreases with more trials.
- `gate.py`: ValidationGate -> ValidationResult. Leaky / overfit (low DSR or
  high PBO) / single-regime strategies CANNOT pass; high trial counts force
  REQUIRES_HUMAN_REVIEW. Consumes trial_count (review P1-2).
- 12 tests incl. adversarial: overfit FAILS, injected leakage FAILS,
  single-regime FAILS, clean PASSES; DSR monotonic in trials; PBO discriminates
  genuine vs overfit. **G5 met.**

End-to-end wiring (DONE): `pipeline.py run_research_validation()` feeds REAL
walk-forward OOS + DSR + PBO (over candidate trials) + leakage into the gate —
a genuine buy-and-hold passes, a block-diagonal overfit set is not promotable, a
leak fails. The console `/validation` snapshot's leakage gate is now the real
detector (`validation_backend="REAL_LEAKAGE_DETECTOR_v1"`), replacing the
`idx % 5 != 2` modulo hack.

Independent adversarial review (research spine) was run and its confirmed P0/P1
findings fixed: leakage-detector bypasses (perturb all fields incl. volume/
knowledge_time, multi-instrument, PIT-dedup), gate input-validation + WARN
not auto-promotable, DSR var_sr required + guards, metrics max-drawdown clamp /
Sortino / Calmar.

## 13. Phase 6 — Alpha Factory (DONE) — the profit center

Built from a 4-lens design workshop + adversarial critique (`docs/ALPHA_FACTORY_SPEC.md`);
all 3 critique P0s folded in. `services/alpha/` + `quantos/contracts/alpha.py`:
- catalog-wide multiple testing: `run_research_validation` gains n_trials/var_sr
  override; `AlphaCatalog` owns a monotonic dedup trial counter + population
  var_sr, fed TOGETHER to Deflated Sharpe (P0-1/2). + Benjamini-Hochberg FDR.
- series-correct ICIR (not factor_engine's broken single-cross-section figure) +
  thin-coverage fail-closed gate (P1-2).
- dollar-neutral net-of-cost panel backtest (fractional weights, per-name ADV
  impact, reconciles) (P0-4).
- evaluator: PIT cross-sections -> IC series -> panel backtest -> catalog DSR ->
  AlphaEvaluation. correlation + complete-linkage clustering + Gram-Schmidt
  orthogonalization (reward only incremental edge). crowding (fail-closed) +
  capacity (impact-budget). robust diversity-aware ranker (MAD-z + z-clip +
  greedy MMR). decay (AR(1) half-life, debounced) + retirement.
- lifecycle state machine + manager: APPROVED is HUMAN-ONLY (Actor.kind verified;
  agents raise UnauthorizedPromotion), human ApprovalRecord + paper-consistency
  gated, every transition hash-chain audited; the manager never mints an approval
  (P0-3). Inert proposal-only generator. AST import-boundary test: services/alpha
  imports nothing from trading/portfolio/execution or broker SDKs (P1-6).
- 80 alpha tests; the automated factory can NEVER auto-approve. **Phase 6 done.**

Independent adversarial review (12 agents) was RUN and all confirmed P0/P1 fixed
(3 batches): var_sr fail-closed (<2 population -> review), config_hash collision
removed, approver-identity + trial-count-at-approval checks, BH-FDR now ENFORCED
in guard_to_validated, retirement sweep() driver; evaluator computes marginal_ir/
capacity_adj_sharpe + records IC p-value, ranker accepts AlphaEvaluation objects,
decay mean-retention debounce (closes sawtooth) + anti-persistent flag, panel
dup/non-finite guards, rank_stability inf/nan guard. 96 alpha tests.

## 14. Phase 7 — Portfolio Layer (core done)

`services/portfolio/` (pure-Python, deterministic, over the small N of ranked
alpha sleeves; no live surface — import-boundary tested):
- risk_model.py: sample covariance over the common window (fail-closed below
  min_overlap), shrinkage toward the diagonal, portfolio variance/vol, component
  + percent risk contributions.
- optimizer.py: risk_parity_weights (equal-risk-contribution, inversion-free) +
  inverse_vol_weights; cap_weights (concentration limit); cost_aware_rebalance
  (no-trade band).
- construction.py: build_portfolio (covariance -> shrink -> risk-parity -> cap ->
  cost-aware rebalance -> RebalancePlan of per-alpha budget weights + turnover) +
  risk_budget_report. PortfolioConfig holds the thresholds.
- 18 tests. Deferred: explicit multi-factor risk model, attribution wiring,
  console surfacing.

## 15. Phase 8 — Risk Layer / Pre-Trade Gatekeeper (core done)

Built from a 4-lens design workshop + adversarial critique (`docs/RISK_LAYER_SPEC.md`);
all 4 critique P0s folded in. New `services/risk/pre_trade/` (one-directional dep:
portfolio/alpha → risk; the quarantined `services/trading/pre_trade_risk` stays):
- config.RiskLimits (frozen; no "unlimited" sentinel; empty allow-lists reject-all);
  context.* with `allow_inf_nan=False` (NaN/inf rejected at construction);
  exposures computed from positions.
- kill_switch: latched, fail-closed `is_active` (corrupt state ⇒ active), any-actor
  arm, HUMAN-only incident-bound reset via a matching ApprovalRecord.
- checks: 23 pure check_* + CHECK_REGISTRY; two-stage (hard gates + post-trade SIGNED
  limit checks); every reject → one of the 20 canonical §8 RejectionReason (all 20
  reachable, capstone-tested).
- gate.PreTradeRiskGate.evaluate: assert_research_safe at construction; binds one
  safety config + authoritative kill-switch state; runs ALL checks collecting ALL
  reasons (deduped, no masking); per-check + outer try/except ⇒ UNKNOWN_RISK_ERROR
  (never approve on error); emits only APPROVED/REJECTED; audit-then-or-reject.
  approval_ref() yields a usable ref ONLY for APPROVED, so the dry-run boundary fails
  a rejected one closed (verified end-to-end). + hash-chained audit, order_bridge.
- ~30 tests (fail-closed, collect-all, boundaries, sell-flip, post-trade aggregate,
  kill-switch latch/human-only-reset/corrupt-fails-active, no-intent-mutation,
  all-20-reachable, P0-1). New contract quantos/contracts/killswitch.py.
Independent adversarial code review of the built gate was RUN; limit-math + overall-
safety dimensions found zero issues; confirmed P0 (LIMIT order with no reference_price
approved) fixed + arm-on-corrupt guard + intent-bound verifiable ref. Deferred:
boundary provenance verification → Phase 10; HMAC-signed approvals + TTL → Phase 11.

## 16. Phase 9 — Paper Trading PnL reconciliation (core done)

`services/research/paper_trading/`:
- reconciliation.py: real signed average-cost accounting over fills (longs/shorts/
  partial closes/flips) + mark-to-market; PnL = realized + unrealized − costs per
  instrument; aggregate reconciles EXACTLY (equity−initial == gross − costs).
- pnl.PnLAttributor.attribute now reports REAL net PnL by alpha/sector — replaces
  the placeholder notional*0.001 (closes Phase-0 defect P0-3).
- 7 tests incl. the reconciliation identity + "attribution is real PnL not the old
  2.2 placeholder". Deferred: backtest-vs-paper consistency wiring (perf_gap →
  decay/lifecycle), consuming Phase-8 approved RebalancePlan/refs.

## 17. Phase 10 — Execution Boundary (core done) — DRY-RUN ONLY

`services/execution/` (no broker SDK, no submission; import-boundary tested):
- intent_builder.build_intents: target vs current signed positions -> delta
  OrderIntents (pure, deterministic; strategy/translation, not permission).
- boundary.ExecutionBoundary.admit: fail-closed last gate — admits ONLY an
  APPROVED RiskDecision whose ref it INDEPENDENTLY re-verifies via gate.verify_ref
  (closes the Phase-8 deferred boundary-provenance gap; forged/empty/mismatched
  ref denied) + idempotency (duplicate fingerprint denied).
- 9 tests incl. an end-to-end capstone: target book -> build_intents -> risk gate
  -> issue_ref -> boundary admission. The full portfolio→risk→execution chain composes.
- Deferred: CTP/FIX protocol-mapping boundary (the existing dry_run_adapters already
  covers a real, fail-closed dry-run path); HMAC-signed refs -> Phase 11.

## 18. Phase 11 — AI Agent Layer (done) — guardrailed research loop

`services/intelligence/`: explicit machine-checked capability policy (a non-human
actor may propose/evaluate/advance-to-PAPER/retire/request-approval and NOTHING
else; APPROVE + RESET_KILL_SWITCH human-only; LIVE_TRADE + ACCESS_CREDENTIALS
forbidden for everyone). ResearchAgentLoop runs as a non-human actor, hands
PAPER_TRADING candidates to a human review queue, isolates per-candidate failures
(fail-closed). Independently reviewed: runtime guardrails sound (no escape);
fixed loop exception-isolation + transitive import-boundary test; threat model
(runtime vs trusted-code) documented in CLAUDE.md.

## 19. Phase 13 — Monitoring + Audit (done)

`services/monitoring/`: GlobalAuditLog (single hash-chained aggregator of all
components' audit events; ingest() preserves source provenance; tamper breaks the
chain) + build_snapshot -> MonitoringSnapshot (audit-chain integrity = health;
kill-switch status; alpha state counts; reject count; anomaly flags).

## 20. Phase 14 — Deployment Governance (done)

`services/deployment/`: evaluate_deployment chokepoint — DEV/TEST free; PAPER needs
a verified human approval; STAGING/LIVE require L4 readiness AND live SafetyConfig
flags (both OFF/below at L2), so live deployment is structurally impossible without
a deliberate multi-step human-approved escalation. Fail-closed.

## 21. Phase 12 — Frontend ↔ backend wiring + contract guard (done)

The 25-page React console already had a full API client (`research-console-api.ts`,
25 fetchers) and per-page `apiEnabled` fetch + mock-fallback logic, and the
read-only `console_api` already served all 25 endpoints from REAL implementations
(factor_engine IC/Sharpe, paper_trading PnL, research-risk gate, leakage detector).
The wiring existed; the gap was **drift safety**. Because the client cast responses
with `as TPayload` (no runtime check) and pages fall back to mock on error, a 200
with a drifted/forged shape would render as `undefined` while still claiming
"connected". Closed on both sides:
- **Gate-side:** `tests/research/test_console_api_contract.py` derives the required
  top-level keys from the frontend `*Payload` types and asserts every endpoint
  returns them + the read-only markers, plus a route↔contract bijection guard and
  a query-echo (`selected_alpha_id`) check. Backend drift now turns the gate RED.
- **Client-side:** `assertConsoleSnapshot` validates the `BACKEND_COMPUTED_SNAPSHOT`
  / `read_only` markers at runtime so a non-snapshot 200 surfaces as a visible
  error + honest mock fallback. First frontend tests added (vitest, 5) — the
  previously configured-but-empty runner is now active.
Run story documented in CLAUDE.md (uvicorn :8400 + `VITE_MOCK_MODE=false`).

**Backend phases 0.5–14 COMPLETE and Phase 12 wiring contract-guarded. 433 Python
tests + 5 frontend tests; governance scanner clean across 13 packages; no live
surface (transitively verified). Five independent adversarial reviews run + fixed
(research spine, alpha factory, risk gate, agent layer, monitoring/deployment).**
