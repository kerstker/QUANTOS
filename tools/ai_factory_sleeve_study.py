"""Research-only independent strategy decisions on one reconciled net account."""
from services.research.ai_factory_sleeves import CONSTRUCTION
from tools.ai_factory_portfolio import _run_portfolio


def run_sleeves(params):
    return _run_portfolio(params,construction=CONSTRUCTION)
