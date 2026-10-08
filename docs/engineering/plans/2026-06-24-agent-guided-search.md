# Agent Guided Search + Learning Implementation Plan

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the autonomous research agent into a deterministic UCB1 guided search that learns from evaluation outcomes, WITHOUT granting any new authority (agent still can never reach APPROVED / trade live).

**Architecture:** Four new pure-Python modules in `services/intelligence/` (hypothesis space, UCB1 bandit, reward, guided-search loop) layered over an UNCHANGED `ResearchAgentLoop` (one additive report field). No LLM, no RNG.

**Tech Stack:** Python 3 stdlib (`math`, `dataclasses`, `itertools`); existing `quantos.contracts` + `services.alpha`.

## Global Constraints

- L2_DRY_RUN_ONLY; pure-Python stdlib; deterministic (NO RNG; total ordering by score then tuple); fail-closed / fail-soft.
- NO new authority: no new `AgentAction`, no change to AGENT_ALLOWED / HUMAN_ONLY / FORBIDDEN_ALWAYS. The search is a PROPOSE-class action guarded by the existing `assert_action_allowed`.
- Containment preserved: evaluate+advance goes through the UNCHANGED `ResearchAgentLoop` → lifecycle guardrails → an AGENT actor can never reach APPROVED. Re-tested.
- Import boundary preserved: new modules import only `quantos.contracts` + `services.alpha` + existing intelligence modules; `tests/intelligence/test_intelligence_import_boundary.py` must stay green.
- Gate between tasks: `python scripts/validate.py`. Isolated pytest: prefix `PYTHONPATH=H:/code/QUANTOS`.

**Reference signatures (existing):**
- `quantos.contracts.alpha.AlphaDefinition(alpha_id, name, factor_ids: tuple, universe="", params: dict[str,str])`; `.definition_hash`.
- `quantos.contracts.alpha.AlphaEvaluation` fields: `deflated_sharpe` (0..1), `net_sharpe` (float), `verdict` (`ValidationVerdict`).
- `quantos.contracts.research.ValidationVerdict` (PASS / WARN / FAIL / REQUIRES_HUMAN_REVIEW).
- `services.intelligence.guardrails.assert_action_allowed(actor, AgentAction.PROPOSE)`; `AgentAction.PROPOSE` is in AGENT_ALLOWED.
- `services.intelligence.agent_loop.ResearchAgentLoop(catalog=None, config=None, evaluator=None, manager=None, actor=None)`; `.run(candidates, *, timestamp=0) -> {"report": {aid: {...}}, "human_review_queue": [...]}`; `.actor`.
- `services.alpha.panel_backtest.PanelPeriod(instruments: tuple, signals: tuple, forward_returns: tuple, adv_notional: tuple = ())`.

---

### Task 1: HypothesisSpace

**Files:**
- Create: `services/intelligence/search_space.py`
- Test: `tests/intelligence/test_search_space.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) HypothesisSpace(factors: tuple[str,...], operators: tuple[str,...], windows: tuple[int,...])`; methods `to_definition(factor, operator, window) -> AlphaDefinition`, `arms() -> list[tuple[str,str]]`, `arms_of(hypothesis) -> tuple[tuple[str,str], tuple[str,str], tuple[str,str]]`, `hypotheses() -> list[tuple[str,str,int]]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/intelligence/test_search_space.py
import pytest
from services.intelligence.search_space import HypothesisSpace


def _space():
    return HypothesisSpace(factors=("mom", "rev"), operators=("zscore", "rank"), windows=(20, 60))


def test_grid_is_full_product_sorted():
    hs = _space()
    assert len(hs.hypotheses()) == 2 * 2 * 2
    assert hs.hypotheses() == sorted(hs.hypotheses())


def test_to_definition_encodes_params_and_distinct_hashes():
    hs = _space()
    d1 = hs.to_definition("mom", "zscore", 20)
    d2 = hs.to_definition("mom", "zscore", 60)
    assert d1.alpha_id == "h_zscore_mom_20"
    assert d1.factor_ids == ("mom",) and d1.params == {"operator": "zscore", "window": "20"}
    assert d1.definition_hash != d2.definition_hash      # distinct trials


def test_arms_of_returns_one_arm_per_kind():
    hs = _space()
    arms = hs.arms_of(("mom", "zscore", 20))
    assert arms == (("factor", "mom"), ("operator", "zscore"), ("window", "20"))
    assert set(arms) <= set(hs.arms())


def test_empty_dimension_fails_closed():
    with pytest.raises(ValueError):
        HypothesisSpace(factors=(), operators=("zscore",), windows=(20,))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/intelligence/test_search_space.py -v`
