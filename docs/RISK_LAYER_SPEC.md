# QUANTOS Risk Layer — Pre-Trade Gatekeeper (Phase 8) Build Spec

From a 4-lens design workshop + adversarial critique. **Critique P0/P1 fixes folded in (⚑).**

## Placement & decisions
- Package: `services/risk/pre_trade/` (NOT under services/trading — keeps the dependency
  arrow portfolio/alpha → risk one-directional; the quarantined
  `services/trading/pre_trade_risk/main.py` stays untouched).
- SEPARATE gate (do NOT reuse research_risk's `elif`-masking gate — it answers "is this
  strategy fit?" not "may THIS order pass now?"). Lift only math idioms.
- Two-stage battery (A: hard safety/permission/structural gates; B: limit checks).
  **Run ALL checks, collect ALL reasons** (no short-circuit; the kill-switch is consulted
  first and is absolute). Output is the canonical `quantos.contracts.risk.RiskDecision`.
- Every reject maps to one of the 20 §8 `RejectionReason`; gross/net/leverage →
  POSITION_LIMIT_EXCEEDED, rate → TURNOVER_LIMIT_EXCEEDED (documented; the 20 aren't expanded).

## ⚑ Critique P0 fixes (MUST hold)
- **P0-1 producer-side approval ref**: only an APPROVED decision yields a non-empty
  `pre_trade_risk_ref`; REJECTED → `""` so the dry-run boundary's existing PRE_TRADE_MISSING
  fails it closed. The gate is the sole producer. (`approval_ref(decision, decision_id)`.)
- **P0-2 constrain emitted type**: the gate asserts `decision.decision in {APPROVED, REJECTED}`
  before emit; APPROVED_WITH_LIMITS / NEEDS_MANUAL_REVIEW are never produced (fail-closed REJECT otherwise).
- **P0-3 audit-then-or-reject + NO auto-arm**: build decision → attempt audit → any audit
  failure returns REJECTED(UNKNOWN_RISK_ERROR), never the pre-built APPROVED. Auto-arm of the
  kill-switch is DEFERRED (self-DoS hot-path risk); kill-switch is armed explicitly by monitor/human.
- **P0-4 human-only incident-bound reset**: reset requires actor.is_human AND a matching
  ApprovalRecord (approved=True, subject_id == the trip's incident id, approver == actor.actor_id).
- **P0-5 non-finite at the boundary**: every float field in context.py uses
  `ConfigDict(allow_inf_nan=False)` (reject NaN/inf at construction) + per-check `math.isfinite`
  belt-and-suspenders.

## ⚑ Critique P1 fixes
- P1-1 audit metadata carries granular `breached_checks` (since gross/net/leverage share a reason).
- P1-2 comparator explicit per limit (loss/rate reject AT limit; size/exposure reject OVER).
- P1-4 snapshot coherence: summed positions must reconcile to snapshot gross/net else REJECT.
- P1-5 ONE SafetyConfig bound at construction (not in ctx) — no divergence.
- P1-6 AI-direct = allow-list: reject unless `actor.is_human` on the direct path (SYSTEM also barred).
- P2-6 add `services.risk` to portfolio/alpha import-boundary FORBIDDEN_PREFIXES.

## Module map (`services/risk/pre_trade/`)
config.py (RiskLimits frozen, no None/inf "unlimited" sentinel, empty allow-lists reject-all),
context.py (AccountRiskState/MarketContext/StrategyApproval/RiskContext, allow_inf_nan=False,
derived projected_position/order_notional/request_hash + snapshot coherence),
kill_switch.py (latched, fail-closed is_active, arm any-actor, human-only incident-bound reset,
UnauthorizedReset), checks.py (one pure check_* per gate + CHECK_REGISTRY ordered),
gate.py (PreTradeRiskGate.evaluate: assert_research_safe at construction, kill-switch first,
run-all/collect-all, per-check + outer try/except → UNKNOWN_RISK_ERROR, type-constrain, audit-or-reject,
order bridge, approval_ref), audit.py (hash-chained RiskAuditTrail + audit_chain_valid),
order_bridge.py (VALIDATING→RISK_APPROVED/RISK_REJECTED). New contract:
`quantos/contracts/killswitch.py` (KillSwitchScope, KillSwitchState).

## Fail-closed rules (exhaustive)
exceptions→REJECT(UNKNOWN_RISK_ERROR); RiskDecision invariant backstop; missing/non-finite/stale
data = breach not zero/unlimited; missing limit = construction error (no inf sentinel); empty
allow-lists reject-all; kill-switch default-active on malformed state, checked first, absolute;
assert_research_safe at construction; post-trade SIGNED portfolio-state checks (not per-order
isolation); no intent mutation; run-all collect-all deduped+ordered; only the 20 canonical reasons;
audit-write failure→REJECT; dup/replay→DUPLICATE_ORDER; deterministic (request-supplied clock).

## Build order (always-green commits)
1 killswitch contract + tests. 2 config + context + kill_switch + tests. 3 checks + gate + audit +
order_bridge + tests (the core). 4 P0-1 dry-run approval_ref + boundary test; wire tests/risk into
validate.py; add services.risk to portfolio/alpha FORBIDDEN_PREFIXES.

## Tests-first (24 — see workshop): fail-closed-on-exception, every-limit-boundary (per-comparator),
degenerate-numerics, sell-flip-through-short-cap, post-trade-aggregate-breach, missing/stale-context,
live+ai-direct-reject, strategy-not-approved, kill-switch blocks-all / latched / human-only-reset /
corrupt-fails-tripped, duplicate, no-intent-mutation, deterministic+hashed, audit+order-state,
all-20-reachable, P0-1 rejected-decision-yields-empty-ref-and-boundary-fails-closed.
