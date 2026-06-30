# QUANTOS — Agent Operating Guide

QUANTOS is a **safety-critical AI quantitative trading research platform**.
Authoritative spec: [`QUANTOS_MASTER_BLUEPRINT.md`](QUANTOS_MASTER_BLUEPRINT.md)
(11 layers, phases 0–14). Program plan:
[`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`](docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md).

Readiness: **L2_DRY_RUN_ONLY** — research + dry-run simulation only. No live
trading, no broker connectivity, no real credentials.

## Safety boundary (non-negotiable — blueprint §1)

- `LIVE_TRADING_ENABLED=false`, `REAL_BROKER_ENABLED=false`,
  `ALLOW_ORDER_SUBMISSION=false`, `ALLOW_AI_DIRECT_TRADING=false`,
  `REQUIRE_HUMAN_APPROVAL=true`. Live trading must remain impossible without
  explicit multi-step human approval.
- Never commit secrets / broker credentials; never import a broker/CTP/FIX SDK;
  never bypass or weaken risk gates; never use future data / introduce
  lookahead; AI agents never place live orders or touch credentials.
- Keep research / paper / live strictly isolated; strategy (intent) separate
  from risk (permission); broker logic behind adapters.
- Tests-first; smallest safe change; never hide or delete failing tests.

These invariants are machine-enforced by the governance scanner (below) and the
tests in `tests/governance/`.

**Threat model (two layers).** The agent guardrails (`services/intelligence`),
the alpha lifecycle state machine, and the pre-trade risk gate are RUNTIME
controls over the *actor* and *evidence* passed in: a non-human actor cannot reach
APPROVED, reset the kill-switch, trade live, or touch credentials. They do NOT —
and in a single process cannot — defend against TRUSTED CODE that constructs a
`HUMAN` `Actor`, calls `AlphaLifecycleManager`/`KillSwitch`/`catalog._set_state`
directly, or otherwise bypasses the chokepoint. That build-time surface is
governed by code review, the governance scanner, and (Phase 14) deployment/process
isolation. The autonomous agent path holds only a non-human actor and reaches the
state machine/gate only through the hardened `ResearchAgentLoop`; direct manager/
catalog access is for human-controlled workflows and is not an agent API.

## What actually exists (verified 2026-06-29 by full-system audit)

Authoritative reality map + completion roadmap:
[`docs/superpowers/specs/2026-06-29-system-audit-and-completion-roadmap.md`](docs/superpowers/specs/2026-06-29-system-audit-and-completion-roadmap.md).
**The backend is substantially built and tested (~1047 tests pass). The console-wiring
gap is now largely closed: the four ALPHA-pipeline breaks are all wired + tested — 断点1
关卡证据回喂 evaluator (real `detect_leakage`/regime/PBO evidence through every search loop),
断点2 前向 paper 追踪持久层, 断点3 console 真组合 (optimizer + real returns), 断点4 衰退/退役调度器.
Most console pages now serve real data; remaining synthetic surfaces are genuinely-missing
subsystems (kept honest), not unwired engines.**

Real, tested backend engines (`*_TESTED` in the audit):
- `services/research/factor_engine/` — real IC/ICIR/Sharpe math + hardened AST
  factor-expression compiler. `services/research/backtest_engine/` — single +
  event-driven backtests with exact PnL-reconciliation identity.
- `services/research/validation/` — Deflated-Sharpe, combinatorial purged CV,
  purged/embargoed walk-forward gate. `services/research/oos_validation.py` —
  no-peek selection OOS.
- `services/research/{alpha_factory,alpha_expression,cs_genome,gp_tree,factor_set,
  session}.py` — real evolutionary/GP search, cross-sectional DSL, synergistic
  de-redundant set selection, reproducible orchestrator.
- `services/research/paper_trading/domain/pnl.py` — **real reconciled PnL
  attribution** (realized+unrealized−costs, `reconciliation.py`/`consistency.py`).
  No longer the `notional*0.001` placeholder this file used to claim.
- `services/research/{run_registry,research_runner,experiment_registry}/` —
  durable sqlite run store + the guarded mutating runner + immutable experiment
  artifacts/DSR trial-count persistence.