Expected: FAIL with `ModuleNotFoundError: services.intelligence.search_space`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/intelligence/search_space.py
"""Parameterized alpha-hypothesis space (factors x operators x windows).

Each hypothesis maps to an AlphaDefinition (operator/window encoded in params, so
distinct hypotheses get distinct definition_hashes and honest trial counting). An
"arm" is a single feature bucket; a hypothesis touches one factor / operator /
window arm. Pure data + mapping, no search logic."""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from quantos.contracts.alpha import AlphaDefinition


@dataclass(frozen=True)
class HypothesisSpace:
    factors: tuple
    operators: tuple
    windows: tuple

    def __post_init__(self):
        if not self.factors or not self.operators or not self.windows:
            raise ValueError("HypothesisSpace dimensions must all be non-empty")

    def to_definition(self, factor: str, operator: str, window: int) -> AlphaDefinition:
        return AlphaDefinition(
            alpha_id=f"h_{operator}_{factor}_{window}",
            name=f"H({operator}:{factor}:{window})",
            factor_ids=(factor,),
            params={"operator": operator, "window": str(window)},
        )

    def arms(self) -> list:
        out = [("factor", f) for f in self.factors]
        out += [("operator", o) for o in self.operators]
        out += [("window", str(w)) for w in self.windows]
        return out

    def arms_of(self, hypothesis) -> tuple:
        factor, operator, window = hypothesis
        return (("factor", factor), ("operator", operator), ("window", str(window)))

    def hypotheses(self) -> list:
        return sorted(itertools.product(self.factors, self.operators, self.windows))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/intelligence/test_search_space.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/intelligence/search_space.py tests/intelligence/test_search_space.py
git commit -m "feat(intelligence): HypothesisSpace — parameterized factor/operator/window grid"
```

---

### Task 2: UCBSearch (the learning bandit)

**Files:**
- Create: `services/intelligence/search_strategy.py`
- Test: `tests/intelligence/test_search_strategy.py`

**Interfaces:**
- Consumes: `HypothesisSpace` (Task 1).
- Produces: `class UCBSearch:` `__init__(self, space, *, c=math.sqrt(2))`; `propose(self, batch_size) -> list[tuple]`; `update(self, hypothesis, reward) -> None`; `arm_means(self) -> dict[tuple,float]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/intelligence/test_search_strategy.py
import pytest
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.search_strategy import UCBSearch


def _space():
    return HypothesisSpace(factors=("a", "b"), operators=("op",), windows=(10, 20))


def test_proposes_distinct_and_marks_proposed():
    s = UCBSearch(_space())
    first = s.propose(2)
    assert len(first) == 2 and len(set(first)) == 2
    second = s.propose(2)
    assert set(first).isdisjoint(second)                 # never re-proposes
    assert s.propose(99) == []                           # grid (4) exhausted


def test_update_raises_arm_mean_and_strategy_favors_good_arm():
    s = UCBSearch(_space())
    # reward hypotheses on window 20 high, window 10 low
    for hyp in s.space.hypotheses():
        s.update(hyp, 1.0 if hyp[2] == 20 else 0.0)
    assert s.arm_means()[("window", "20")] > s.arm_means()[("window", "10")]


def test_is_deterministic():
    a = UCBSearch(_space()).propose(4)
    b = UCBSearch(_space()).propose(4)
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/intelligence/test_search_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError: services.intelligence.search_strategy`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/intelligence/search_strategy.py
"""UCB1 multi-armed bandit over hypothesis-space arms — the learning component.

Each arm (a factor / operator / window bucket) tracks (pulls, reward_sum). A
hypothesis is scored by the SUM of its three arms' UCB1 values; propose() returns
the top unproposed hypotheses by that score (deterministic tie-break by the
hypothesis tuple). update() feeds an evaluation reward back into the touched arms,
so later proposals concentrate on buckets that have scored well. No RNG."""
from __future__ import annotations

import math


class UCBSearch:
    def __init__(self, space, *, c: float = math.sqrt(2)) -> None:
        self.space = space
        self.c = c
        self.pulls: dict = {}
        self.reward_sum: dict = {}
        self.n_evaluated: int = 0
        self.proposed: set = set()

    def _ucb(self, arm) -> float:
        n = self.pulls.get(arm, 0)
        if n == 0:
            return math.inf                              # force first exploration of every arm
        mean = self.reward_sum[arm] / n
        return mean + self.c * math.sqrt(math.log(max(self.n_evaluated, 1)) / n)

    def _score(self, hypothesis) -> float:
        arms = self.space.arms_of(hypothesis)
        # inf + finite = inf; cap so several unpulled arms still order deterministically
        return sum(self._ucb(a) for a in arms)

    def propose(self, batch_size: int) -> list:
        pool = [h for h in self.space.hypotheses() if h not in self.proposed]
        # sort by score desc, then by the hypothesis tuple asc (deterministic)
        pool.sort(key=lambda h: (-self._score(h), h))
        chosen = pool[:max(0, batch_size)]
        self.proposed.update(chosen)
        return chosen

    def update(self, hypothesis, reward: float) -> None:
        for arm in self.space.arms_of(hypothesis):
            self.pulls[arm] = self.pulls.get(arm, 0) + 1
            self.reward_sum[arm] = self.reward_sum.get(arm, 0.0) + reward
        self.n_evaluated += 1

    def arm_means(self) -> dict:
        return {a: self.reward_sum[a] / self.pulls[a] for a in self.pulls}
```

