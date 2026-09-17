import pytest

from services.research.ai_factory import HOUR
from services.research.ai_factory_forward import forecast_document
from services.research.ai_factory_store import digest
from services.research.portfolio_target_intent import resolve_target
from services.research.profitability_forward_account import portfolio_decision


def inputs():
    slot = 32 * HOUR
    forecast = forecast_document(slot=slot, available=slot+1, issued=slot+2,
        activated=HOUR, model_hash='a'*64, input_hash='b'*64,
        features=[0., 0., 0., .001, 0.], prediction=.01, threshold_bps=0,
        decision_policy='DISCRETE_COST_RISK_V1')
    forecast['result_hash'] = digest(forecast)
    now = forecast['entry_ms']
    context = {'quantity': '0', 'mark': '100000', 'equity': '5000', 'observed_at_ms': now}
    funding = [{'time': h*HOUR, 'available_at_ms': h*HOUR+1, 'rate': '0'} for h in (8,16,24)]
    return forecast, context, funding, now


def test_online_intent_matches_existing_portfolio_policy_and_binds_inputs():
    row, context, funding, now = inputs()
    result = resolve_target(row, context, funding, model_hash='a'*64, now_ms=now, fee_bps=4, slip_bps=5)
    existing = portfolio_decision({now: row}, {'funding': funding}, 'FROZEN_MODEL', 4, 5)
    assert result['target_weight'] == existing(now, context) == .25
    assert result['forecast_sha256'] == row['result_hash']
    assert result['context_sha256'] == digest(context)
    assert result['submission_enabled'] is False
    assert result['economic_authority'] is False
    row.pop('result_hash')
    row['prediction'] = 0.
    row['result_hash'] = digest(row)
    context['quantity'] = '.0124'
    held = resolve_target(row, context, funding, model_hash='a'*64, now_ms=now, fee_bps=4, slip_bps=5)
    assert held['action'] == 'HOLD' and held['target_weight'] is None
    assert portfolio_decision({now: row}, {'funding': funding}, 'FROZEN_MODEL', 4, 5)(now, context) is None


def test_unavailable_stale_or_wrong_model_inputs_cannot_produce_intent():
    row, context, funding, now = inputs()
    for changes in ({'now_ms': now-1}, {'now_ms': now+HOUR}, {'model_hash': 'c'*64},
                    {'funding': [{**r, 'available_at_ms': now+1} for r in funding]},
                    {'context': {**context, 'observed_at_ms': now-60001}},
                    {'forecast': {**row, 'prediction': -.01}}):
        kwargs = dict(forecast=row, context=context, funding=funding,
                      model_hash='a'*64, now_ms=now, fee_bps=4, slip_bps=5)
        kwargs.update(changes)
        with pytest.raises(ValueError):
            resolve_target(**kwargs)
