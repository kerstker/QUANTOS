from copy import deepcopy
import json
import pytest

from services.research.gold_portfolio_memory import build_memory, validate_memory
from tests.research.test_ai_factory_provider import LOCAL, Transport
from tools.ai_factory_provider import propose


def completed():
    return [dict(attempt=i, result_hash=str(i)*64, reviewed_at_ms=i*100,
        report={'portfolio':dict(trial_id='a'*64,result_hash='b'*64,
            account=dict(net_equity_change='-1.23456789',fees='0.12345678',funding='-0.5',
                         max_observed_drawdown='0.00123456789',reconciliation_residual='0'),
            order_count=2,filled_order_count=2,open_position_count=0,
            loss_control={'status':'ACTIVE'})},review={'status':'INDEPENDENT_AUTHORITY_UNCONFIGURED'}) for i in range(1,8)]


def test_all_seven_portfolio_windows_reach_model_without_truncating_decimals():
    memory=build_memory(completed(),next_attempt=8,as_of_ms=1000)
    transport=Transport({'candidate_id':0,'reason':'Compare costs'})
    result=propose('Evaluate next unseen gold window',[0,1,2],[],environ=LOCAL,transport=transport,
        candidate_catalog='GOLD_BAR_CANDIDATES_V1',research_memory=memory)
    body=json.loads(transport.request['messages'][1]['content'])
    received=body['prior_technical_experiments']
    assert [r['attempt'] for r in received['records']]==list(range(1,8))
    assert received['records'][0]['portfolio']['account']['net_equity_change']=='-1.23456789'
    assert result['research_memory_hash']==memory['result_hash']
    assert received['economic_eligible'] is False


def test_missing_portfolio_is_unknown_not_zero():
    rows=completed();rows[0]['report']={}
    memory=build_memory(rows,next_attempt=8,as_of_ms=1000)
    assert memory['records'][0]['portfolio'] is None
    assert build_memory([dict(rows[0])],next_attempt=8,as_of_ms=1000) is None


@pytest.mark.parametrize('damage',['future','duplicate','current_attempt','hash','cash'])
def test_invalid_feedback_rejected_before_model(damage):
    rows=completed()
    if damage=='future':rows[0]['reviewed_at_ms']=1001
    if damage=='duplicate':rows[1]['attempt']=1
    if damage=='current_attempt':rows[-1]['attempt']=8
    if damage=='cash':rows[0]['report']['portfolio']['account']['reconciliation_residual']='1'
    if damage=='hash':
        value=build_memory(rows,next_attempt=8,as_of_ms=1000);value['records'][0]['portfolio']['account']['fees']='99'
        with pytest.raises(ValueError):validate_memory(value)
    else:
        with pytest.raises(ValueError):build_memory(rows,next_attempt=8,as_of_ms=1000)


def test_netting_diagnostics_reach_model_and_absence_stays_unknown():
    from services.research.gold_bar_diagnostics import target_diagnostics
    rows = completed()
    diagnostic = target_diagnostics([dict(timestamp=1, desired=dict(
        channel='0.01', trend='0', reversion='-0.01'), net_target='0', actual_quantity='0')])
    rows[0]['report']['portfolio']['target_diagnostics'] = diagnostic
    memory = build_memory(rows, next_attempt=8, as_of_ms=1000)
    transport = Transport({'candidate_id': 0, 'reason': 'Compare netting'})
    propose('Compare future window', [0, 1, 2], [], environ=LOCAL, transport=transport,
            candidate_catalog='GOLD_BAR_CANDIDATES_V1', research_memory=memory)
    records = json.loads(transport.request['messages'][1]['content'])['prior_technical_experiments']['records']
    assert records[0]['portfolio']['target_diagnostics'] == diagnostic
    assert 'target_diagnostics' not in records[1]['portfolio']
    rows[0]['report']['portfolio']['target_diagnostics']['observation_count'] = 2
    with pytest.raises(ValueError):
        build_memory(rows, next_attempt=8, as_of_ms=1000)


def test_imported_memory_survives_new_windows_without_resetting_loss_stop():
    from services.research.gold_portfolio_memory import continue_memory
    rows = completed()
    prior = build_memory(rows[:1], next_attempt=2, as_of_ms=200)
    result = continue_memory(prior, rows[2:3], next_attempt=4, as_of_ms=500)
    assert [r['attempt'] for r in result['records']] == [1, 3]
    assert result['records'][0] == prior['records'][0]
    with pytest.raises(ValueError):
        continue_memory(prior, rows[:1], next_attempt=4, as_of_ms=500)
    rows[0]['report']['portfolio']['loss_control']['status'] = 'STOPPED_FLAT'
    stopped = build_memory(rows[:1], next_attempt=2, as_of_ms=200)
    with pytest.raises(ValueError, match='IMPORTED_MEMORY_LOSS_STOP'):
        continue_memory(stopped, [], next_attempt=3, as_of_ms=500)