Note on the `_score` sort key: `-self._score(h)` is `-inf` when any arm is unpulled, so all-unexplored hypotheses sort first (every arm gets explored early), with the tuple tie-break giving a stable order. Python's `sort` is stable and `float('inf')` comparisons are well-defined.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/intelligence/test_search_strategy.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add services/intelligence/search_strategy.py tests/intelligence/test_search_strategy.py
git commit -m "feat(intelligence): UCB1 bandit search strategy over hypothesis arms"
```

---

### Task 3: reward

**Files:**
- Create: `services/intelligence/reward.py`
- Test: `tests/intelligence/test_reward.py`

**Interfaces:**
- Consumes: `AlphaEvaluation` (contract).
- Produces: `evaluation_reward(ev) -> float` in `[0,1]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/intelligence/test_reward.py
import pytest
from quantos.contracts.alpha import AlphaEvaluation
from quantos.contracts.research import ValidationVerdict
from services.intelligence.reward import evaluation_reward


def _ev(ds=0.0, ns=0.0, verdict=ValidationVerdict.REQUIRES_HUMAN_REVIEW):
    return AlphaEvaluation(alpha_id="x", verdict=verdict, deflated_sharpe=ds, net_sharpe=ns)


def test_reward_is_bounded_unit_interval():
    assert 0.0 <= evaluation_reward(_ev(ds=1.0, ns=10.0, verdict=ValidationVerdict.PASS)) <= 1.0
    assert 0.0 <= evaluation_reward(_ev(ds=0.0, ns=-10.0)) <= 1.0


def test_reward_is_monotone_in_quality_signals():
    assert evaluation_reward(_ev(ds=0.8)) > evaluation_reward(_ev(ds=0.2))
    assert evaluation_reward(_ev(ns=1.0)) > evaluation_reward(_ev(ns=-1.0))


def test_pass_beats_warn_beats_fail_for_equal_metrics():
    p = evaluation_reward(_ev(ds=0.5, ns=0.5, verdict=ValidationVerdict.PASS))
    w = evaluation_reward(_ev(ds=0.5, ns=0.5, verdict=ValidationVerdict.WARN))
    f = evaluation_reward(_ev(ds=0.5, ns=0.5, verdict=ValidationVerdict.FAIL))
    assert p > w > f


def test_none_evaluation_is_zero():
    assert evaluation_reward(None) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/intelligence/test_reward.py -v`
Expected: FAIL with `ModuleNotFoundError: services.intelligence.reward`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/intelligence/reward.py
"""Map an AlphaEvaluation to a scalar reward in [0,1] for the search bandit.

Monotonic in the robust quality signals (deflated Sharpe, net Sharpe) with a
verdict bonus, so the bandit learns from a continuous signal even when verdicts
are REQUIRES_HUMAN_REVIEW (the common case before evidence is supplied). A None
evaluation (an errored candidate) scores 0."""
from __future__ import annotations

from quantos.contracts.research import ValidationVerdict

_VERDICT_BONUS = {ValidationVerdict.PASS: 1.0, ValidationVerdict.WARN: 0.5}


def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def evaluation_reward(ev) -> float:
    if ev is None:
        return 0.0
    bonus = _VERDICT_BONUS.get(ev.verdict, 0.0)
    return _clamp01(
        0.5 * _clamp01(ev.deflated_sharpe)
        + 0.35 * _clamp01((ev.net_sharpe + 1.0) / 2.0)
        + 0.15 * bonus
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/intelligence/test_reward.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/intelligence/reward.py tests/intelligence/test_reward.py
git commit -m "feat(intelligence): evaluation -> bounded reward for the search bandit"
```

