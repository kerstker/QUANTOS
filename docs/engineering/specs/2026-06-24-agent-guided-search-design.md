# Agent Guided Search + Learning-from-Outcomes — Design

Date: 2026-06-24 · Status: approved · Scope: single implementation plan
Audit driver: `docs/QUANTOS_AUDIT_2026-06.md` §6 P1 — "Autonomous agent loop is a
fixed scripted pipeline — no hypothesis generation, search, or learning."

## 1. Goal

Turn the autonomous research agent from a fixed for-loop over a hardcoded
candidate list into a **deterministic guided search that learns from evaluation
outcomes** — WITHOUT granting any new authority. The agent still proposes and
evaluates hypotheses through the unchanged guardrailed lifecycle path and can
NEVER reach APPROVED / LIVE_TRADE / reset the kill-switch.

The "intelligence" is a pure-Python UCB1 multi-armed bandit over a parameterized
hypothesis space — no LLM, no RNG, fully deterministic. Over rounds, it
concentrates proposals on the feature buckets (factors / operators / windows)
whose past evaluations scored highest.

Constraints: L2_DRY_RUN_ONLY; pure-Python stdlib; deterministic (no RNG; vary by
arm/index); fail-closed; reuse the unchanged guardrails + lifecycle chokepoint;
preserve the agent containment + import-boundary invariants (re-tested).

## 2. Architecture

New modules in `services/intelligence/`:
```
search_space.py    HypothesisSpace — factors x operators x windows -> AlphaDefinition; arms
search_strategy.py UCBSearch — UCB1 bandit over arms; propose() / update(); arm_means()
reward.py          evaluation_reward(AlphaEvaluation) -> reward in [0,1]
guided_search.py   GuidedSearchLoop — wraps an UNCHANGED ResearchAgentLoop; rounds of propose->run->reward->update
```
One additive change: `agent_loop.py` `ResearchAgentLoop.run` adds an `evaluation`
field to each per-candidate report entry (so the wrapper can compute reward) —
NO control-flow change. Everything else (guardrails, lifecycle, evaluator) is
reused unchanged.

Dependency direction: `guided_search` → `search_strategy`/`search_space`/`reward`
+ the existing `ResearchAgentLoop`; the new modules import only `quantos.contracts`
+ `services.alpha` (same surface the agent layer already uses) — the
import-boundary test (no live/broker surface, transitive) still holds.

## 3. Components

### 3.1 `search_space.py`
- `@dataclass(frozen=True) HypothesisSpace: factors: tuple[str,...]; operators:
  tuple[str,...]; windows: tuple[int,...]` (all non-empty; validated).
- A **hypothesis** is `(factor, operator, window)`.
- `to_definition(self, factor, operator, window) -> AlphaDefinition`:
  `alpha_id=f"h_{operator}_{factor}_{window}"`, `factor_ids=(factor,)`,
  `params={"operator": operator, "window": str(window)}`. Distinct hypotheses get
  distinct `definition_hash` (honest multiple-testing trial counting downstream).
- `arms(self) -> list[tuple[str,str]]`: the union of `("factor",f)`, `("operator",op)`,
  `("window",str(w))`. A hypothesis touches exactly one arm of each kind.
- `arms_of(self, hypothesis) -> tuple[arm,arm,arm]`.
- `hypotheses(self) -> list[tuple]`: the full deterministic grid (sorted).

### 3.2 `search_strategy.py` — UCBSearch
- `__init__(self, space, *, c: float = sqrt(2))`. State: `pulls: dict[arm,int]`,
  `reward_sum: dict[arm,float]`, `n_evaluated: int`, `proposed: set[hypothesis]`.
- `_ucb(self, arm) -> float`: `inf` if `pulls[arm]==0` (force first exploration);
  else `mean + c*sqrt(ln(max(n_evaluated,1))/pulls[arm])`.
- `propose(self, batch_size) -> list[hypothesis]`: score each NOT-yet-proposed
  hypothesis by the SUM of its 3 arms' UCB; sort descending with a deterministic
  tie-break (the hypothesis tuple); return the top `batch_size` and mark them
  proposed. Returns fewer (or empty) when the grid is exhausted (fail-soft).
- `update(self, hypothesis, reward) -> None`: for each of its 3 arms,
  `pulls[arm]+=1; reward_sum[arm]+=reward`; `n_evaluated += 1`.
- `arm_means(self) -> dict[arm,float]`: `reward_sum/pulls` per pulled arm
  (for assertions / inspection of what was learned).

### 3.3 `reward.py`
- `evaluation_reward(ev) -> float` in `[0,1]`, monotonic in the quality signals so
  the bandit learns from a continuous reward even when verdicts are
  REQUIRES_HUMAN_REVIEW (the common case before evidence is supplied):
  `reward = clamp01(0.5*ev.deflated_sharpe + 0.35*clamp01((ev.net_sharpe+1)/2) +
  0.15*verdict_bonus)` where `verdict_bonus` = 1.0 PASS / 0.5 WARN / 0.0 otherwise.
  A `None` evaluation (a candidate that errored in the loop) yields reward `0.0`.

