"""Synthetic gold batches retain one cash ledger across partial reduction."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json

import pytest

from quantos.contracts.binance_demo_account import DemoAccountCapture
from services.research.ai_factory_store import digest
from services.research.demo_cash_chain import project_cash_chain
from services.research.demo_execution_journal import DemoExecutionJournal,observations,replay_ledger
from tests.research.test_demo_cash_chain import fixture as btc_fixture
from tests.research.test_binance_order_receipts import receipt


def inputs(tmp_path):
    batches,cash=btc_fixture(tmp_path)
    # Transform synthetic fixture inputs, never stored real evidence.
    batches,cash=json.loads(json.dumps([batches,cash]).replace('BTCUSDC','XAUUSDT').replace('USDC','USDT'))
    for i,(manifest,messages) in enumerate(batches):
        manifest['schema']='DEMO_GOLD_EXECUTION_REPLAY_V1' if i==0 else 'DEMO_GOLD_EXECUTION_REPLAY_V2'
        manifest['native']['schema']='BINANCE_USDM_GOLD_ORDER_RECEIPTS_V2'
        for binding in manifest['native']['bindings']:
            binding.setdefault('time_in_force','GTX');binding.setdefault('reduce_only',False)
        for n,row in enumerate(messages):
            messages[n]=receipt(json.loads(row['raw_utf8']),row['received_at_ms'])
        if i:
            opening=manifest['opening']
            capture=DemoAccountCapture.model_validate(opening['capture'])
            opening['capture_hash']=hashlib.sha256(capture.model_dump_json().encode()).hexdigest()
    first,messages=batches[0]
    with DemoExecutionJournal(tmp_path/'gold-prefix.db',manifest=first) as journal:
        journal.append(messages)
        from services.research.demo_order_recovery import build_query_plan
        assert build_query_plan(journal).symbol=='XAUUSDT'
        evidence=observations(journal.manifest,journal.read()[0],journal.parsed)['native_observations']
        state=replay_ledger(journal.manifest,journal.parsed,evidence,cash_events=[]).state_as_canonical_dict()
    batches[1][0]['opening']['basis'][0]['source_sha256']=digest(state)
    return batches,cash


@pytest.mark.parametrize('offset',[-1000,1000])
def test_gold_clocked_continuation_seeds_venue_bound_not_local_capture_time(tmp_path,offset):
    from services.research.demo_execution_journal import prepare
    from services.research.demo_opening_projection import prepare_opening
    batches,_=inputs(tmp_path);manifest=batches[1][0]
    native=manifest['native'];native['schema']='BINANCE_USDM_GOLD_ORDER_RECEIPTS_V3'
    completed=manifest['opening']['capture']['completed_at_ms']
    start=completed-1000;end=native['coverage']['end_ms']+100
    native['clock_bracket']={'schema':'BINANCE_USDM_CLOCK_BRACKET_V1','provenance':'SYNTHETIC_TEST_FIXTURE',
        'before':dict(requested_at_ms=start,received_at_ms=start+20,server_time_ms=start+10+offset,response_sha256='a'*64,source_sha256='b'*64),
        'after':dict(requested_at_ms=end,received_at_ms=end+20,server_time_ms=end+10+offset,response_sha256='c'*64,source_sha256='d'*64)}
    _,seed,report=prepare_opening(manifest,prepare(manifest)[0])
    assert report['status']=='DECLARED_INPUTS_READY'
    bound=report['clock_boundary']
    assert bound['local_observed_at_ms']==completed
    assert bound['venue_not_before_ms']>=completed+offset
    assert seed.timestamp==bound['venue_not_before_ms'] and seed.timestamp!=completed
    assert not bound['source_verified']


@pytest.mark.parametrize('damage',[None,'wallet','basis','missing_commission'])
def test_gold_cash_chain_preserves_basis_funding_and_external_cash(tmp_path,damage):
    batches,cash=inputs(tmp_path)
    if damage=='wallet':
        next(r for r in cash['reads'] if r['kind']=='wallet_after')['data'][0]['wallet']='9999'
    elif damage=='basis':batches[1][0]['opening']['basis'][0]['source_sha256']='f'*64
    elif damage=='missing_commission':
        for read in cash['reads']:
            if read['kind']=='income':read['data']=[r for r in read['data'] if r['tranId']!='105']
    with ExitStack() as stack:
        journals=[stack.enter_context(DemoExecutionJournal(tmp_path/f'gold-{i}.db',manifest=m)) for i,(m,_) in enumerate(batches)]
        for journal,(_,messages) in zip(journals,batches):journal.append(messages)
        before=[deepcopy(j.read()) for j in journals]
        result=project_cash_chain(journals,cash)
        assert [j.read() for j in journals]==before
    with ExitStack() as stack:
        journals=[stack.enter_context(DemoExecutionJournal(tmp_path/f'gold-{i}.db',read_only=True)) for i in range(2)]
        assert project_cash_chain(journals,cash)==result
    if damage=='wallet':
        assert result['status']=='DIFFERENCE_FOUND' and result['wallet_residual']!='0'
    elif damage:
        assert result['status']=='UNRESOLVED' and result['issues']
    else:
        assert result['status']=='MATCHED_REPORTED_INPUTS',result['issues']
        assert result['canonical_wallet']=='1104.1714' and result['wallet_residual']=='0'
        assert result['positions']['XAUUSDT']['quantity']=='0.6'
        assert result['positions']['XAUUSDT']['entry_notional']=='59.4'
        assert result['reconciliation']['initial_equity']=='1000'
        assert result['reconciliation']['fee']=='0.0286'
        assert result['reconciliation']['funding']=='-0.2'
        assert result['reconciliation']['external_cash_flow']=='100'
        assert result['continuations'][0]['status']=='MATCHED_CANONICAL_PREFIX'
    assert result['strategy_pnl'] is None and not result['authoritative'] and not result['coverage_verified']


@pytest.mark.parametrize('damage',['held_flat','legacy_manifest','legacy_receipts','wrong_margin'])
def test_gold_flat_technical_origin_cannot_reinterpret_other_scopes(tmp_path,damage):
    from services.research.demo_execution_journal import prepare
    batches,_=inputs(tmp_path)
    manifest=deepcopy(batches[0][0])
    if damage=='held_flat':manifest['initial_position']='1'
    elif damage=='legacy_manifest':manifest['schema']='DEMO_EXECUTION_REPLAY_V1'
    elif damage=='legacy_receipts':manifest['native']['schema']='BINANCE_USDM_ORDER_RECEIPTS_V2'
    else:manifest['contract']['margin_asset']='USDC'
    from services.execution.perpetual_ledger_v2 import UnsupportedContract
    with pytest.raises(UnsupportedContract if damage=='wrong_margin' else ValueError):prepare(manifest)
