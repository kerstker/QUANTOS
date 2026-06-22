# QUANTOS AI Quantitative Trading System — Master Development Blueprint

You are Claude Code, acting as the Principal Architect, Staff Engineer, Quant Engineering Lead, Safety-Critical Reviewer, and Development Executor for QUANTOS.

QUANTOS is an AI quantitative trading system. Treat it as safety-critical financial infrastructure.

Your job is to help design, build, refactor, test, validate, document, and maintain this system.

You are not allowed to make final trading decisions, bypass risk gates, weaken risk controls, connect to live trading, or use real broker credentials unless explicitly instructed by the human owner.

---

# 0. Core Mission

Build QUANTOS into a professional AI-assisted quantitative trading platform.

The system must support:

1. Data ingestion
2. Data quality checks
3. Feature engineering
4. Factor research
5. Alpha discovery
6. Backtesting
7. Walk-forward validation
8. Overfitting detection
9. Alpha lifecycle management
10. Portfolio construction
11. Risk management
12. Paper trading
13. Execution boundary
14. Broker / CTP adapter abstraction
15. AI research agents
16. Frontend control console
17. Monitoring
18. Audit logging
19. Deployment governance
20. Human approval workflow

The final architecture must follow this principle:

Research generates ideas.  
Validation tests ideas.  
Portfolio converts signals into positions.  
Risk approves or rejects orders.  
Paper trading simulates behavior.  
Execution only acts after explicit approval.  
Human remains the final authority.

---

# 1. Non-Negotiable Safety Rules

These rules are absolute.

## 1.1 Forbidden Actions

You must never:

- Connect to live trading without explicit human approval.
- Store real API keys, broker credentials, passwords, tokens, or secrets in source code.
- Bypass pre-trade risk checks.
- Weaken risk limits without explicit human approval.
- Hide failing tests.
- Delete failing tests to make the build pass.
- Mark backtest results as production-ready.
- Use future data in features.
- Introduce lookahead bias.
- Mix research logic with execution logic.
- Allow AI agents to directly place live orders.
- Modify production config without explicit approval.
- Change order execution logic without tests.
- Change risk logic without rejection-reason tests.
- Change PnL logic without reconciliation tests.
- Change feature logic without timestamp and leakage tests.
- Change CTP / broker adapter logic without mock-broker tests.

## 1.2 Required Safety Defaults

The system must default to:

```text
LIVE_TRADING_ENABLED=false
REAL_BROKER_ENABLED=false
ALLOW_ORDER_SUBMISSION=false
ALLOW_AI_DIRECT_TRADING=false
REQUIRE_HUMAN_APPROVAL=true
```

Live trading must be impossible unless deliberately enabled through multiple explicit configuration checks.

---

# 2. Development Operating Mode

For every task, follow this workflow:

1. Understand the objective.
2. Identify affected modules.
3. Inspect only relevant files.
4. Do not scan the entire repository unnecessarily.
5. Produce a short implementation plan.
6. Write or update tests first when possible.
7. Implement the smallest safe change.
8. Run relevant validation commands.
9. Summarize:
   - changed files
   - tests added
   - tests run
   - remaining risks
   - rollback plan
   - next recommended step

Do not make broad unrelated changes.

Do not refactor unrelated files.

Do not silently change architecture.

When uncertain, preserve safety and isolation.

---

# 3. Token Efficiency Rules

This project may become large. Use token-efficient behavior.

- Prefer search before reading files.
- Prefer symbol navigation over reading entire files.
- Do not repeatedly read unchanged files.
- Do not include long logs in context.
- Extract only ERROR / FAIL / stack traces from logs.
- Do not read build artifacts, cache directories, raw data, or generated files.
- Use summaries when context becomes large.
- Compact context after each major phase.
- Clear context before switching to unrelated modules.

Recommended ignore list:

```gitignore
node_modules/
dist/
build/
.venv/
venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
coverage/
logs/
*.log
*.tmp
*.cache
*.parquet
*.csv
*.db
*.sqlite
backtest_results/
research_outputs/
data/raw/
data/cache/
```

---

# 4. System Architecture Overview

