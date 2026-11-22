"""MCTS/UCT search over the alpha formula-tree space.

A smarter navigator than blind generational GP: grow a search tree where each node is a
candidate alpha (genome) and UCT (Upper Confidence bound for Trees) balances EXPLOITING
high-reward branches against EXPLORING fresh refinements — so a sufficient set of effective
alphas is found with FEWER evaluations, which also lightens the multiple-testing burden
(fewer distinct trials -> a smaller Deflated-Sharpe haircut).

Fully deterministic: argmax-UCT with a repr tie-break, refinements enumerated in a fixed order,
no RNG and no wall-clock. Fail-closed on non-finite exploration constants, rewards, or invalid
iteration counts.
"""
from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

from .reward import evaluation_reward

try:
    from services.research.ports.persistence import TrialPersistenceError
except Exception:  # pragma: no cover
    class TrialPersistenceError(RuntimeError):  # type: ignore[no-redef]
        pass

try:
    from services.validation.selection_authority import (
        LEGACY_INVALIDATION_CODE,
        require_diagnostic_selection_seam,
    )
except Exception:  # pragma: no cover
    LEGACY_INVALIDATION_CODE = "INVALIDATED_PERP_LEDGER_V1"

    def require_diagnostic_selection_seam(name: str, *, diagnostic_only: bool = False) -> None:
        if not diagnostic_only:
            raise PermissionError(f"{name} requires diagnostic_only=True")

try:
    from .agent_loop import ResearchAgentLoop
    from .evidence import provider_leakage_evidence
    from .guardrails import AgentAction, assert_action_allowed
    from .evolutionary_search import genome_to_definition, mutate, DEFAULT_VOCAB
except Exception:  # pragma: no cover
    ResearchAgentLoop = None  # type: ignore[assignment]
    provider_leakage_evidence = None  # type: ignore[assignment]
    AgentAction = None  # type: ignore[assignment]
    assert_action_allowed = None  # type: ignore[assignment]
    genome_to_definition = None  # type: ignore[assignment]
    mutate = None  # type: ignore[assignment]
    DEFAULT_VOCAB = ()


class _Node:
    __slots__ = ("genome", "value", "visits", "parent", "children", "untried")

    def __init__(self, genome, parent):
        self.genome = genome          # None for the virtual root
        self.value = 0.0              # best finite reward observed in this node's subtree
        self.visits = 0
        self.parent = parent
        self.children = []
        self.untried = None           # lazily computed list of refinement genomes


class MCTSAlphaSearch:
    def __init__(self, provider: Callable, *, inner_loop: Optional[object] = None,
                 vocab=DEFAULT_VOCAB, c: float = 1.4):
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or c < 0.0:
            raise ValueError(f"exploration constant c must be finite and >= 0, got {c!r}")
        self.provider = provider
        if inner_loop is not None:
            self.inner_loop = inner_loop
        elif ResearchAgentLoop is not None:
            self.inner_loop = ResearchAgentLoop()
        else:
            self.inner_loop = None
        self.vocab = vocab
        self.c = float(c)

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
        if not math.isfinite(r):
            r = 0.0
        evaluated[genome] = r
        return r

    def _refinements(self, genome, in_tree: set) -> list:
        try:
            neigh = mutate(genome, self.vocab)
        except Exception:
            neigh = []
        return [g for g in neigh if g not in in_tree]   # mutate already returns a sorted list

    def _uct(self, parent: _Node, child: _Node) -> float:
        if child.visits <= 0:
            return math.inf
        val = child.value if (isinstance(child.value, (int, float)) and not isinstance(child.value, bool) and math.isfinite(child.value)) else 0.0
        parent_visits = max(int(parent.visits), 1)
        return val + self.c * math.sqrt(math.log(parent_visits) / child.visits)

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
        child.value = r if math.isfinite(r) else 0.0
        node.children.append(child)
        return child

    @staticmethod
    def _backprop(node: _Node) -> None:
        raw_v = node.value
        v = raw_v if (isinstance(raw_v, (int, float)) and not isinstance(raw_v, bool) and math.isfinite(raw_v)) else 0.0
        while node is not None:
            node.visits += 1
            cur = node.value if (isinstance(node.value, (int, float)) and not isinstance(node.value, bool) and math.isfinite(node.value)) else 0.0
            if v > cur or not math.isfinite(node.value):
                node.value = max(v, cur)
            node = node.parent

    def run(self, seed_genomes: Sequence, *, iterations: int, timestamp: int = 0,
            diagnostic_only: bool = False) -> dict:
        require_diagnostic_selection_seam(
            "MCTSAlphaSearch.run", diagnostic_only=diagnostic_only
        )
        if not seed_genomes:
            raise ValueError("seed_genomes must be non-empty")
        if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
            raise ValueError(f"iterations must be an integer >= 1, got {iterations!r}")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ValueError(f"timestamp must be an integer, got {timestamp!r}")
        evaluated: dict = {}
        report: dict = {}
        in_tree: set = set()
        root = _Node(None, None)
        root.untried = list(dict.fromkeys(seed_genomes))     # seeds first (dedup, order preserved)
        for _ in range(iterations):
            if assert_action_allowed is not None and self.inner_loop is not None:
                assert_action_allowed(self.inner_loop.actor, AgentAction.PROPOSE)   # contained
            node = self._select(root)
            child = self._expand(node, evaluated, report, in_tree, timestamp)
            self._backprop(child if child is not None else node)
        best = sorted(evaluated.items(), key=lambda gr: (-gr[1], repr(gr[0])))
        return {"best": best, "report": report, "n_evaluated": len(evaluated),
                "diagnostic_only": True, "selection_authority": False,
                "invalidation_code": LEGACY_INVALIDATION_CODE}
