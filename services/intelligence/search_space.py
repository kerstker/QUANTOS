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