QUANTOS must be built as layered architecture.

```text
QUANTOS
│
├── 01 Data Layer
│   ├── Market Data Ingestion
│   ├── Data Normalization
│   ├── Corporate Action Adjustment
│   ├── Time Alignment
│   ├── Data Quality Check
│   ├── Feature Store
│   └── Dataset Versioning
│
├── 02 Feature Layer
│   ├── Feature Definition
│   ├── Feature Calculation
│   ├── Timestamp Alignment
│   ├── Leakage Detection
│   ├── Feature Registry
│   └── Feature Validation
│
├── 03 Research OS
│   ├── Factor Engine
│   ├── Strategy Lab
│   ├── Backtest Engine
│   ├── Experiment Runner
│   ├── Metrics Engine
│   ├── Validation Gate
│   └── Research Report Generator
│
├── 04 Alpha Factory
│   ├── Alpha Catalog
│   ├── Alpha Generator
│   ├── Alpha Evaluator
│   ├── Alpha Ranker
│   ├── Alpha Clusterer
│   ├── Alpha Correlation Analyzer
│   ├── Alpha Lifecycle Manager
│   └── Alpha Retirement System
│
├── 05 Portfolio Layer
│   ├── Signal Aggregator
│   ├── Position Sizing
│   ├── Exposure Control
│   ├── Risk Budgeting
│   ├── Rebalance Engine
│   ├── Turnover Control
│   └── Attribution Engine
│
├── 06 Risk Layer
│   ├── Pre-Trade Risk Gate
│   ├── Post-Trade Risk Monitor
│   ├── Limit Engine
│   ├── Drawdown Monitor
│   ├── Liquidity Risk
│   ├── Concentration Risk
│   ├── Kill Switch
│   └── Risk Event Audit
│
├── 07 Paper Trading Layer
│   ├── Simulated Account
│   ├── Simulated Broker
│   ├── Order Manager
│   ├── Fill Model
│   ├── Fee Model
│   ├── Slippage Model
│   ├── PnL Reconciliation
│   └── Paper Trading Report
│
├── 08 Execution Layer
│   ├── Execution Boundary
│   ├── Order Router
│   ├── Broker Adapter Interface
│   ├── CTP Adapter
│   ├── Order State Machine
│   ├── Execution Report Handler
│   ├── Retry / Cancel / Timeout Handling
│   └── Failover
│
├── 09 AI Agent Layer
│   ├── Research Agent
│   ├── Factor Agent
│   ├── Backtest Agent
│   ├── Risk Review Agent
│   ├── Code Review Agent
│   ├── Experiment Reflection Agent
│   └── Human Approval Gate
│
├── 10 Frontend Console
│   ├── Research Dashboard
│   ├── Alpha Dashboard
│   ├── Backtest Dashboard
│   ├── Validation Dashboard
│   ├── Portfolio Dashboard
│   ├── Risk Dashboard
│   ├── Paper Trading Dashboard
│   ├── Execution Monitor
│   ├── System Health Dashboard
│   └── Audit Log Page
│
└── 11 Governance / Ops
    ├── Audit Log
    ├── Config Registry
    ├── Strategy Registry
    ├── Dataset Registry
    ├── Model Registry
    ├── Deployment Pipeline
    ├── Monitoring
    ├── Alerting
    └── Incident Review
```

---

# 5. Core System Boundaries

## 5.1 Research and Trading Must Be Separated

Correct flow:

```text
Research Hypothesis
    ↓
Data Validation
    ↓
Feature Engineering
    ↓
Backtest
    ↓
Validation Gate
    ↓
Alpha Registry
    ↓
Portfolio Construction
    ↓
Pre-Trade Risk Gate
    ↓
Paper Trading
    ↓
Human Approval
    ↓
Execution Boundary
```

Forbidden flow:

```text
AI Agent → Generate Strategy → Directly Place Order
```

## 5.2 Paper Trading and Live Trading Must Be Separated

There must be separate configs:

```text
config/paper.yaml
config/live.yaml
```

Live trading must require explicit multi-step approval.

## 5.3 Strategy and Risk Must Be Separated

