"""Protected day capture is append-only, idempotent and never submits."""
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
import pytest

from tests.live.test_demo_day_checkpoint import recovered_context, later_close, NOW, DAY
from tests.live.test_demo_reported_recovery import close


@pytest.mark.parametrize('changed',[False,True])
@pytest.mark.parametrize('gold',[False,True])
def test_fixed_capture_rechecks_executor_and_retains_only_valid_checkpoint(tmp_path,monkeypatch,changed,gold):
    from tools import demo_day_checkpoint as tool
    from live.execution.demo_pre_submit import _state
    batch,stores,events,value,state,context=recovered_context(tmp_path,monkeypatch,gold=gold)
    run=lambda:tool.run(source_commit='c'*40,symbol=context['symbol'])
    stores['stream']=object()
    before=deepcopy(state);calls=[]
    try:
        monkeypatch.setattr(tool,'require_custody',lambda:tmp_path)
        monkeypatch.setattr(tool,'fixed_account',lambda:(tmp_path,context['account_ref_sha256']))
        monkeypatch.setattr(tool,'_open',lambda *a:stores)
        monkeypatch.setattr(tool,'_cycle_lease',lambda *a:nullcontext())
        monkeypatch.setattr(tool,'_preparation_lease',lambda *a:nullcontext())
        monkeypatch.setattr(tool,'now_ms',lambda:NOW+DAY+100000)
        monkeypatch.setattr(tool,'_read',lambda p,*a,**kw:__import__('json').loads(Path(p).read_text()))
        def capture(*a,**kw):
            calls.append(kw)
            if changed:monkeypatch.setattr(tool,'_state',lambda *a:{**state,'changed':True})
            return later_close(value)
        monkeypatch.setattr(tool,'capture_inputs',capture)
        if changed:
            with pytest.raises(ValueError):run()
            assert not list((tmp_path/'receipt-reconciliation/day-checkpoints').glob('*.json'))
        else:
            result=run()
            assert result['status']=='CANONICAL_CONTEXT' and result['context']['day_id']==NOW//DAY+1
            assert calls[0]['day_replay'] is True and len(calls)==1
            assert run()['context']==result['context']
            assert len(calls)==1 and len(list((tmp_path/'receipt-reconciliation/day-checkpoints').glob('*.json')))==1
        assert _state(stores['dispatch'],stores['recovery'],stores['oms'])==before
    finally:events.close();close(stores)
