"""Versioned private observations preserve venue and availability time domains."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.research.binance_order_receipts import normalize_order_receipts
from services.research.passive_receipt_calibration import validate_calibration
from tests.research.test_binance_order_receipts import v2_spec, ACTIONS, HASH, receipt
from tests.research.test_demo_clock_bracket import bracket

ACTIONS_V3=tuple(replace(a,timestamp=a.timestamp+9930) for a in ACTIONS)


def native_v3(offset=1000):
    raw=v2_spec()['raw_observed']; raw['schema']='BINANCE_USDM_ORDER_RECEIPTS_V3'
    raw['coverage'].update(start_ms=10030,end_ms=10290)
    raw['clock_bracket']=bracket(offset)
    for i,r in enumerate(raw['messages']):
        p=json.loads(r['raw_utf8'])
        p['E']+=9930+offset; p['T']+=9930+offset; p['o']['T']+=9930+offset
        raw['messages'][i]=receipt(p,r['received_at_ms']+9930)
    return raw


@pytest.mark.parametrize('offset',[-1000,0,1000])
def test_versioned_offset_retains_raw_prices_fees_ids_and_both_clocks(offset):
    raw=native_v3(offset); original=deepcopy(raw)
    observed,evidence=normalize_order_receipts(raw,ACTIONS_V3,'BTCUSDC',HASH)
    assert raw==original and evidence['trades_count']==2
    assert evidence['reported_quote_commission']=='0.0198'
    assert evidence['trade_rows'][0]['venue_trade_ms']==10051+offset
    assert evidence['trade_rows'][0]['received_at_ms']==10061
    assert observed['clock_bracket']==raw['clock_bracket']
    assert evidence['clock']['exact_latency_ms'] is None
    assert evidence['clock']['available_at_ms']==10320
    assert evidence['clock']['source_verified'] is False
    assert evidence['clock']['causality_status']=='COMPATIBILITY_ONLY_NOT_CAUSAL_CERTIFICATION'


def test_zero_offset_legacy_remains_byte_shape_compatible_and_requires_no_probe():
    raw=v2_spec()['raw_observed']
    observed,evidence=normalize_order_receipts(raw,ACTIONS,'BTCUSDC',HASH)
    assert 'clock' not in evidence and 'clock_bracket' not in observed
    raw['clock_bracket']=bracket()
    with pytest.raises(ValueError): normalize_order_receipts(raw,ACTIONS,'BTCUSDC',HASH)


@pytest.mark.parametrize('change',['missing','future_local','impossible_past','impossible_future','venue_backwards',
    'stale_bracket','missing_option','different_option','wrong_fee','foreign_intent'])
def test_offset_contract_does_not_hide_invalid_receipts(change):
    raw=native_v3()
    if change=='missing': del raw['clock_bracket']
    elif change=='stale_bracket': raw['clock_bracket']['before']['received_at_ms']=10040
    elif change=='missing_option': del raw['bindings'][0]['reduce_only']
    elif change=='different_option': raw['bindings'][0]['time_in_force']='GTX'
    else:
        p=json.loads(raw['messages'][1]['raw_utf8']); r=raw['messages'][1]['received_at_ms']
        if change=='future_local': r=10300
        elif change in ('impossible_past','impossible_future'):
            delta=-3000 if change=='impossible_past' else 3000
            p['E']+=delta; p['T']+=delta; p['o']['T']+=delta
        elif change=='venue_backwards': p['T']=p['o']['T']-1
        elif change=='wrong_fee': p['o']['n']={'unsupported':1}
        else: p['o']['c']='someone-else'
        raw['messages'][1]=receipt(p,r)
    with pytest.raises(ValueError): normalize_order_receipts(raw,ACTIONS_V3,'BTCUSDC',HASH)


def test_clock_bracket_cannot_silently_enter_legacy_exact_latency_calibration():
    from types import SimpleNamespace
    raw=native_v3()
    # Native V3 is a retrospective interval observation. An exact-clock
    # calibration path must not silently reinterpret these raw venue times.
    with pytest.raises(ValueError,match='interval|clock|GTX'):
        validate_calibration({'schema':'PASSIVE_BINANCE_ORDER_RECEIPTS_V1','cutoff_ms':10150,'raw_observed':raw},
            SimpleNamespace(symbol='BTCUSDC',start_ms=10030,end_ms=10290),ACTIONS_V3,HASH)


@pytest.mark.parametrize('offset',[-1000,1000])
def test_clocked_journal_replays_exact_cash_once_across_restart(tmp_path,offset):
    from services.research.demo_execution_journal import DemoExecutionJournal,replay_report
    from services.research.ai_factory_store import digest
    from tests.research.test_demo_execution_journal import manifest
    frozen=manifest(); raw=native_v3(offset); messages=raw.pop('messages')
    frozen['actions'][0]['timestamp']=10030; raw['action_hash']=digest(frozen['actions'])
    frozen['native']=raw; path=tmp_path/'two-clock.sqlite3'
    with DemoExecutionJournal(path,manifest=frozen) as journal:
        assert journal.append(messages)==3
        result=replay_report(journal)
        assert result['projection_status']=='REPLAYED_TECHNICAL_ONLY'
        assert result['reconciliation']['fee']=='0.0198'
        assert result['position_quantity']=='1'
        assert result['clock']['available_at_ms']==10320
        assert result['recent_trades'][0]['received_at_ms']==10061
    with DemoExecutionJournal(path) as journal:
        assert journal.append(messages)==0
        assert replay_report(journal)==result
    assert frozen['native']['clock_bracket']==bracket(offset)


def test_unordered_state_cannot_bypass_journal_validation_using_a_clock_bracket(tmp_path):
    from services.research.demo_execution_journal import DemoExecutionJournal
    from services.research.ai_factory_store import digest
    from tests.research.test_demo_execution_journal import manifest
    frozen=manifest(); raw=native_v3(); messages=raw.pop('messages')
    frozen['actions'][0]['timestamp']=10030; raw['action_hash']=digest(frozen['actions']); frozen['native']=raw
    p=json.loads(messages[0]['raw_utf8']); p['E']=11201; p['T']=11200; p['o']['T']=11200
    messages.append(receipt(p,10230))
    with DemoExecutionJournal(tmp_path/'bad-state.sqlite3',manifest=frozen) as journal:
        with pytest.raises(ValueError): journal.append(messages)
        assert journal.read()[0]==[]


def test_uncertain_venue_utc_capacity_day_is_not_assigned_from_local_day(tmp_path):
    from services.research.demo_execution_journal import DemoExecutionJournal
    from services.research.ai_factory_store import digest
    from tests.research.test_demo_execution_journal import manifest
    frozen=manifest(); raw=native_v3(); raw.pop('messages')
    shift=86400000-11000
    for p in raw['clock_bracket']['before'],raw['clock_bracket']['after']:
        for key in ('requested_at_ms','received_at_ms','server_time_ms'): p[key]+=shift
    for key in ('start_ms','end_ms'): raw['coverage'][key]+=shift
    frozen['actions'][0]['timestamp']=10030+shift; raw['action_hash']=digest(frozen['actions']); frozen['native']=raw
    with pytest.raises(ValueError,match='CAPACITY.*CLOCK|CLOCK.*CAPACITY'):
        DemoExecutionJournal(tmp_path/'uncertain-day.sqlite3',manifest=frozen)


def clocked_opening(offset=1000,completed_at_ms=9000):
    from tests.research.test_demo_opening_journal import opening_manifest,recapture
    from services.research.ai_factory_store import digest
    frozen=opening_manifest(); cap=frozen['opening']['capture']; shift=completed_at_ms-cap['completed_at_ms']
    for k in ('observed_at_ms','completed_at_ms'): cap[k]+=shift
    for r in cap['reads']:
        r['started_at_ms']+=shift; r['received_at_ms']+=shift
    recapture(frozen)
    raw=native_v3(offset); messages=raw.pop('messages')
    raw['clock_bracket']['before'].update(requested_at_ms=completed_at_ms-100,
        received_at_ms=completed_at_ms-80,server_time_ms=completed_at_ms-90+offset)
    frozen['actions'][0]['timestamp']=10030; raw['action_hash']=digest(frozen['actions']); frozen['native']=raw
    return frozen,messages


@pytest.mark.parametrize('offset',[-1000,1000])
def test_opening_keeps_local_fact_and_separate_conservative_venue_cutoff(tmp_path,offset):
    from services.research.demo_execution_journal import DemoExecutionJournal,replay_report
    raw,messages=clocked_opening(offset)
    original=deepcopy(raw)
    with DemoExecutionJournal(tmp_path/'opening.sqlite3',manifest=raw) as journal:
        journal.append(messages)
        result=replay_report(journal)
    assert raw==original and result['projection_status']=='REPLAYED_TECHNICAL_ONLY'
    assert result['position_quantity']=='3' and result['reconciliation']['fee']=='0.0198'
    opening=result['opening']
    assert opening['opening_at_ms']==9000
    assert opening['clock_boundary']['local_observed_at_ms']==9000
    assert opening['clock_boundary']['venue_not_before_ms']==9000+offset+511
    assert opening['clock_boundary']['policy']=='CONSERVATIVE_VENUE_UPPER_BOUND_NOT_OBSERVATION_V1'


def test_trade_ambiguous_with_opening_is_retained_but_does_not_replay_cash(tmp_path):
    from services.research.demo_execution_journal import DemoExecutionJournal,replay_report
    raw,messages=clocked_opening(completed_at_ms=9990)
    with DemoExecutionJournal(tmp_path/'ambiguous-opening.sqlite3',manifest=raw) as journal:
        journal.append(messages); result=replay_report(journal)
    assert result['journal_records']==3 and result['projection_status']=='UNRESOLVED'
    assert result['projection_reason']=='OPENING_CLOCK_BOUNDARY_UNRESOLVED'
    assert result['reconciliation'] is None