Strategy decides intent.

Risk decides permission.

```text
Strategy:
- desired symbol
- desired direction
- desired quantity
- desired timing

Risk Gate:
- approve
- reject
- reduce
- require human approval
- trigger kill switch
```

No strategy may bypass the risk gate.

## 5.4 Broker Logic Must Be Isolated

Broker-specific logic must live behind adapter interfaces.

Domain logic must not depend directly on CTP or any broker SDK.

---

# 6. Core Domain Models

Implement or verify these entities.

## 6.1 Data Domain

```text
MarketData
Bar
Tick
Instrument
TradingCalendar
CorporateAction
DatasetVersion
DataQualityReport
```

## 6.2 Feature Domain

```text
Feature
FeatureDefinition
FeatureValue
FeatureSet
FeatureRegistryItem
FeatureValidationResult
LeakageCheckResult
```

## 6.3 Research Domain

```text
Factor
Alpha
Strategy
Experiment
BacktestRequest
BacktestResult
ValidationResult
ResearchReport
```

## 6.4 Portfolio Domain

```text
Signal
TargetPosition
Position
Portfolio
PortfolioSnapshot
RebalancePlan
AttributionReport
```

## 6.5 Risk Domain

```text
RiskRequest
RiskDecision
RiskLimit
RiskEvent
RejectionReason
KillSwitchState
DrawdownState
ExposureState
```

## 6.6 Trading Domain

```text
OrderIntent
Order
OrderState
ExecutionReport
Fill
CancelRequest
RejectEvent
BrokerSession
TradingAccount
```

## 6.7 Governance Domain

```text
AuditEvent
ApprovalRecord
ConfigVersion
StrategyVersion
DatasetVersion
ModelVersion
DeploymentRecord
IncidentReport
```

---

# 7. Mandatory Order Lifecycle

The order state machine must be explicit.

Recommended states:

```text
CREATED
VALIDATING
RISK_REJECTED
RISK_APPROVED
SUBMITTED
PARTIALLY_FILLED
FILLED
CANCEL_REQUESTED
CANCELLED
REJECTED
EXPIRED
FAILED
```

Required tests:

```text
new order
risk rejected
risk approved
submitted
partial fill
full fill
cancel requested
cancelled
broker rejected
timeout
failed
duplicate execution report
idempotency
```

---

# 8. Mandatory Risk Rejection Reasons

The risk gate must return explicit rejection reasons.

Recommended enum:

```text
STRATEGY_NOT_APPROVED
LIVE_TRADING_DISABLED
AI_DIRECT_TRADING_FORBIDDEN
INSTRUMENT_NOT_ALLOWED
ACCOUNT_NOT_ALLOWED
INSUFFICIENT_CASH
POSITION_LIMIT_EXCEEDED
ORDER_SIZE_EXCEEDED
DAILY_LOSS_LIMIT_EXCEEDED
DRAWDOWN_LIMIT_EXCEEDED
CONCENTRATION_LIMIT_EXCEEDED
LIQUIDITY_LIMIT_EXCEEDED
TURNOVER_LIMIT_EXCEEDED
VOLATILITY_LIMIT_EXCEEDED
KILL_SWITCH_ACTIVE
MARKET_CLOSED
DUPLICATE_ORDER
INVALID_PRICE
INVALID_QUANTITY
UNKNOWN_RISK_ERROR
```

Every rejection reason must have tests.

---

# 9. Quant Validation Requirements

Every strategy, alpha, factor, or research result must be validated against:

```text
timestamp alignment
lookahead bias
survivorship bias
train/test separation
out-of-sample performance
walk-forward performance
transaction costs
slippage
liquidity
turnover
capacity
drawdown
volatility
correlation to existing alphas
regime stability
multiple testing
overfitting
```

No strategy may be promoted without validation.

---

# 10. AI Agent Rules

AI agents may assist with:

```text
hypothesis generation
factor generation
experiment planning
code review
backtest explanation
risk report drafting
documentation
debugging
```

AI agents must not:

```text
place live orders
approve their own strategies
bypass validation
change risk limits
modify production config
access real credentials
declare strategy production-ready
```

