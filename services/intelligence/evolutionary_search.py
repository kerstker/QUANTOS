"""Evolutionary alpha search: breed new hypotheses (mutation + crossover) from
high-reward parents, evaluate them through the UNCHANGED guardrailed ResearchAgentLoop
(PROPOSE-only), keep elites. Crossover composes two atomic parents into a 2-leg
interaction (sub-project 1), so the agent CREATES factors beyond a fixed grid.

Containment: every candidate is evaluated by ResearchAgentLoop — the agent can never
reach APPROVED. Multiple-testing honesty: each distinct genome is a distinct
AlphaDefinition → a distinct trial in the catalog → the Deflated-Sharpe / var_sr /
BH-FDR machinery penalizes the amplified search. Deterministic (no RNG; enumerate
+ sort), fail-closed (per-genome evaluation errors are isolated to reward 0).
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition

from .agent_loop import ResearchAgentLoop
from .guardrails import AgentAction, assert_action_allowed
from .reward import evaluation_reward
from services.research.interaction_factors import interaction_definition, make_interaction_provider
from services.research.panel_builder import make_data_periods_provider
from services.research.fundamental_factors import make_fundamental_periods_provider
from services.research import gp_tree, gp_genome
from services.research.factor_set import novelty_vs_basis
from services.research.subtree_stats import structural_commonness, update_corpus

DEFAULT_VOCAB = {
    "operators": ("momentum", "ts_mean", "zscore", "volatility", "ts_rank",
                  "ema_ratio", "dist_from_high", "accel"),
    "windows": ("3", "5", "10"),
    "metrics": ("eps",),
    "modes": ("ratio_to_price", "raw"),
    "combines": ("sum", "product"),
    # GP expression-tree ("T") channel — additive; defaults keep flat P/F/X runs unchanged.
    "unary": gp_tree.UNARY,
    "binary": gp_tree.BINARY,
    "consts": gp_tree.CONST_VOCAB,
    "max_depth": gp_tree.MAX_DEPTH_DEFAULT,
    "max_nodes": gp_tree.MAX_NODES_DEFAULT,
    "parsimony_lambda": 0.0,   # bloat penalty on SEARCH reward only (0 -> off, honest eval unchanged)
    "parsimony_mu": 0.0,
    # incremental-discovery: scale a candidate's SEARCH reward by how much NEW information it
    # adds vs the already-discovered elite basis (0 -> off, recorded evaluation never touched).
    "redundancy_lambda": 0.0,
    # frequent-subtree avoidance: down-weight the SEARCH reward of a structurally COMMON tree
    # (its sub-expressions already recur) to force formula-shape diversity (0 -> off).
    "diversity_lambda": 0.0,
}


def _no_colon(*parts) -> None:
    # ':' is reserved (the interaction leg-id delimiter); forbid it in any id component
    # so atomic/interaction id namespaces can't be confused. NOTE: alpha_ids join
    # components with '_', and legitimate components contain '_' (e.g. operator
    # "ts_mean", mode "ratio_to_price"), so '_' is NOT forbidden. The DEFAULT vocab is
    # verified collision-free; with a CUSTOM vocab whose names collide under the '_'
    # join, the duplicate alpha_id is rejected by catalog.register and that genome is
    # dropped fail-closed (reward 0) — never merged into another's state (definition_hash
    # keys on params, which stay distinct).
    for p in parts:
        if ":" in str(p):
            raise ValueError(f"genome id component must not contain ':' : {p!r}")


def genome_to_definition(genome) -> AlphaDefinition:
    """Map a genome to an AlphaDefinition (distinct genome → distinct definition_hash
    → honest multiple-testing count)."""
    tag = genome[0]
    if tag == "P":
        _, op, window = genome
        _no_colon(op, window)
        return AlphaDefinition(alpha_id=f"h_{op}_{window}", name=f"H({op}:{window})",
                               factor_ids=("close",), params={"operator": op, "window": str(window)})
    if tag == "F":
        _, metric, mode = genome
        _no_colon(metric, mode)
        return AlphaDefinition(alpha_id=f"hf_{metric}_{mode}", name=f"HF({metric}:{mode})",
                               factor_ids=(metric,), params={"metric": metric, "mode": mode, "family": "fundamental"})
    if tag == "X":
        _, leg_a, leg_b, combine = genome
        return interaction_definition(leg_a, leg_b, combine)
    if tag == "T":
        _, tree = genome
        return gp_tree.tree_definition(tree)        # canonicalize + ':' guard + bound inside
    raise ValueError(f"unknown genome tag: {tag!r}")


def make_evolution_provider(price_store, fund_store, instruments: Sequence[str], *,
                            event_times: Sequence[int], fwd_horizon: int, universe=None) -> Callable:
    """A master provider dispatching by AlphaDefinition family: interaction / fundamental / price."""
    price = make_data_periods_provider(price_store, instruments, event_times=event_times,
                                       fwd_horizon=fwd_horizon, universe=universe)
    inter = make_interaction_provider(price_store, fund_store, instruments, event_times=event_times,
                                      fwd_horizon=fwd_horizon, universe=universe)
    fund = (make_fundamental_periods_provider(fund_store, price_store, instruments, event_times=event_times,
                                              fwd_horizon=fwd_horizon, universe=universe)
            if fund_store is not None else None)
    tree = gp_tree.make_tree_provider(price_store, instruments, event_times=event_times,
                                      fwd_horizon=fwd_horizon, universe=universe)

    def provider(defn):
        params = defn.params
        if params.get("family") == "gp_tree":
            return tree(defn)
        if params.get("family") == "interaction":
            return inter(defn)
        if "metric" in params:
            if fund is None:
                raise ValueError("fundamental genome requires a fund_store")
            return fund(defn)
        return price(defn)
    return provider


# ---------- genetic operators (deterministic) -----------------------------------

def _atom_leg(genome):
    """The interaction-leg form of an atomic genome; None for a non-atomic (X) genome."""
    tag = genome[0]
    if tag == "P":
        return ("price", genome[1], genome[2])
    if tag == "F":
        return ("fund", genome[1], genome[2])
    return None


def _mutate_leg(leg, vocab) -> list:
    kind = leg[0]
    if kind == "price":
        _, op, w = leg
        return ([("price", o2, w) for o2 in vocab["operators"] if o2 != op]
                + [("price", op, w2) for w2 in vocab["windows"] if w2 != w])
    _, m, mode = leg                                       # fund
    return ([("fund", m, mode2) for mode2 in vocab["modes"] if mode2 != mode]
            + [("fund", m2, mode) for m2 in vocab["metrics"] if m2 != m])


def mutate(genome, vocab=DEFAULT_VOCAB) -> list:
    tag = genome[0]
    out = []
    if tag == "P":
        _, op, w = genome
        out += [("P", o2, w) for o2 in vocab["operators"] if o2 != op]
        out += [("P", op, w2) for w2 in vocab["windows"] if w2 != w]
    elif tag == "F":
        _, m, mode = genome
        out += [("F", m, mode2) for mode2 in vocab["modes"] if mode2 != mode]
        out += [("F", m2, mode) for m2 in vocab["metrics"] if m2 != m]
    elif tag == "X":
        _, leg_a, leg_b, combine = genome
        out += [("X", leg_a, leg_b, c2) for c2 in vocab["combines"] if c2 != combine]
        out += [("X", la2, leg_b, combine) for la2 in _mutate_leg(leg_a, vocab)]
        out += [("X", leg_a, lb2, combine) for lb2 in _mutate_leg(leg_b, vocab)]
    elif tag == "T":
        md, mn = vocab.get("max_depth", gp_tree.MAX_DEPTH_DEFAULT), vocab.get("max_nodes", gp_tree.MAX_NODES_DEFAULT)
        out += [("T", t) for t in gp_genome.point_and_subtree_mutate(genome[1], vocab, max_depth=md, max_nodes=mn)]
    else:
        raise ValueError(f"unknown genome tag: {tag!r}")
    return sorted(set(out))


def _tree_of(genome):
    """A genome as a GP tree: 'T' yields its tree; an atomic 'P' lifts to a depth-0 leaf
    (so the atomic population can SEED trees); anything else -> None (does not cross as a tree)."""
    if genome[0] == "T":
        return genome[1]
    if genome[0] == "P":
        return ("leaf", genome[1], genome[2])
    return None


def crossover(g1, g2, vocab=DEFAULT_VOCAB) -> list:
    """When at least one parent is a GP tree ('T'), breed tree offspring by subtree splice
    (the other parent may be a 'T' or a liftable atomic 'P'). When NEITHER parent is a tree,
    preserve the original behavior: compose two ATOMIC parents into 2-leg 'X' interactions
    (so existing flat P/F/X search is byte-identical)."""
    if g1[0] == "T" or g2[0] == "T":
        t1, t2 = _tree_of(g1), _tree_of(g2)
        if t1 is None or t2 is None:
            return []
        md, mn = vocab.get("max_depth", gp_tree.MAX_DEPTH_DEFAULT), vocab.get("max_nodes", gp_tree.MAX_NODES_DEFAULT)
        return sorted({("T", t) for t in gp_genome.crossover(t1, t2, vocab, max_depth=md, max_nodes=mn)})
    a, b = _atom_leg(g1), _atom_leg(g2)
    if a is None or b is None:
        return []
    return sorted({("X", a, b, c) for c in vocab["combines"]})


class EvolutionarySearchLoop:
    def __init__(self, provider: Callable, *, inner_loop: Optional[ResearchAgentLoop] = None, vocab=DEFAULT_VOCAB):
        self.provider = provider
        self.inner_loop = inner_loop or ResearchAgentLoop()
        self.vocab = vocab

    def _evaluate(self, genomes, evaluated: dict, history: list, report: dict, timestamp: int,
                  basis: Optional[dict] = None, corpus: Optional[dict] = None) -> None:
        candidates, ok = [], []
        for g in genomes:
            if g in evaluated:
                continue
            try:
                defn = genome_to_definition(g)
                periods = self.provider(defn)
                candidates.append((defn, periods))
                ok.append((g, defn))
            except Exception as exc:                       # isolate: errored genome scores 0, continue
                evaluated[g] = 0.0
                history.append({"genome": g, "reward": 0.0, "error": type(exc).__name__})
        if not candidates:
            return
        out = self.inner_loop.run(candidates, timestamp=timestamp)
        report.update(out["report"])
        red_lambda = self.vocab.get("redundancy_lambda", 0.0)
        div_lambda = self.vocab.get("diversity_lambda", 0.0)
        for g, defn in ok:
            entry = out["report"].get(defn.alpha_id, {})
            r = evaluation_reward(entry.get("evaluation"))
            if g[0] == "T":
                # parsimony pressure on the SEARCH-selection reward ONLY (never the recorded
                # AlphaEvaluation): bias the population toward smaller trees, opt-in via vocab.
                lam, mu = self.vocab.get("parsimony_lambda", 0.0), self.vocab.get("parsimony_mu", 0.0)
                if lam or mu:
                    r = max(0.0, r - gp_genome.parsimony_penalty(g[1], lam, mu))
            if red_lambda > 0 and basis:
                # incremental discovery: down-weight the SEARCH reward of a candidate that is
                # redundant with the already-discovered elite basis, so the search seeks NEW
                # information instead of re-finding the same alpha. Recorded eval untouched.
                cand_returns = self.inner_loop.catalog.returns(defn.alpha_id)
                if len(cand_returns) >= 2:
                    nov = novelty_vs_basis(cand_returns, basis)
                    if nov is not None:
                        r = r * (1.0 - red_lambda * (1.0 - nov))
            if div_lambda > 0 and corpus is not None and g[0] == "T":
                # frequent-subtree avoidance: penalize a structurally COMMON tree, then record
                # its sub-shapes so later identical shapes are penalized more. Search reward only.
                r = r * (1.0 - div_lambda * structural_commonness(g[1], corpus))
                update_corpus(corpus, g[1])
            evaluated[g] = r
            history.append({"genome": g, "reward": r, "state": entry.get("state")})

    def _elite_basis(self, elites) -> dict:
        """Return series of the current elites (the already-discovered good factors) — the
        basis a new candidate's novelty is measured against. Empty unless redundancy is on."""
        if self.vocab.get("redundancy_lambda", 0.0) <= 0:
            return {}
        cat = self.inner_loop.catalog
        basis = {}
        for g in elites:
            try:
                aid = genome_to_definition(g).alpha_id
            except Exception:
                continue
            rs = cat.returns(aid)
            if len(rs) >= 2:
                basis[aid] = rs
        return basis

    def run(self, seed_genomes: Sequence, *, generations: int, population_size: int,
            elite_k: int, timestamp: int = 0) -> dict:
        if not seed_genomes:
            raise ValueError("seed_genomes must be non-empty")
        if generations < 0 or population_size < 1 or elite_k < 1:
            raise ValueError("generations>=0, population_size>=1, elite_k>=1 required")
        evaluated: dict = {}
        history: list = []
        report: dict = {}
        corpus: Optional[dict] = {} if self.vocab.get("diversity_lambda", 0.0) > 0 else None
        population = sorted(set(seed_genomes))[:population_size]
        self._evaluate(population, evaluated, history, report, timestamp, corpus=corpus)

        for _ in range(generations):
            assert_action_allowed(self.inner_loop.actor, AgentAction.PROPOSE)   # contained
            elites = [g for g, _r in sorted(((g, evaluated[g]) for g in population),
                                            key=lambda gr: (-gr[1], gr[0]))[:elite_k]]
            offspring: set = set()
            for g in elites:
                offspring.update(mutate(g, self.vocab))
            for i in range(len(elites)):
                for j in range(i + 1, len(elites)):
                    offspring.update(crossover(elites[i], elites[j], self.vocab))
            fresh = [g for g in sorted(offspring) if g not in evaluated][:population_size]
            self._evaluate(fresh, evaluated, history, report, timestamp,
                           basis=self._elite_basis(elites), corpus=corpus)
            pool = set(elites) | set(fresh) | set(population)
            population = [g for g, _r in sorted(((g, evaluated.get(g, 0.0)) for g in pool),
                                                key=lambda gr: (-gr[1], gr[0]))[:population_size]]

        best = sorted(evaluated.items(), key=lambda gr: (-gr[1], gr[0]))
        return {"best": best, "history": history, "report": report, "n_evaluated": len(evaluated)}
