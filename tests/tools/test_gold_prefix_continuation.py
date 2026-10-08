"""Fresh original quote prefixes must continue, rather than reset, an account."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from tests.execution.test_perpetual_public_replay import START
from tests.tools.test_gold_native_prefix import frozen
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def pieces(tmp_path, monkeypatch, source=None):
    from tools import gold_native_prefix as bridge
    from tools.gold_session_prefix import snapshot

    _, bars, capture, source = frozen(tmp_path, monkeypatch,source)
    monkeypatch.setattr(bridge, 'source_identity', lambda: source)
    captures = []
    for index,samples in enumerate((capture['samples'][:3],capture['samples'][3:])):
        segment = deepcopy(capture)
        segment['samples'] = samples
        if index:
            for kind,received in (('exchange',1900),('funding',1930),('candles',1960)):
                segment['bootstrap'][kind].update(requested_at=START+received-1,
                                                  received_at=START+received)
        else:
            segment['exchange_after'].update(requested_at=START+2003, received_at=START+2004)
        quotes = tmp_path/f'quotes-{index}'
        quotes.mkdir()
        (quotes/'plan.json').write_text(json.dumps(dict(schema='GOLD_PUBLIC_QUOTE_SESSION_PLAN_V1',
            source=source,submission_enabled=False)))
        rows = [segment['bootstrap'][name] for name in ('exchange','funding','candles')]
        for sample in samples:
            rows.extend((sample['mark'],sample['depth']))
        for number,row in enumerate(rows):
            (quotes/f'receipt-{number:06d}.json').write_text(json.dumps(row)+'\n')
        prefix = tmp_path/f'prefix-{index}'
        snapshot(quotes,bars,prefix)
        captures.append((prefix,segment))
    kwargs = dict(bar_plan=(bars/'plan.json').read_bytes(),release_identity=source,
        quantity=Decimal('.1'),initial_collateral=Decimal('1000'),fee_rate=Decimal('.001'))
    first = bridge.arguments(captures[0][0],exchange_after=captures[0][1]['exchange_after'],**kwargs)
    second = bridge.arguments(captures[1][0],exchange_after=captures[1][1]['exchange_after'],
                              continuation=True,**kwargs)
    return first,second


@pytest.mark.parametrize('portfolio',[False,True])
def test_frozen_original_prefixes_join_original_account_and_request(tmp_path,monkeypatch,portfolio):
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_experiment import _execute, _encode
    from tools.gold_bar_research import read_arguments
    first,second = pieces(tmp_path,monkeypatch)
    assert set(second) == {'schema','capture','journal_files'}
    if portfolio:
        first['portfolio_plan'] = dict(quantities={name:'.1' for name in
            ('channel','trend','reversion')},max_notional='100')
    arguments = bridge.assemble(first,[second])
    result = _execute(arguments)
    assert len(result['trials']) == 3
    channel = result['trials'][0]['simulation']
    assert len(channel['orders']) == 2
    assert channel['orders'][0]['filled_at'] < START+901500 < channel['orders'][1]['filled_at']
    assert channel['reconciliation']['initial_equity'] == '1000'
    assert channel['ending_state']['cashflow_identity']['residual'] == '0'
    assert len(channel['environment_segments']) == 2
    assert result['dataset_identity']['input_segment_count'] == 2
    if portfolio:
        assert len(result['portfolio']['simulation']['environment_segments']) == 2
    bridge.write_request(arguments,tmp_path/'request')
    assert _encode(read_arguments(tmp_path/'request',
        original_source=first['release_identity'])) == _encode(arguments)
    assert result['economic_eligible'] is result['submission_enabled'] is False


def test_continuation_does_not_override_original_capital_and_history(tmp_path,monkeypatch):
    from tools import gold_native_prefix as bridge
    first,second = pieces(tmp_path,monkeypatch)
    second['initial_collateral'] = '2000'
    with pytest.raises(ValueError,match='FIELDS'):
        bridge.assemble(first,[second])


def test_original_queue_and_cold_recovery_use_the_joined_prefix_account_once(
        tmp_path,monkeypatch,technical_arguments):
    import time
    from tests.research.test_registered_research_job import queued
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tools import gold_native_prefix as bridge, gold_bar_experiment, gold_bar_research
    from tools import registered_gold_collection as collected, registered_research_job as job
    from tools import registered_research_run as native
    source = technical_arguments['release_identity']
    first,second = pieces(tmp_path,monkeypatch,source)
    for module in (gold_bar_experiment,gold_bar_research):
        monkeypatch.setattr(module,'source_identity',lambda:source)
    monkeypatch.setattr(time,'time_ns',lambda:
        (second['capture']['exchange_after']['received_at']+1000)*1_000_000)
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    bridge.write_request(bridge.assemble(first,[second]),
        tmp_path/'data/cache/gold_bar_research/requests/1')
    original,calls = native._native_run,[]
    def observed(**kwargs):
        producer = kwargs['execute']
        def execute():
            calls.append(1)
            return producer()
        return original(**{**kwargs,'execute':execute})
    monkeypatch.setattr(native,'_native_run',observed)
    registry,queue = queued(tmp_path)
    uid = queue.submit('gold_bar_research',{'attempt':1},1,invocation_id='continued-prefix')
    queue.join()
    assert registry.get(uid).status=='done',registry.get(uid).error
    project,database = job.workspace_paths()
    cut = ProjectResearchEvidenceReader(project,registry_database=database)()
    assert cut['attempt_count']==3 and {row['status'] for row in cut['attempts']}=={'PASSED'}
    report = project/'runs'/collected.invocation_id(1)/'archive/report.json'
    before = report.read_bytes(),database.read_bytes()
    channel = json.loads(before[0])['trials'][0]['simulation']
    assert len(channel['environment_segments'])==2 and len(channel['orders'])==2
    uid = queue.submit('gold_bar_recover',{'attempt':1},2,invocation_id='continued-prefix-recover')
    queue.join()
    assert registry.get(uid).status=='done',registry.get(uid).error
    assert calls==[1] and before==(report.read_bytes(),database.read_bytes())