- `services/research/paper_trading/{store,advance,health}.py` — 断点2 forward paper-track
  persistence (append-only `data/cache/paper_track.db` of held-out OOS paper P&L/IC, honest
  train/paper split). `services/research/leakage_evidence.py` + `services/intelligence/
  evidence.py` — 断点1 leakage evidence SOURCE (wrap a SIGNAL/VOLUME op or GP tree as a real
  `detect_leakage` ComputeFn; providers expose `leakage_ok(defn)`; the search loops feed it as
  `evidence=` to the evaluator gate). `services/alpha/maintenance.py` — 断点4 non-human
  degradation/retirement sweep (the missing caller for `degradation_monitor`). All tested.
- `services/research/ml/{feature_panel,stacker,walk_forward_stack}.py` — the first ML layer: a DETERMINISTIC
  learned signal-combination (stacking) over the factor zoo (numpy + scikit-learn, PINNED + used deterministically:
  threads=1, `random_state=0`, Ridge cholesky; same-input→bit-identical test). GBM + Ridge, trained purged+embargo,
  judged no-peek OOS. Driver `tools/ml_stack_research.py`. Honest finding: NO tradable edge (crypto combination
  hurts/GBM overfits; FX +0.21 is gross + contaminated-universe) — bottleneck is data/signal-class, not modeling.
  numpy/sklearn are non-network so `services/` no-network-import is still machine-enforced.
- `services/risk/research_risk/` — 9-engine fail-closed gate, **now tested**
  (`tests/risk/test_research_risk_gate.py`). `services/risk/pre_trade/` — real
  fail-closed pre-trade gate + latched `KillSwitch` (the successor to the
  quarantined `trading/pre_trade_risk`); tested but **not yet surfaced** in any
  endpoint/page.
- `services/alpha/` (26 modules / 185 tests) — catalog, lifecycle SM, evaluator,
  BH-FDR ranker, population_optimizer, decay, crowding/capacity, correlation/
  cluster, orthogonalize, zoo, alpha_graph, lineage, degradation_monitor.
- `services/data/` (89 tests) — PIT store, survivorship-free universe, versioning,
  quality, normalization, PIT corporate actions, catalog.
- `services/portfolio/` (57 tests) — constrained optimizer (feasibility-checked),
  PSD-checked factor risk model, construction, attribution, signal_adapter.
- `services/intelligence/` (59 tests) — guardrailed non-human `ResearchAgentLoop`
  + guided/evolutionary/GP/MCTS search strategies + bandit persistence.
- `services/{features,execution,monitoring,deployment}/` — leakage detector +
  feature registry; execution boundary + intent builder; hash-chained audit log +
  health; fail-closed deployment/version-pin gate. All tested.
- `services/trading/dry_run_adapters/` — real, tested, fail-closed dry-run
  adapters (no broker SDK).
- `services/execution/dry_run_pipeline.py` + `dry_run_store.py` — **端到端干跑流水线**
  (the successor that turns the pre-trade gate from "tested dead code" into a RUNTIME
  chokepoint). `run_dry_run_pipeline`: 权重→目标手数→`build_intents`→逐意图过
  `PreTradeRiskGate.evaluate` + kill-switch (**fail-closed**, latched→全拒)→干跑适配器
  受理(纸面、**绝不真发**)→`PaperFill`→`reconcile` 平均成本 PnL(恒等式残差≈0)→
  `GlobalAuditLog` 哈希链→可复现 `DryRunRun` 落 `data/cache/dry_run.db`. Deterministic core
  (墙钟/seed only at the driver boundary). Driver `tools/dry_run_carry.py` runs the carry
  book end-to-end (latest COMPOSITE legs × vol-target leverage). 11 tests + governance
  (no broker/network import; orchestrator NOT in research_runner). 实盘 = 同接口换真券商
  适配器 + 人工批准 = 显式非目标.
