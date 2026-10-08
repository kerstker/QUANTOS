"""Synthetic account response fixtures; none authenticate a venue account."""
from copy import deepcopy
from decimal import Decimal as D, localcontext
from fractions import Fraction
import hashlib
import json

import pytest

from tests.research.test_passive_execution_study import account_source
from tests.execution.test_passive_account_environment import BASE
from services.research.passive_execution_study import prepare_input, prepare_account, run_profile


def receipt(kind, payload, received):
    raw = json.dumps(payload, separators=(',', ':'))
    return {'kind': kind, 'requested_at_ms': received-10, 'received_at_ms': received,
            'raw_utf8': raw, 'response_sha256': hashlib.sha256(raw.encode()).hexdigest()}


def terms_source():
    data = account_source()
    del data['account']['fee_rate']
    payloads = {
        'commission': {'symbol': 'BTCUSDC', 'makerCommissionRate': '0.0002',
                       'takerCommissionRate': '0.001', 'rpiCommissionRate': '0.00005'},
        'configuration': [{'symbol': 'BTCUSDC', 'marginType': 'CROSSED',
            'isAutoAddMargin': False, 'leverage': 3, 'maxNotionalValue': '180'}],
        'brackets': {'symbol': 'BTCUSDC', 'brackets': [
            {'bracket': 1, 'initialLeverage': 10, 'notionalFloor': 0,
             'notionalCap': 100, 'maintMarginRatio': 0.01, 'cum': 0},
            {'bracket': 2, 'initialLeverage': 5, 'notionalFloor': 100,
             'notionalCap': 200, 'maintMarginRatio': 0.02, 'cum': 1}]},
    }
    data['account']['account_terms'] = {
        'schema': 'BINANCE_USDM_ACCOUNT_TERMS_V1', 'symbol': 'BTCUSDC',
        'account_ref_sha256': 'a'*64, 'provenance': 'SYNTHETIC_TEST_FIXTURE',
        'before': {k: receipt(k, v, BASE+900) for k, v in payloads.items()},
        'after': {k: receipt(k, v, BASE+1300) for k, v in payloads.items()},
    }
    return data


def prepared(data):
    tape, actions = prepare_input(data)
    return tape, actions, prepare_account(data, tape)


def edit(data, kind, transform, *, both=True):
    for phase in ('before', 'after') if both else ('before',):
        r = data['account']['account_terms'][phase][kind]
        payload = json.loads(r['raw_utf8']); transform(payload)
        data['account']['account_terms'][phase][kind] = receipt(kind, payload, r['received_at_ms'])


def test_selected_leverage_exact_fee_roles_and_finite_cap_reach_shared_ledger():
    data = terms_source(); original = deepcopy(data)
    tape, actions, account = prepared(data)
    spec = account['contract_rules'][0].spec
    assert data == original
    assert spec.new_risk_notional_cap == D('180')
    assert spec.margin_brackets[-1].notional_cap == D('200')
    assert Fraction(spec.margin_brackets[0].initial_margin_rate) == Fraction(333333333333333333333334, 10**24)
    assert spec.margin_brackets[0].maintenance_margin_rate == D('0.01')
    result = run_profile(tape, actions, 0, account=account, release_identity={'synthetic': True})
    assert result['reconciliation']['fee'] == '0.00198'
    assert result['reconciliation']['ending_equity'] == '1000.09802'
    assert result['actual_venue_fees'] is None
    facts = account['manifest']['account_terms']
    assert facts['selected_leverage'] == 3 and facts['source_verified'] is False
    assert facts['actual_paid_fees'] is None and facts['provenance'] == 'SYNTHETIC_TEST_FIXTURE'
    assert facts['rpi_fee_rate_not_applied'] == '0.00005'


def test_low_precision_caller_cannot_change_margin_rate_or_input_identity():
    a = prepared(terms_source())[2]
    with localcontext() as context:
        context.prec = 6
        b = prepared(terms_source())[2]
        assert context.prec == 6
    assert a['manifest'] == b['manifest']
    assert a['contract_rules'] == b['contract_rules']


def test_selected_leverage_caps_new_risk_before_ineligible_higher_tier():
    data = terms_source()
    edit(data, 'configuration', lambda p: p[0].update(leverage=7))
    assert prepared(data)[2]['contract_rules'][0].spec.new_risk_notional_cap == D('100')


@pytest.mark.parametrize('change', ['hash', 'late', 'short_after', 'changed', 'quote', 'coefficient',
    'isolated', 'auto_add', 'overleveraged', 'gap', 'continuity', 'credentials', 'ambiguous_fee',
    'duplicate', 'negative_fee', 'unknown_origin', 'unknown_field'])
