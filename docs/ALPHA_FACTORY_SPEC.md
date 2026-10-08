# QUANTOS Alpha Factory — Build Spec (Phase 6, §04)

Synthesized from a 4-lens design workshop + adversarial critique. This is the
authoritative spec for the multi-turn build. **The critique's P0/P1 fixes are
folded in (marked ⚑).**

## Decisions
- New `quantos/contracts/alpha.py` (don't overload `research.py`). Reuse
  `AlphaState`/`Alpha`/`ValidationResult` from `research.py`.
- Flat `services/alpha/` package (mirrors `services/data`, `services/research/validation`).
- **ICIR from a SERIES of per-period cross-sectional ICs**, not `factor_engine.compute_ic.icir`
  (which is single-cross-section pair-products — mathematically wrong). Test-locked.
- **Catalog-wide multiple testing**: ONE monotonic catalog trial counter feeds
  `n_trials` into the EXISTING `deflated_sharpe_ratio`/`pbo_cscv` — never per-alpha.
- Orthogonalization (Gram-Schmidt residual + marginal_IR) rewards *incremental* edge.
- APPROVED triple-locked: (a) not in `AGENT_REACHABLE`; (b) requires a verifiable
  **human** approval; (c) `services/alpha` imports nothing from `services/trading`.
- AlphaCombiner/sizing **deferred to Phase 7**.

## ⚑ Critique fixes (must implement)
- **P0-1/P0-2 (complete)**: `run_research_validation` requires the shared catalog and
  candidate IDs; there are no `n_trials_override` / `var_sr_override` escape hatches.
  `AlphaCatalog.evidence()` exclusively mints replayable `CatalogEvidence` over the
  frozen candidate-Sharpe population, including conservative effective-trial accounting.
  The gate recomputes it instead of trusting caller-supplied DSR or trial counts.
- **P0-3**: `ApprovalRecord.approver` has no actor-kind. Add `Actor(kind=HUMAN/AGENT/SYSTEM)`;
  the APPROVED guard checks the approval carries a verified HUMAN actor. Test: agent-actor
  approval rejected even with approved=True + matching subject_id.
- **P0-4**: cross-sectional long/short backtest must be **dollar-neutral on one shared
  capital base, fractional/notional sizing, per-name ADV wired so impact_coef>0 bites**,
  and reconcile the AGGREGATE book (not just per-leg).
- **P1-1**: clamp non-finite (inf) metrics before MAD-z ranking and hashing.
- **P1-2**: per-period IC None on `n<10` names → min-breadth + min-T gates → fail-closed
  REQUIRES_HUMAN_REVIEW on thin coverage (else T inflated = hidden p-hacking).
- **P1-3**: orthogonalization uses the same MIN_OVERLAP + shared as_of index as correlation.
- **P1-4**: crowding is fail-closed-to-review until a real reference panel exists; in
  Phase 6 anti-crowding rests on the correlation/cluster/orthogonalization triad +
  `max_abs_corr_to_approved` + `marginal_ir` (be honest: `crowding_score` is stub-until-panel).
- **P1-6**: write an explicit **AST import-boundary test** (services/alpha imports nothing
  from services/{trading,portfolio,execution}); scanner doesn't catch intra-repo imports.
  Plant the test-20 violation in a NON-test module.
- **P2-1**: edit `scripts/validate.py` CHECKS to include `tests/alpha` in build step 1.

## Module map
Contracts: `quantos/contracts/alpha.py` — Actor, AlphaDefinition, AlphaEvaluation,
AlphaRanking, AlphaCluster, DecayReport, RetirementDecision, AlphaTransition,
PaperEvidence + lifecycle SM (`ALPHA_ALLOWED_TRANSITIONS`, `AGENT_REACHABLE`,
`assert_alpha_transition`, `InvalidAlphaTransition`, `UnauthorizedPromotion`).

`services/alpha/`: config.py, catalog.py (owns trial counter + var_sr + approved_set),
generator.py (proposal-only, import-isolated), metrics.py (series ICIR/t-stat/p/hit/breadth/autocorr),
evaluator.py (PIT panel backtest + metrics + run_research_validation w/ catalog n_trials),
correlation.py, cluster.py, orthogonalize.py, crowding.py, capacity.py,
multiple_testing.py (catalog→n_trials/var_sr + BH-FDR), ranker.py (robust composite),
decay.py (AR(1) half-life + debounced), guards.py, lifecycle.py (transition chokepoint;
never mints ApprovalRecord), retirement.py, factory.py (orchestrator).

## Lifecycle SM
DRAFT→TESTING→VALIDATED→PAPER_TRADING→APPROVED→RETIRED/REJECTED.
- `AGENT_REACHABLE = {TESTING, VALIDATED, PAPER_TRADING, REJECTED, RETIRED}` — APPROVED excluded.
- PAPER_TRADING→APPROVED: agents barred (`UnauthorizedPromotion`); requires verified human
  `ApprovalRecord` (Actor.kind==HUMAN, approved=True, subject_id==alpha_id) AND paper-vs-backtest
  consistency. Manager never constructs an ApprovalRecord; only verifies a supplied one.
- Guards: TESTING→VALIDATED requires `ValidationResult.promotable` (PASS only) AND
  `vr.trial_count == catalog.global_trial_count()` AND dsr≥min AND pbo≤max AND BH-FDR survive.
- Automation may move toward RETIRED, never toward APPROVED. Every transition writes a
  hash-chained AuditEvent + immutable AlphaTransition (tamper-evident).

## Build order (incremental, always-green)
0. ⚑ pipeline n_trials/var_sr injection (P0-1/2).
1. contracts/alpha.py + SM; wire tests/alpha into validate.py. Tests: legal/illegal, agent≠APPROVED.
2. config + catalog (counter + var_sr). 3. metrics (series ICIR + thin-coverage gate).
4. multiple_testing. 5. evaluator + dollar-neutral panel backtest. 6. correlation+cluster+orthogonalize.
7. crowding+capacity. 8. ranker. 9. decay. 10. guards+lifecycle. 11. retirement. 12. generator. 13. factory + import-boundary/scanner locks.

## Tests-first (define done): 22 tests — see workshop output; key locks:
series-ICIR (#1), catalog-wide deflation (#2/#3), only-PASS-advances (#4), redundant-clone-sinks (#5),
AR(1) half-life (#6), duplicate-zero-marginal-IR (#7), ranker-robust-to-outlier (#8), clusterer (#9),
crowding+missing-panel-review (#10), agent-cannot-APPROVE (#11), human-token+audit (#12),
paper-inconsistency-blocks (#13), legal/illegal transitions (#14), decay-auto-retire (#15),
retirement-frees-capacity (#16), net-of-cost (#17), deterministic+PIT (#18), generator-inert/isolated (#19),
no-live-surface+scanner (#20), audited+chained (#21), registration-metadata (#22).
