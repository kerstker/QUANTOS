from decimal import Decimal as D
import numpy as np
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research_runner.runner import JobQueue
from services.console.api.research_run_views import _technical_replay_detail
from tools import causal_block_research as tool


def test_native_orderblock_run_keeps_trial_population_and_technical_boundary(tmp_path,monkeypatch):
    start=1735689900000;end=start+40*86400000
    times=np.arange(start-30*86400000,end+60000,60000,dtype=np.int64)
    market=dict(times=times,prices=np.full(len(times),100.),marks=np.full(len(times),100.),
        trade_highs=np.full(len(times),101.),trade_lows=np.full(len(times),99.),trade_closes=np.full(len(times),100.),
        funding=[],start=start,end=end,daily_volume={int(t//86400000):D('10000000') for t in times})
    monkeypatch.setattr(tool.shared,'load_data',lambda **kwargs:(market.copy(),None,None,None,None,{'fixture':True}))
    monkeypatch.setattr(tool,'OUTPUT',tmp_path/'results')
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')));queue=JobQueue(registry)
    run=queue.submit('orderblock_research',{'attempt':1},1,invocation_id='ob-test');queue.join()
    record=registry.get(run)
    assert record.status=='done',record.error
    assert record.metrics['trial_count']==24
    assert record.metrics['failed_cases']==0
    assert record.metrics['further_review_count']==0
    assert _technical_replay_detail(record)['authoritative'] is False