def test_incomplete_changed_or_unsupported_account_conditions_are_rejected(change):
    data = terms_source(); terms = data['account']['account_terms']
    if change == 'hash': terms['before']['commission']['response_sha256'] = '0'*64
    elif change == 'late':
        r = terms['before']['commission']; r.update(requested_at_ms=BASE+1060, received_at_ms=BASE+1070)
    elif change == 'short_after':
        r = terms['after']['commission']; r.update(requested_at_ms=BASE+1090, received_at_ms=BASE+1100)
    elif change == 'changed': edit(data, 'configuration', lambda p: p[0].update(leverage=4), both=False)
    elif change == 'quote': terms['symbol'] = 'BTCUSDT'
    elif change == 'coefficient': edit(data, 'brackets', lambda p: p.update(notionalCoef=1.5))
    elif change == 'isolated': edit(data, 'configuration', lambda p: p[0].update(marginType='ISOLATED'))
    elif change == 'auto_add': edit(data, 'configuration', lambda p: p[0].update(isAutoAddMargin=True))
    elif change == 'overleveraged': edit(data, 'configuration', lambda p: p[0].update(leverage=11))
    elif change == 'gap': edit(data, 'brackets', lambda p: p['brackets'][1].update(notionalFloor=101))
    elif change == 'continuity': edit(data, 'brackets', lambda p: p['brackets'][1].update(cum=2))
    elif change == 'credentials': terms['before']['commission']['headers'] = {'unwanted': 'not-a-real-key'}
    elif change == 'ambiguous_fee': data['account']['fee_rate'] = '0.001'
    elif change == 'negative_fee': edit(data, 'commission', lambda p: p.update(makerCommissionRate='-0.001'))
    elif change == 'unknown_origin': terms['provenance'] = 'AUTHENTICATED'
    elif change == 'unknown_field': edit(data, 'configuration', lambda p: p[0].update(unrecognized=1))
    else:
        r = terms['before']['commission']; r['raw_utf8'] = r['raw_utf8'].replace('"symbol":', '"symbol":"BTCUSDC","symbol":')
        r['response_sha256'] = hashlib.sha256(r['raw_utf8'].encode()).hexdigest()
    with pytest.raises(ValueError): prepared(data)


def test_unused_taker_change_and_source_receipt_change_still_change_replay_identity():
    a = terms_source(); b = deepcopy(a)
    edit(b, 'commission', lambda p: p.update(takerCommissionRate='0.002'))
    results = []
    for data in (a, b):
        tape, actions, account = prepared(data)
        results.append(run_profile(tape, actions, 0, account=account, release_identity={'fixture': 1}))
    assert results[0]['reconciliation'] == results[1]['reconciliation']
    assert results[0]['input_hash'] != results[1]['input_hash']


def test_raw_receipt_provenance_change_keeps_amounts_but_changes_bound_environment():
    a = terms_source(); b = deepcopy(a)
    b['account']['account_terms']['provenance'] = 'IMPORTED_UNVERIFIED'
    a, b = prepared(a)[2], prepared(b)[2]
    assert a['contract_rules'][0].spec == b['contract_rules'][0].spec
    assert a['manifest']['environment_hash'] != b['manifest']['environment_hash']
    assert b['manifest']['account_terms']['source_verified'] is False


def test_quote_currency_is_bound_through_both_public_and_account_inputs():
    data = terms_source()
    # Rebuild all synthetic raw response hashes after changing the quote.
    def convert(value):
        if isinstance(value, dict):
            result = {k:convert(v) for k,v in value.items()}
            if 'response_sha256' in result:
                result['response_sha256'] = hashlib.sha256(result['raw_utf8'].encode()).hexdigest()
            return result
        if isinstance(value, list): return [convert(v) for v in value]
        if isinstance(value, str): return value.replace('USDC','USDT').replace('usdc','usdt')
        return value
    account = prepared(convert(data))[2]
    assert account['manifest']['account_terms']['quote'] == 'USDT'
    assert account['contract_rules'][0].spec.margin_asset == 'USDT'


def test_account_terms_flow_through_immutable_input_versions_and_workflow(tmp_path, monkeypatch):
    from services.research.ai_factory_store import FactoryStore
    from tools import profitability_input_workflow as workflow
    from services.console.api.profitability_views import profitability_payload
    data = terms_source(); original = deepcopy(data)
    with FactoryStore(tmp_path/'data/cache/ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'})
        store.update(1,'COMPLETED',{})
        store.add_profitability_input(1,'passive',data,'synthetic-account-terms')
    monkeypatch.setattr(workflow,'runtime_state_root',lambda:tmp_path)
    result = workflow.run_input_version({'attempt':1,'input_kind':4,'revision':1})
    assert result['p5_queue']['account_environment']['account_terms']['selected_leverage'] == 3
    assert profitability_payload(tmp_path,1)['p5_queue']['trials_attempted'] == 3
    assert data == original