---

### Task 4: GuidedSearchLoop + additive ResearchAgentLoop field

**Files:**
- Modify: `services/intelligence/agent_loop.py:60` (add `"evaluation": ev` to the success report entry)
- Create: `services/intelligence/guided_search.py`
- Test: `tests/intelligence/test_guided_search.py`

**Interfaces:**
- Consumes: `HypothesisSpace`, `UCBSearch`, `evaluation_reward`, `ResearchAgentLoop`, `assert_action_allowed`/`AgentAction.PROPOSE`.
- Produces: `class GuidedSearchLoop:` `__init__(self, space, *, strategy=None, inner_loop=None)`; `search(self, rounds, batch_size, periods_provider, *, timestamp=0) -> dict` with keys `history`, `arm_means`, `human_review_queue`, `report`.

- [ ] **Step 1: Make the additive ResearchAgentLoop change**

In `services/intelligence/agent_loop.py`, change line 60 from:

```python
                report[defn.alpha_id] = {"verdict": ev.verdict.value, "state": self._state_of(defn.alpha_id)}
```

to:

```python
                report[defn.alpha_id] = {"verdict": ev.verdict.value, "state": self._state_of(defn.alpha_id),
                                         "evaluation": ev}
```

(The error branch is unchanged — it has no `evaluation` key, so `.get("evaluation")` returns None downstream. No control-flow change.)

- [ ] **Step 2: Write the failing test**

```python
# tests/intelligence/test_guided_search.py
import math
import pytest
from quantos.contracts.research import AlphaState
from services.alpha.panel_backtest import PanelPeriod
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.guided_search import GuidedSearchLoop

GOOD_WINDOW = 60
BAD_WINDOW = 10


def _space():
    return HypothesisSpace(factors=("a", "b"), operators=("zscore", "rank"),
                           windows=(BAD_WINDOW, GOOD_WINDOW))


def _provider(defn):
    """Synthetic periods where the GOOD window aligns signal with forward return
    (positive net Sharpe) and others anti-align (negative). Deterministic, with
    per-period variation so the Sharpe is finite."""
    w = int(defn.params["window"])
    edge = 0.012 if w == GOOD_WINDOW else -0.006
    n_inst, n_periods = 12, 14
    insts = tuple(f"i{k}" for k in range(n_inst))
    mid = (n_inst - 1) / 2.0
    periods = []
    for t in range(n_periods):
        signals = tuple(float(k) for k in range(n_inst))
        fwd = tuple(edge * (k - mid) + 0.001 * (((k * 3 + t * 7) % 5) - 2) for k in range(n_inst))
        periods.append(PanelPeriod(instruments=insts, signals=signals, forward_returns=fwd))
    return periods


def test_bandit_learns_good_window_outscores_bad():
    loop = GuidedSearchLoop(_space())
    out = loop.search(rounds=4, batch_size=2, periods_provider=_provider)
    means = out["arm_means"]
    assert means[("window", str(GOOD_WINDOW))] > means[("window", str(BAD_WINDOW))]


def test_search_never_reaches_approved():
    loop = GuidedSearchLoop(_space())
    loop.search(rounds=4, batch_size=2, periods_provider=_provider)
    cat = loop.inner_loop.catalog
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())


def test_search_is_deterministic():
    a = GuidedSearchLoop(_space()).search(rounds=4, batch_size=2, periods_provider=_provider)
    b = GuidedSearchLoop(_space()).search(rounds=4, batch_size=2, periods_provider=_provider)
    assert a["arm_means"] == b["arm_means"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/intelligence/test_guided_search.py -v`
Expected: FAIL with `ModuleNotFoundError: services.intelligence.guided_search`.

- [ ] **Step 4: Write minimal implementation**

