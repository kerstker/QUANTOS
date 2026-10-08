"""Resolve a preissued portfolio forecast at decision time; research intent only.

Uses the same cost/risk policy as portfolio forward replay. This receipt is not
an execution task, validation attestation, or an approval. Asset/account binding
and authorized task construction must still be supplied by the execution bridge.
"""
import math
import re

from services.research.ai_factory import HOUR
from services.research.ai_factory_decision import decide
from services.research.ai_factory_store import digest
from services.research.profitability_forward import forward_summary


def resolve_target(forecast, context, funding, *, model_hash, now_ms, fee_bps, slip_bps):
    """Use only a current preissued forecast and already available observations."""
    plain = {k: v for k, v in forecast.items() if k != 'result_hash'}
    if (forecast.get('result_hash') != digest(plain)
            or not isinstance(model_hash, str) or not re.fullmatch('[0-9a-f]{64}', model_hash)
            or forecast.get('model_hash') != model_hash):
        raise ValueError('unchanged forecast and frozen model required')
    forward_summary([forecast], [], as_of_ms=now_ms)
    slot = forecast['slot_ms']
    entry = forecast['entry_ms']
    if (slot % HOUR or entry != slot+300000 or not entry <= now_ms < entry+HOUR
            or forecast['issued_at_ms'] >= entry or forecast['exit_ms'] != entry+4*HOUR
            or forecast.get('decision_policy') != 'DISCRETE_COST_RISK_V1'
            or forecast.get('target_weight') is not None
            or forecast.get('unit') != 'simple_return' or forecast.get('horizon_ms') != 4*HOUR
            or not isinstance(forecast.get('input_hash'), str)
            or not re.fullmatch('[0-9a-f]{64}', forecast['input_hash'])
            or forecast.get('authoritative') is not False
            or forecast.get('account_pnl_computed') is not False):
        raise ValueError('current discrete portfolio forecast required')
    features = forecast.get('features')
    if (not isinstance(features, list) or len(features) != 5
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in features)
            or features[3] < 0):
        raise ValueError('finite forecast risk features required')
    observed = context.get('observed_at_ms')
    if type(observed) is not int or not now_ms-60000 <= observed <= now_ms:
        raise ValueError('current account observation required')
    equity, mark, quantity = (float(context[k]) for k in ('equity', 'mark', 'quantity'))
    if not all(math.isfinite(v) for v in (equity, mark, quantity)) or equity <= 0 or mark <= 0:
        raise ValueError('positive observed account equity and mark required')
    if not isinstance(funding, list) or not 3 <= len(funding) <= 9:
        raise ValueError('three to nine settled funding observations required')
    previous = -1
    rates = []
    for row in funding:
        at, available = row['time'], row['available_at_ms']
        if (type(at) is not int or type(available) is not int
                or not previous < at < now_ms or not at <= available <= now_ms):
            raise ValueError('ordered available settled funding required')
        rate = float(row['rate'])
        if not math.isfinite(rate):
            raise ValueError('finite funding required')
        previous = at
        rates.append(rate)
    # Match the original replay's numpy mean exactly, including float rounding.
    import numpy as np
    decision = decide(prediction=forecast['prediction'], variance=features[3]**2*4,
        current=quantity*mark/equity, fee_bps=fee_bps, slip_bps=slip_bps,
        funding=float(np.asarray(rates).mean())*.5)
    result = {'schema': 'QUANTOS_PORTFOLIO_TARGET_INTENT_V1', 'observed_at_ms': now_ms,
        'valid_until_ms': entry+HOUR, 'model_hash': model_hash,
        'forecast_sha256': forecast['result_hash'], 'context_sha256': digest(context),
        'funding_sha256': digest(funding), 'decision': decision,
        'target_weight': decision['target'], 'action': 'HOLD' if decision['target'] is None else 'TARGET',
        'submission_enabled': False, 'economic_authority': False}
    return {**result, 'result_hash': digest(result)}