### 3.4 `guided_search.py` — GuidedSearchLoop
- `__init__(self, space, *, strategy=None, inner_loop=None)`. `strategy` defaults
  to `UCBSearch(space)`; `inner_loop` defaults to a fresh `ResearchAgentLoop`
  (the unchanged guardrailed evaluate+advance path; its actor is a non-human AGENT).
- `search(self, rounds, batch_size, periods_provider, *, timestamp=0) -> dict`:
  for each round — `assert_action_allowed(inner_loop.actor, AgentAction.PROPOSE)`;
  `hyps = strategy.propose(batch_size)`; build `candidates = [(defn,
  periods_provider(defn)) for defn in map(to_definition, hyps)]`; `out =
  inner_loop.run(candidates, timestamp=timestamp)`; for each hyp read
  `out["report"][defn.alpha_id].get("evaluation")`, compute `evaluation_reward`,
  `strategy.update(hyp, reward)`; accumulate history + human_review_queue.
  Returns `{history, arm_means, human_review_queue, report}`. Stops early when
  `propose` returns empty (grid exhausted).
- `periods_provider: Callable[[AlphaDefinition], Sequence[PanelPeriod]]` — supplied
  by the caller, keeping the search decoupled from data/PIT concerns.

## 4. Data flow

```
HypothesisSpace ──► UCBSearch.propose(batch) ──► AlphaDefinitions
                                                      │ periods_provider
                                                      ▼
        (unchanged) ResearchAgentLoop.run ── guardrails + lifecycle ──► verdict/state (NEVER APPROVED)
                                                      │ AlphaEvaluation
                                                      ▼
                              evaluation_reward ──► UCBSearch.update (LEARN)
```

## 5. Safety + error handling (the non-negotiable part)

- **No new authority**: the agent's only actions remain PROPOSE/EVALUATE/ADVANCE_*
  /REJECT/RETIRE/REQUEST_HUMAN_APPROVAL. The search is a PROPOSE-class action,
  guarded by the existing `assert_action_allowed`. No new `AgentAction`, no change
  to AGENT_ALLOWED / HUMAN_ONLY / FORBIDDEN_ALWAYS.
- **Containment preserved**: evaluate+advance runs through the UNCHANGED
  `ResearchAgentLoop` → lifecycle `assert_alpha_transition` → an AGENT actor cannot
  reach APPROVED (`UnauthorizedPromotion`). A test asserts that after a full guided
  search (incl. PASS candidates that reach PAPER_TRADING) NOTHING is APPROVED.
- **Import boundary**: the new modules import only `quantos.contracts` +
  `services.alpha` + the existing intelligence modules; the transitive
  no-live-surface test (`tests/intelligence/test_intelligence_import_boundary.py`)
  must still pass.
- **Determinism**: no RNG anywhere; `propose` ordering is a total order (UCB score
  then hypothesis tuple); identical inputs → identical proposals, rewards, and
  arm_means (a determinism test asserts equality across two runs).
- **Fail-closed / fail-soft**: empty/degenerate space raises at construction;
  `propose` returns `[]` when the grid is exhausted (loop stops, no crash); an
  errored candidate contributes reward 0 (recorded, never advanced).

## 6. Testing (tests-first)

- `tests/intelligence/test_search_space.py`: grid size, `to_definition` params +
  distinct hashes, `arms_of` returns one arm per kind, empty space raises.
- `tests/intelligence/test_search_strategy.py`: first proposals explore every arm
  (UCB inf for unpulled); after updates, a high-reward arm's mean rises and
  `propose` favors it; `proposed` dedups; grid exhaustion → `[]`; determinism.
- `tests/intelligence/test_reward.py`: bounds `[0,1]`; monotonic in
  deflated_sharpe / net_sharpe; PASS > WARN > FAIL for equal metrics; None → 0.
- `tests/intelligence/test_guided_search.py`: (a) **learning** — a synthetic
  periods-provider where one window is genuinely better; after N rounds
  `arm_means[("window", str(good))] > arm_means[("window", str(bad))]` (window arm
  keys stringify the int, matching to_definition's params); (b) **containment** —
  PASS candidates reach PAPER_TRADING but NOTHING reaches APPROVED; (c)
  **determinism** — two searches give identical arm_means; (d) the search step
  goes through PROPOSE guardrail.
- `tests/intelligence/test_agent_loop.py` (existing) still passes with the additive
  `evaluation` field; `test_intelligence_import_boundary.py` still passes.
- Wire: new tests under `tests/intelligence` (already in `scripts/validate.py`);
  governance scanner auto-covers the new modules.

## 7. Out of scope (YAGNI / roadmap)

LLM-driven hypothesis generation; evolutionary/genetic search; Thompson sampling;
feature interactions beyond (factor, operator, window); auto-supplying
leakage/pbo/regime evidence (still fail-closed to human review); persisting the
bandit state across processes (the ExperimentRegistry already persists the catalog;
bandit-state persistence is a later increment). This plan delivers the
deterministic UCB1 guided search + outcome learning over the unchanged guardrailed
path only.