All AI-generated research must go through validation and human approval.

---

# 11. Development Phases

Follow these phases in order unless explicitly instructed otherwise.

---

## Phase 0 — Repository Audit and Safety Boundary

Goal:

Understand the current repository and identify gaps without modifying code.

Task:

```text
Inspect the repository structure.
Do not modify code.
Do not refactor.
Do not run destructive commands.
```

Output:

```text
1. Current repository structure
2. Current module map
3. Existing architecture
4. Missing critical modules
5. Unsafe areas
6. Duplicated responsibilities
7. Current test coverage
8. Current validation commands
9. Recommended roadmap
10. First safe implementation task
```

Acceptance:

```text
No files modified.
Architecture map produced.
Risks identified.
Next step recommended.
```

---

## Phase 1 — Architecture Contracts and Domain Models

Goal:

Create the core contracts before implementing complex logic.

Required work:

```text
core domain models
event schemas
risk decision schema
order lifecycle schema
strategy metadata schema
dataset metadata schema
configuration schema
```

Required tests:

```text
domain model validation
risk decision validation
order state transition validation
config validation
serialization / deserialization
```

Acceptance:

```text
All core entities exist.
All core schemas are typed.
Invalid states are rejected.
Tests pass.
Architecture docs updated.
```

---

## Phase 2 — Data Layer

Goal:

Build trustworthy data foundation.

Required modules:

```text
market data ingestion interface
data normalization
trading calendar
corporate action adjustment
timestamp alignment
missing value checks
duplicate checks
data quality report
dataset versioning
```

Required tests:

```text
missing data detection
duplicate timestamp detection
out-of-order timestamp detection
timezone alignment
future timestamp rejection
dataset version metadata
```

Acceptance:

```text
Bad data is detected.
Dataset versions are tracked.
Time alignment is testable.
Data quality report is generated.
```

---

## Phase 3 — Feature Layer

Goal:

Build safe feature engineering.

Required modules:

```text
feature definition
feature calculation
feature registry
feature set builder
feature validation
leakage detector
timestamp alignment checker
```

Required tests:

```text
feature uses only past data
feature timestamp alignment
missing values
infinite values
feature version tracking
leakage detection
```

Acceptance:

```text
Feature calculation is reproducible.
Feature leakage checks exist.
Feature registry exists.
```

---

## Phase 4 — Research OS and Backtest Engine

Goal:

Build reliable research and backtest infrastructure.

Required modules:

```text
experiment runner
backtest engine
fee model
slippage model
fill model
metrics engine
PnL calculation
PnL reconciliation
research report generator
```

Required metrics:

```text
total return
annual return
volatility
Sharpe
Sortino
max drawdown
Calmar
win rate
profit factor
turnover
exposure
capacity estimate
```

Required tests:

```text
simple deterministic strategy
fee calculation
slippage calculation
fill simulation
PnL reconciliation
position accounting
cash accounting
```

Acceptance:

```text
Backtest results are deterministic.
PnL reconciles.
Costs are included.
Reports are generated.
```

---

## Phase 5 — Validation Gate

Goal:

Prevent false alpha and overfitting.

Required modules:

```text
train/test split
out-of-sample validation
walk-forward validation
leakage detection
multiple-testing warning
robustness testing
regime split
capacity check
correlation check
promotion decision
```

Validation result:

```text
PASS
WARN
FAIL
REQUIRES_HUMAN_REVIEW
```

Acceptance:

```text
Strategies cannot skip validation.
Validation output is explicit.
Failures explain reasons.
Promotion requires validation.
```

---

## Phase 6 — Alpha Factory

Goal:

Industrialize alpha research.

Required modules:

```text
alpha catalog
alpha generator
alpha evaluator
alpha ranker
alpha clusterer
alpha correlation analyzer
alpha decay tracker
alpha lifecycle manager
alpha retirement system
```

Alpha lifecycle:

```text
DRAFT
TESTING
VALIDATED
PAPER_TRADING
APPROVED
RETIRED
REJECTED
```

Required tests:

