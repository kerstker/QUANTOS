"""Compatibility export for the canonical transaction cost model (Phase 4).

Costs are charged on the absolute traded notional:
  * fee      — commission (bps of notional),
  * slippage — execution shortfall (bps of notional),
  * impact   — optional square-root market impact, scaled by participation
               (trade size / average daily volume). Disabled by default
               (impact_coef=0) so it never silently changes existing results.
All research PnL is therefore net-of-cost. The value object now lives in the
shared contract leaf so portfolio code does not depend back on research.
"""

from quantos.contracts.cost import CostModel


__all__ = ["CostModel"]