```python
# services/intelligence/guided_search.py
"""Guided research search: a UCB1 bandit proposes alpha hypotheses, the UNCHANGED
guardrailed ResearchAgentLoop evaluates + advances them (so the agent still can
never reach APPROVED), and the evaluation reward is fed back so later rounds
concentrate on the best feature buckets. Pure-Python, deterministic; adds NO new
authority — proposing is the existing PROPOSE agent action."""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from .agent_loop import ResearchAgentLoop
from .guardrails import AgentAction, assert_action_allowed
from .reward import evaluation_reward
from .search_space import HypothesisSpace
from .search_strategy import UCBSearch


class GuidedSearchLoop:
    def __init__(self, space: HypothesisSpace, *, strategy=None, inner_loop=None) -> None:
        self.space = space
        self.strategy = strategy or UCBSearch(space)
        self.inner_loop = inner_loop or ResearchAgentLoop()

    def search(self, rounds: int, batch_size: int, periods_provider: Callable,
               *, timestamp: int = 0) -> dict:
        history, review_queue, report = [], [], {}
        for _ in range(rounds):
            assert_action_allowed(self.inner_loop.actor, AgentAction.PROPOSE)
            hyps = self.strategy.propose(batch_size)
            if not hyps:
                break                                    # grid exhausted -> stop (fail-soft)
            defns = {h: self.space.to_definition(*h) for h in hyps}
            candidates = [(defns[h], periods_provider(defns[h])) for h in hyps]
            out = self.inner_loop.run(candidates, timestamp=timestamp)
            report.update(out["report"])
            review_queue.extend(out["human_review_queue"])
            for h in hyps:
                entry = out["report"].get(defns[h].alpha_id, {})
                reward = evaluation_reward(entry.get("evaluation"))
                self.strategy.update(h, reward)
                history.append({"hypothesis": h, "reward": reward,
                                "state": entry.get("state")})
        return {"history": history, "arm_means": self.strategy.arm_means(),
                "human_review_queue": review_queue, "report": report}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/intelligence/test_guided_search.py -v`
Expected: PASS (3 passed). If `test_bandit_learns_good_window_outscores_bad` is flat, confirm `_provider`'s `edge` signs produce a positive net panel return for GOOD and negative for BAD (the long-short panel goes long the high-signal names; aligned forward returns -> positive net Sharpe -> higher reward).

- [ ] **Step 6: Run the existing intelligence tests (containment + import boundary must stay green)**

Run: `PYTHONPATH=. python -m pytest tests/intelligence -v`
Expected: PASS — including `test_agent_loop.py` (additive `evaluation` field does not break the verdict/state/error assertions) and `test_intelligence_import_boundary.py` (no live surface).

- [ ] **Step 7: Run the full gate**

Run: `python scripts/validate.py`
Expected: `VALIDATE PASSED (fail-closed gate green).` (count up by ~14). Scanner clean.

- [ ] **Step 8: Commit**

```bash
git add services/intelligence/agent_loop.py services/intelligence/guided_search.py tests/intelligence/test_guided_search.py
git commit -m "feat(intelligence): guided search loop — UCB1 proposes, guardrailed loop evaluates, bandit learns"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 HypothesisSpace → Task 1. ✓
- §3.2 UCBSearch (propose/update/arm_means, UCB inf-explore, deterministic) → Task 2. ✓
- §3.3 reward → Task 3. ✓
- §3.4 GuidedSearchLoop + additive evaluation field → Task 4. ✓
- §5 no new authority (PROPOSE guard) → Task 4 `search` asserts PROPOSE; no new AgentAction. ✓
- §5 containment (never APPROVED) → Task 4 `test_search_never_reaches_approved`. ✓
- §5 import boundary preserved → Task 4 Step 6 runs the existing boundary test. ✓
- §5 determinism → Tasks 2,4 determinism tests. ✓
- §5 fail-soft on exhausted grid → Task 2 (`propose`→[]) + Task 4 (`break`). ✓
- §6 learning test via synthetic provider → Task 4. ✓

**2. Placeholder scan:** Every code step is complete; no TBD/TODO. ✓

**3. Type consistency:** `HypothesisSpace.to_definition(factor, operator, window)` / `arms_of(hyp)->3 arms` / `arms()` consistent Tasks 1↔2↔4. `UCBSearch(space)`, `.propose(batch)->list`, `.update(hyp,reward)`, `.arm_means()->dict`, `.space` attribute used by tests — all defined in Task 2. `evaluation_reward(ev)->float` consistent Task 3↔4. `GuidedSearchLoop(space).inner_loop.catalog` used in the containment test and provided by `__init__`. Window arm key is `("window", str(w))` everywhere (space.arms / arms_of / the learning assertion). `ResearchAgentLoop.run` report entry gains `"evaluation"` (Task 4 Step 1) which Task 4's loop reads via `.get("evaluation")`. ✓

Implementer note: the synthetic `_provider` must give the GOOD window a positive net panel Sharpe and others negative — the long-short panel longs the top-signal names, so forward returns rising with the signal (`edge>0`) yield positive net return. Per-period jitter (`0.001*(...)`) keeps the Sharpe finite (non-zero variance).