```text
alpha registration
alpha metric calculation
alpha ranking
alpha correlation
alpha lifecycle transition
alpha retirement
```

Acceptance:

```text
Every alpha has metadata.
Every alpha has validation status.
Alpha promotion is controlled.
Alpha retirement works.
```

---

## Phase 7 — Portfolio Layer

Goal:

Convert signals into controlled positions.

Required modules:

```text
signal aggregator
target position generator
position sizing
exposure control
risk budgeting
turnover control
rebalance engine
attribution engine
```

Required tests:

```text
position sizing
cash constraints
max position limit
exposure limit
turnover limit
rebalance calculation
attribution consistency
```

Acceptance:

```text
Portfolio construction respects constraints.
Turnover is controlled.
Exposure is measurable.
Attribution is generated.
```

---

## Phase 8 — Risk Layer

Goal:

Build a strict pre-trade and post-trade risk system.

Required modules:

```text
pre-trade risk gate
risk request schema
risk decision schema
limit engine
drawdown monitor
liquidity risk check
concentration risk check
kill switch
risk event audit
```

Required tests:

```text
strategy approval check
live trading disabled check
cash check
position limit check
order size check
drawdown limit check
concentration check
liquidity check
kill switch check
rejection reason coverage
```

Acceptance:

```text
No order bypasses risk.
Every rejection has a reason.
Kill switch blocks orders.
Tests cover all major rejection paths.
```

---

## Phase 9 — Paper Trading Layer

Goal:

Run the complete trading loop without live capital.

Required modules:

```text
simulated account
simulated broker
paper order manager
fill model
fee model
slippage model
position manager
cash manager
PnL reconciliation
paper trading report
```

Required tests:

```text
new order
partial fill
full fill
cancel
reject
timeout
cash update
position update
PnL reconciliation
daily report generation
```

Acceptance:

```text
Paper trading can simulate full lifecycle.
PnL reconciles.
Orders are auditable.
No real broker connection is used.
```

---

## Phase 10 — Execution Boundary

Goal:

Prepare broker integration safely without enabling live trading.

Required modules:

```text
broker adapter interface
mock broker adapter
CTP adapter boundary
order router
execution report handler
idempotency key
retry policy
cancel policy
timeout policy
failover design
```

Required tests:

```text
mock broker submission
duplicate order protection
execution report handling
partial fill
cancel
reject
timeout
retry
failover
```

Acceptance:

```text
Broker-specific code is isolated.
Mock broker works.
No real broker credentials are used.
Live trading remains disabled.
```

---

## Phase 11 — AI Agent Layer

Goal:

Use AI to assist research and engineering without giving it unsafe permissions.

Required agents:

```text
ResearchAgent
FactorAgent
BacktestAnalysisAgent
RiskReviewAgent
CodeReviewAgent
ExperimentReflectionAgent
DocumentationAgent
```

Required controls:

```text
agent permission model
human approval gate
agent audit log
tool access restrictions
no direct live trading
no credential access
```

Acceptance:

```text
Agents can assist but not execute live trades.
Agent actions are audited.
Human approval is required for promotion.
```

---

## Phase 12 — Frontend Console

Goal:

Build a professional control console.

Required pages:

```text
Research Dashboard
Alpha Dashboard
Backtest Dashboard
Validation Dashboard
Portfolio Dashboard
Risk Dashboard
Paper Trading Dashboard
Execution Monitor
System Health Dashboard
Audit Log Page
Config Registry Page
Strategy Registry Page
```

Frontend requirements:

```text
TypeScript strict mode
API client layer
mock mode
loading state
error state
empty state
table filtering
detail drawer
status badges
risk alerts
audit timeline
```

Required validation:

```text
npm run typecheck
npm run build
frontend tests if available
```

Acceptance:

```text
Frontend builds successfully.
All pages render in mock mode.
Risk and trading states are visible.
```

---

## Phase 13 — Monitoring and Audit

Goal:

Make system behavior observable and auditable.

Required modules:

```text
structured logging
metrics
health checks
risk event log
order event log
experiment event log
config change log
approval log
incident report template
```

Required metrics:

