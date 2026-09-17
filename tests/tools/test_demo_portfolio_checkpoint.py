import os

import pytest

from tools import demo_portfolio_checkpoint as checkpoint
from tests.live.test_demo_request_inputs import inputs
from tests.live.test_demo_portfolio_task import declarations
from live.execution import demo_portfolio_task


def test_interrupted_install_reuses_original_task_and_claimed_task_is_query_only(tmp_path,monkeypatch):
    kwargs,opened,log=inputs(tmp_path,monkeypatch)
    try:
        kwargs.pop('orders');allocation,forecast,funding=declarations(kwargs,.01)
        built=demo_portfolio_task.build(allocation=allocation,forecast=forecast,funding=funding,**kwargs)
        state=tmp_path/'controller';state.mkdir()
        monkeypatch.setattr(checkpoint.supervisor,'STATE',state)
        monkeypatch.setattr(checkpoint.os,'geteuid',lambda:0)
        monkeypatch.setattr(checkpoint.supervisor,'_root_private_directory',lambda _:None)
        monkeypatch.setattr(checkpoint.supervisor,'_read_root_owned_json',lambda p:p.read_bytes())
        writer=checkpoint.bundle.write_new
        def write(path,body):
            if path.parent.name=='tasks':raise OSError('interrupted before task install')
            return writer(path,body,uid=os.getuid(),gid=os.getgid())
        monkeypatch.setattr(checkpoint.bundle,'write_new',write)
        args=dict(allocation=allocation,forecast=forecast,built=built,policy=kwargs['issuer_policy'],now_ms=kwargs['now_ms'])
        with pytest.raises(OSError):checkpoint.retain(**args)
        records=list((state/'portfolio-decisions').glob('*.json'))
        assert len(records)==1
        original=records[0].read_bytes()
        monkeypatch.setattr(checkpoint.bundle,'write_new',lambda p,b:writer(p,b,uid=os.getuid(),gid=os.getgid()))
        result=checkpoint.retain(**args)
        assert result['status']=='TASK_READY' and result['task_sha256']==built['task_sha256']
        assert records[0].read_bytes()==original
        task_path=state/'tasks'/f"{built['task_sha256']}.json"
        task_bytes=task_path.read_bytes()
        checkpoint.retain(**args)
        assert task_path.read_bytes()==task_bytes
        (state/'task-claims').mkdir()
        (state/'task-claims'/f"{built['task_sha256']}.json").write_text('{}')
        # Even an unreadable/partial claim cannot become permission to retry.
        assert checkpoint.retain(**args)['status']=='QUERY_ONLY'
        assert len(list((state/'tasks').iterdir()))==1
    finally:
        log.close()
        for store in opened:store.close()
