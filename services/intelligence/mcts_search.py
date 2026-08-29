"""MCTS/UCT search over the alpha formula-tree space.

A smarter navigator than blind generational GP: grow a search tree where each node is a
candidate alpha (genome) and UCT (Upper Confidence bound for Trees) balances EXPLOITING
high-reward branches against EXPLORING fresh refinements — so a sufficient set of effective
alphas is found with FEWER evaluations, which also lightens the multiple-testing burden
(fewer distinct trials -> a smaller Deflated-Sharpe haircut).

Reuses everything unchanged:
  - refinements = the existing deterministic `mutate` neighbours (works for P/F/X and GP 'T'),
  - evaluation = the UNCHANGED contained ResearchAgentLoop (a non-human agent can never reach
    APPROVED; each distinct genome registers as exactly ONE catalog trial),
  - reward = evaluation_reward.
Fully deterministic: argmax-UCT with a repr tie-break, refinements enumerated in a fixed order,
no RNG and no wall-clock. (Inspired by LLM-MCTS formulaic-alpha mining, arXiv 2505.11122 —
here the navigator is MCTS and the proposer stays the in-constraint deterministic mutate.)
"""
from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

from services.research.ports.persistence import TrialPersistenceError
from services.validation.selection_authority import (
    LEGACY_INVALIDATION_CODE,
    require_diagnostic_selection_seam,
)

from .agent_loop import ResearchAgentLoop
from .evidence import provider_leakage_evidence
from .guardrails import AgentAction, assert_action_allowed
from .reward import evaluation_reward
from .evolutionary_search import genome_to_definition, mutate, DEFAULT_VOCAB


class _Node:
    __slots__ = ("genome", "value", "visits", "parent", "children", "untried")

    def __init__(self, genome, parent):
        self.genome = genome          # None for the virtual root
        self.value = 0.0              # best reward observed in this node's subtree
        self.visits = 0
        self.parent = parent
        self.children = []
        self.untried = None           # lazily computed list of refinement genomes


class MCTSAlphaSearch:
    def __init__(self, provider: Callable, *, inner_loop: Optional[ResearchAgentLoop] = None,
                 vocab=DEFAULT_VOCAB, c: float = 1.4):
        self.provider = provider
        self.inner_loop = inner_loop or ResearchAgentLoop()
        self.vocab = vocab
        self.c = c

    # ---- evaluation through the UNCHANGED contained loop (memoized per genome) ----------
    def _evaluate(self, genome, evaluated: dict, report: dict, timestamp: int) -> float:
        if genome in evaluated:
            return evaluated[genome]                 # one trial per distinct genome
        try:
            defn = genome_to_definition(genome)
            periods = self.provider(defn)
        except TrialPersistenceError:
            raise
        except Exception:
            evaluated[genome] = 0.0                  # isolate: a bad genome scores 0
            return 0.0
        cands = [(defn, periods)]
        # 断点1 (方案A): provider-supplied real leakage_ok evidence -> evaluator gate (else {} = fail-closed).
        evidence = provider_leakage_evidence(self.provider, cands)
        out = self.inner_loop.run(
            cands,
            timestamp=timestamp,
            evidence=evidence,
            diagnostic_only=True,
        )
        report.update(out["report"])
        entry = out["report"].get(defn.alpha_id, {})
        r = evaluation_reward(entry.get("evaluation"))
        evaluated[genome] = r
        return r

    def _refinements(self, genome, in_tree: set) -> list:
        try:
            neigh = mutate(genome, self.vocab)
        except Exception:
            neigh = []
        return [g for g in neigh if g not in in_tree]   # mutate already returns a sorted list

    def _uct(self, parent: _Node, child: _Node) -> float:
        if child.visits == 0:
            return math.inf
        return child.value + self.c * math.sqrt(math.log(max(parent.visits, 1)) / child.visits)

    def _select(self, root: _Node) -> _Node:
        node = root
        while (node.untried is not None and not node.untried) and node.children:
            ordered = sorted(node.children, key=lambda ch: repr(ch.genome))   # deterministic tie-break
            node = max(ordered, key=lambda ch: self._uct(node, ch))
        return node

    def _expand(self, node: _Node, evaluated, report, in_tree: set, timestamp: int) -> Optional[_Node]:
        if node.untried is None:                     # lazily compute this node's refinements
            node.untried = [] if node.genome is None else self._refinements(node.genome, in_tree)
        if not node.untried:
            return None                              # nothing to expand (leaf) -> backprop its value
        g = node.untried.pop(0)                      # deterministic (sorted refinement list)
        in_tree.add(g)
        r = self._evaluate(g, evaluated, report, timestamp)
        child = _Node(g, node)
        child.value = r
        node.children.append(child)
        return child

    @staticmethod
    def _backprop(node: _Node) -> None:
        v = node.value
        while node is not None:
            node.visits += 1
            if v > node.value:
                node.value = v
            node = node.parent

    def run(self, seed_genomes: Sequence, *, iterations: int, timestamp: int = 0,
            diagnostic_only: bool = False) -> dict:
        require_diagnostic_selection_seam(
            "MCTSAlphaSearch.run", diagnostic_only=diagnostic_only
        )
        if not seed_genomes:
            raise ValueError("seed_genomes must be non-empty")
        if iterations < 1:
            raise ValueError("iterations must be >= 1")
        evaluated: dict = {}
        report: dict = {}
        in_tree: set = set()
        root = _Node(None, None)
        root.untried = list(dict.fromkeys(seed_genomes))     # seeds first (dedup, order preserved)

        for _ in range(iterations):
            assert_action_allowed(self.inner_loop.actor, AgentAction.PROPOSE)   # contained
            node = self._select(root)
            child = self._expand(node, evaluated, report, in_tree, timestamp)
            self._backprop(child if child is not None else node)

        best = sorted(evaluated.items(), key=lambda gr: (-gr[1], repr(gr[0])))
        return {"best": best, "report": report, "n_evaluated": len(evaluated),
                "diagnostic_only": True, "selection_authority": False,
                "invalidation_code": LEGACY_INVALIDATION_CODE}