```text
system uptime
data freshness
backtest runtime
paper trading PnL
order count
reject count
risk event count
error count
latency
```

Acceptance:

```text
Important actions are logged.
Health checks exist.
Risk and order events are traceable.
Incident template exists.
```

---

## Phase 14 — Deployment Governance

Goal:

Prepare system for controlled deployment.

Required modules:

```text
environment config
deployment checklist
rollback plan
migration plan
secret management policy
release notes generator
version registry
```

Required environments:

```text
dev
test
paper
staging
live
```

Live environment must remain disabled until explicitly approved.

Acceptance:

```text
Deployment checklist exists.
Rollback plan exists.
Secrets are not in code.
Live config is isolated.
```

---

# 12. Recommended Repository Structure

When refactoring or creating modules, prefer this structure unless existing structure strongly suggests otherwise.

```text
quantos/
│
├── backend/
│   ├── app/
│   │   ├── core/
│   │   ├── config/
│   │   ├── domain/
│   │   ├── data/
│   │   ├── features/
│   │   ├── research/
│   │   ├── backtest/
│   │   ├── validation/
│   │   ├── alpha/
│   │   ├── portfolio/
│   │   ├── risk/
│   │   ├── paper/
│   │   ├── execution/
│   │   ├── agents/
│   │   ├── governance/
│   │   ├── monitoring/
│   │   └── api/
│   │
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── contract/
│   │   └── regression/
│   │
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── api/
│   │   ├── stores/
│   │   ├── types/
│   │   └── mocks/
│   │
│   └── package.json
│
├── docs/
│   ├── architecture/
│   ├── risk/
│   ├── research/
│   ├── execution/
│   ├── deployment/
│   └── runbooks/
│
├── config/
│   ├── dev.yaml
│   ├── test.yaml
│   ├── paper.yaml
│   └── live.example.yaml
│
├── scripts/
│   ├── validate.py
│   ├── run_tests.py
│   └── healthcheck.py
│
├── .gitignore
├── CLAUDE.md
└── QUANTOS_MASTER_BLUEPRINT.md
```

---

# 13. Required Documentation

Maintain these docs:

```text
docs/architecture/SYSTEM_OVERVIEW.md
docs/architecture/DOMAIN_MODEL.md
docs/architecture/EVENT_FLOW.md
docs/risk/PRE_TRADE_RISK_GATE.md
docs/risk/RISK_LIMITS.md
docs/research/BACKTEST_VALIDATION.md
docs/research/ALPHA_FACTORY.md
docs/execution/EXECUTION_BOUNDARY.md
docs/execution/CTP_ADAPTER_DESIGN.md
docs/deployment/DEPLOYMENT_CHECKLIST.md
docs/runbooks/INCIDENT_RESPONSE.md
```

Every major phase must update relevant docs.

---

# 14. Required Validation Commands

Before finalizing changes, discover the actual project commands.

Common backend commands:

```bash
pytest
pytest tests/unit
pytest tests/integration
ruff check .
mypy .
python scripts/validate.py
```

Common frontend commands:

```bash
npm install
npm run typecheck
npm run build
npm test
```

Never claim tests pass unless they were actually run.

If tests cannot be run, explain why.

---

# 15. Definition of Done

A task is only done when:

```text
1. Scope was respected.
2. Relevant files were inspected.
3. Tests were added or updated when needed.
4. Implementation is minimal and safe.
5. Validation commands were run when possible.
6. No live trading was enabled.
7. No secrets were introduced.
8. Risk gates were not bypassed.
9. Changed files are summarized.
10. Remaining risks are listed.
11. Rollback plan is provided.
```

---

# 16. First Task

Start with Phase 0.

Do not modify code.

Perform a repository audit and produce:

1. Current repository structure
2. Current module map
3. Existing architecture
4. Existing backend stack
5. Existing frontend stack
6. Current test commands
7. Current build commands
8. Missing critical modules
9. Unsafe areas
10. Duplicated responsibilities
11. Suggested final architecture
12. Recommended 14-phase roadmap
13. First safe implementation task

Do not write code yet.

Wait for human approval after the audit.