- `services/research/console_api/main.py` — FastAPI **read-only** API (~31 GET
  endpoints), now **mostly REAL** (distinct provenance markers; fail-closed to
  `SYNTHETIC_DETERMINISTIC` only when an artifact is absent, e.g. CI). REAL: `research-runs`/
  `research-run` (run_registry sqlite), `crypto-live` (CoinGecko offline snapshot),
  `fx-research` (ECB/Frankfurter snapshots + tradable gauntlet), `lifecycle-monitor`
  (experiment_registry artifacts + real paper-track `live_ic`/recommendation), `alpha-zoo`
  (12-dim real eval + time/活体征 dims), `backtest-results` (run_registry by role),
  `data-catalog` (real PIT datasets), `safety-primitives` (real KillSwitch/gate), `portfolio`
  (real optimizer/risk-parity), risk-dashboard/validation (real engines). The residual
  synthetic root for the few remaining PARTIAL pages is `_factor_values`/`_forward_returns`
  (`math.sin/cos`); contract-guarded by `tests/research/test_console_api_contract.py`.
- `apps/research-console/src/` — React 18 + Vite 6 + TS (strict) + Tailwind; **37
  pages, route-lazy code-split** (+ FX Research). The audit doc's original split
  (7 REAL / 6 PARTIAL / 17 MOCK / 6 NONE) predates this session — REAL has grown
  substantially after the 4-break + paper-track + FX wiring (CryptoLive, FxResearch,
  ResearchRuns, LifecycleMonitor, BacktestResults, DataCatalog, Portfolio, AlphaZoo, …).

Quarantined / broken:
- `services/trading/pre_trade_risk/main.py` — **NON-FUNCTIONAL** (missing domain
  modules + undefined-name bug). Superseded by `services/risk/pre_trade`. Do not
  wire; delete or rebuild in Phase 8.

Shared contracts:
- `quantos/contracts/` — canonical domain contracts (SafetyConfig §1.2,
  RejectionReason §8, OrderState §7, RiskDecision). Single source of truth.

Genuinely empty (PLANNED, no code): `services/{gateway,learning}` (no auth/online-
learning layer), the nested DDD sub-trees under `services/{alpha,data,research}/*`
(`alpha_discovery_engine`, `*_service`, `model_registry`, `experiment_tracker`,
`dataset_builder`, …), `platform/`, `schemas/`, `proto/`, `config/`. The real code
lives in the FLAT `services/<domain>/*.py` modules, not these shells.

## Commands

```bash
# Full fail-closed gate (governance scanner + all runnable test suites)
python scripts/validate.py

# Governance scanner only
python apps/research-console/scripts/run_paper_to_live_governance_check.py

# Frontend (mock-first; opens http://localhost:3100/research)
cd apps/research-console && npm ci && npm run typecheck && npm run build && npm test

# Frontend against the REAL read-only backend (Phase 12 wiring)
#   1) start the console API (read-only computed snapshots, no broker/OMS):
python -m uvicorn services.research.console_api.main:app --host 127.0.0.1 --port 8400
#   2) point the console at it and disable mock mode (CORS allows :3100 only):
cd apps/research-console && VITE_API_URL=http://127.0.0.1:8400 VITE_MOCK_MODE=false npm run dev

# Research-ops console (operate research FROM the frontend): also start the GUARDED runner.
#   research_runner is the ONLY mutating surface — POST /run triggers a WHITELISTED research kind
#   (backtest/search/data refresh) async into the run_registry; it touches NO order/broker/credential
#   path (machine-enforced by tests/governance/test_research_runner_boundary.py). console_api stays GET-only.
python -m uvicorn services.research.research_runner.main:app --host 127.0.0.1 --port 8420
#   then run the frontend with both URLs (the `research-console-live` launch.json config sets these):
cd apps/research-console && VITE_API_URL=http://127.0.0.1:8400 VITE_RUNNER_URL=http://127.0.0.1:8420 \
  VITE_MOCK_MODE=false npm run dev   # → /research/runs lists every run; buttons trigger new ones
```

Note: all backend services now live under the repo-root `services/` tree
(unified in Phase 1; `apps/research-console/` is frontend-only). `scripts/validate.py`
runs the full suite from the repo root — prefer it over a bare `pytest`.

Phase 12 wiring is contract-guarded on BOTH sides: `tests/research/test_console_api_contract.py`
(in the gate) asserts every endpoint returns the exact top-level keys the
frontend `*Payload` types read, and the console API client validates the
read-only snapshot markers at runtime (`assertConsoleSnapshot`) so a drifted /
non-snapshot 200 surfaces as a visible error + honest mock fallback rather than
silently rendering `undefined`. Change a payload shape → change both the TS type
and the contract test in the same commit.
