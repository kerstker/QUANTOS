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


class CombinedHypothesisSpace:
    """Unified space over price factors AND fundamental value factors, searched by a
    single UCB1 bandit so the two families compete. Hypotheses are family-tagged,
    all-string tuples (JSON-safe for bandit snapshot):
      price       ("P", factor, operator, str(window))   -> 3 arms
      fundamental ("F", metric, mode)                     -> 2 arms
    Distinct alpha_ids/definition_hashes keep multiple-testing counting honest.
    """

    def __init__(self, price_space: HypothesisSpace, metrics: tuple, modes: tuple) -> None:
        if not metrics or not modes:
            raise ValueError("CombinedHypothesisSpace metrics and modes must be non-empty")
        self.price_space = price_space
        self.metrics = tuple(metrics)
        self.modes = tuple(modes)

    def hypotheses(self) -> list:
        price = [("P", f, o, str(w)) for (f, o, w) in self.price_space.hypotheses()]
        fund = [("F", m, mode) for m in self.metrics for mode in self.modes]
        return sorted(price + fund)

    def arms_of(self, hypothesis) -> tuple:
        if hypothesis[0] == "P":
            _, factor, operator, window = hypothesis
            return (("factor", factor), ("operator", operator), ("window", str(window)))
        _, metric, mode = hypothesis
        return (("metric", metric), ("mode", mode))

    def to_definition(self, *hypothesis) -> AlphaDefinition:
        if hypothesis[0] == "P":
            _, factor, operator, window = hypothesis
            return AlphaDefinition(
                alpha_id=f"h_{operator}_{factor}_{window}",
                name=f"H({operator}:{factor}:{window})",
                factor_ids=(factor,),
                params={"operator": operator, "window": str(window)},
            )
        _, metric, mode = hypothesis
        return AlphaDefinition(
            alpha_id=f"hf_{metric}_{mode}",
            name=f"HF({metric}:{mode})",
            factor_ids=(metric,),
            params={"metric": metric, "mode": mode, "family": "fundamental"},
        )
