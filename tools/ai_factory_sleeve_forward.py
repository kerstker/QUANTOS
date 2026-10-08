"""Frozen raw component forecasts and an isolated one-way forward cost account."""
from tools.ai_factory_forward import _now
from tools.btc_forward_observation import _get
from tools.ai_factory_portfolio_forward import run_observation as _run


def run_observation(params,*,root=None,clock=_now,get=_get):
    return _run(params,root=root,clock=clock,get=get,sleeves=True)
