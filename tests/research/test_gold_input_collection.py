import json

import pytest

from services.console.api.gold_input_catalog import catalog
from tests.tools.test_gold_public_cost_inputs import request
from tools.gold_cost_inputs import dump_inputs, load_inputs
from tools.gold_public_cost_inputs import assemble_inputs
from tools.gold_public_capture import PublicCapture


def setup(tmp_path, monkeypatch):
    from tools import gold_input_collection as tool, gold_input_preparation
    req = request()
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    monkeypatch.setattr(gold_input_preparation, 'source_identity', lambda: req['release_identity'])
    base = tmp_path/'data/cache/ai_factory'
    (base/'gold-inputs').mkdir(parents=True)
    (base/'gold-inputs/1.json').write_bytes(dump_inputs(assemble_inputs(**req)))
    start = req['capture']['samples'][0]['depth']['received_at']
    shift = start-req['document']['start_ms']
    capture = PublicCapture(started_at_ms=start-200)
    for row in req['document']['packets']:
        p = dict(row['payload']);p['T'] += shift;p['E'] += shift
        capture.ingest(json.dumps({'stream':'xauusdt@aggTrade','data':p}),received_at_ms=row['received_at_ms']+shift)
    trades = capture.finish(ended_at_ms=start+3000,reason='DURATION_REACHED')
    calls = []
    async def collect(seconds, output):
        calls.append(seconds)
        return {'quotes':req['capture'],'trades':trades}
    monkeypatch.setattr(tool, 'collect_pair', collect)
    return tool, base, calls


def test_collect_publish_and_retry_do_not_recapture_or_change_template(tmp_path, monkeypatch):
    tool, base, calls = setup(tmp_path, monkeypatch)
    original = (base/'gold-inputs/1.json').read_bytes()
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.runner import JobQueue
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    run_id = queue.submit('gold_collect_input', {'attempt':2,'template_id':1,'seconds':30}, 1)
    queue.join()
    assert registry.get(run_id).status == 'done', registry.get(run_id).error
    result = json.loads((base/'gold-collections/2/result.json').read_text())
    assert result['input_id'] == 2
    args = load_inputs((base/'gold-inputs/2.json').read_bytes())
    assert args['document']['zone']['lower'] == load_inputs(original)['document']['zone']['lower']
    assert catalog(tmp_path)['items'][0]['input_id'] == 2
    assert tool.run_collect({'attempt':2,'template_id':1,'seconds':30}) == result
    with pytest.raises(ValueError,match='GOLD_COLLECTION_REQUEST_CHANGED'):
        tool.run_collect({'attempt':2,'template_id':1,'seconds':30,'decision_policy':'FIRST_RECEIVED_WATERMARK_V1'})
    assert calls == [30] and (base/'gold-inputs/1.json').read_bytes() == original


def test_interrupted_capture_is_retained_and_not_published(tmp_path, monkeypatch):
    tool, base, calls = setup(tmp_path, monkeypatch)
    original_collect = tool.collect_pair
    async def interrupted(seconds, output):
        pair = await original_collect(seconds, output)
        pair['trades']['status'] = 'INTERRUPTED'
        pair['trades']['stop_reason'] = 'STREAM_INTERRUPTED'
        return pair
    monkeypatch.setattr(tool, 'collect_pair', interrupted)
    with pytest.raises(ValueError):
        tool.run_collect({'attempt':2,'template_id':1,'seconds':30})
    assert (base/'gold-collections/2/trades.json').is_file()
    assert not (base/'gold-inputs/2.json').exists()
    with pytest.raises(ValueError):
        tool.run_collect({'attempt':2,'template_id':1,'seconds':30})
    assert calls == [30]


def test_both_collectors_share_a_window_and_retain_each_completed_capture(tmp_path, monkeypatch):
    import asyncio
    import threading
    from tools import gold_input_collection as tool
    started = threading.Event()
    def quotes(**kwargs):
        assert kwargs['symbol'] == 'XAUUSDT'
        assert started.wait(2)
        return {'test': 'quotes'}
    async def trades(seconds):
        assert seconds == 30
        started.set()
        return {'test': 'trades'}
    monkeypatch.setattr(tool, 'capture_public', quotes)
    monkeypatch.setattr(tool, 'collect', trades)
    pair = asyncio.run(tool.collect_pair(30, tmp_path))
    assert pair == {'quotes': {'test': 'quotes'}, 'trades': {'test': 'trades'}}
    assert json.loads((tmp_path/'quotes.json').read_text()) == pair['quotes']
    assert json.loads((tmp_path/'trades.json').read_text()) == pair['trades']
