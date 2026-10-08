"""Bounded AI proposal uses gold hypotheses and the original cost ledger."""
from copy import deepcopy
import json

import pytest

from tests.research.test_ai_factory_provider import Transport, LOCAL
from tests.tools.test_gold_cost_replay import inputs
from tools.ai_factory_provider import propose
from tools.gold_cost_replay import replay_costs


def test_model_sees_gold_hypotheses_and_choice_drives_original_cost_replay():
    from services.research.gold_factory_candidates import apply_candidate, candidates
    transport = Transport({'candidate_id': 0, 'reason': 'Test absorption at the registered zone'})
    receipt = propose('XAUUSDT cost-aware absorption experiment', list(range(6)), [],
                      candidate_catalog='GOLD_ABSORPTION_V1', environ=LOCAL, transport=transport)
    content = json.loads(transport.request['messages'][1]['content'])
    assert content['candidates'] == candidates()
    assert all(row['instrument_id'] == 'XAUUSDT' for row in content['candidates'])
    assert receipt['candidate_catalog'] == 'GOLD_ABSORPTION_V1'
    assert len(receipt['candidate_catalog_sha256']) == 64
    args = inputs()
    original = deepcopy(args)
    args['document'] = apply_candidate(args['document'], receipt['candidate_id'])
    result = replay_costs(**args)
    assert result['simulation']['reconciliation']['ending_equity'] == '1000.5733'
    assert result['authoritative'] is False
    assert original == inputs()


def test_candidate_cannot_change_market_data_plan_or_original_document():
    from services.research.gold_factory_candidates import apply_candidate
    args = inputs()
    original = deepcopy(args['document'])
    result = apply_candidate(original, 5)
    assert original == args['document']
    assert {k: v for k, v in result.items() if k != 'rule'} == {
        k: v for k, v in original.items() if k != 'rule'}
    assert result['rule'] == {**original['rule'], 'sell_threshold': '50', 'confirm_windows': 3}
    result['packets'].clear()
    assert original['packets']


@pytest.mark.parametrize('candidate_id', [True, -1, 6, '0'])
def test_candidate_identity_is_finite_and_typed(candidate_id):
    from services.research.gold_factory_candidates import apply_candidate
    with pytest.raises(ValueError):
        apply_candidate(inputs()['document'], candidate_id)


@pytest.mark.parametrize('options', [
    {'candidate_catalog': 'unregistered'},
    {'candidate_catalog': 'GOLD_ABSORPTION_V1', 'search_plan': {'version': 2}},
])
def test_catalog_conflicts_rejected_before_transport(options):
    transport = Transport({'candidate_id': 0, 'reason': 'test'})
    with pytest.raises(ValueError):
        propose('research', [0], [], environ=LOCAL, transport=transport, **options)
    assert transport.request is None


def test_gold_history_requires_scoped_evidence_before_transport():
    transport = Transport({'candidate_id': 1, 'reason': 'test'})
    with pytest.raises(ValueError):
        propose('research', [1], [{'candidate_id': 0, 'score': 1, 'status': 'PASSED'}],
                candidate_catalog='GOLD_ABSORPTION_V1', environ=LOCAL, transport=transport)
    assert transport.request is None


def test_gold_catalog_uses_existing_role_reservations_and_cache(tmp_path):
    from tools.ai_factory_collaboration import bounded_proposal
    calls = []
    def proposal(*args, **kwargs):
        transport = Transport({'candidate_id': 0, 'reason': 'test fixed gold hypothesis'})
        result = propose(*args, **kwargs, environ=LOCAL, transport=transport)
        calls.append(result)
        return result
    kwargs = dict(protocol='SINGLE_V1', output=tmp_path,
                  options={'candidate_catalog': 'GOLD_ABSORPTION_V1'}, propose_fn=proposal)
    result = bounded_proposal('gold research', list(range(6)), [], **kwargs)
    assert result['roles'][0]['candidate_catalog'] == 'GOLD_ABSORPTION_V1'
    assert result['economic_eligible'] is False
    assert bounded_proposal('gold research', list(range(6)), [], **kwargs) == result
    assert len(calls) == 1
    with pytest.raises(ValueError, match='identity changed'):
        bounded_proposal('gold research', list(range(6)), [],
                         **{**kwargs, 'options': {'candidate_catalog': 'HOURLY_MODELS'}})
    assert len(calls) == 1
