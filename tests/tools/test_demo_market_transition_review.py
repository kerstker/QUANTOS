"""The operator review retains its original capture/context boundaries."""
from contextlib import nullcontext
from copy import deepcopy
import json
import pytest

from tools import demo_successor_setup as tool
from tests.live.test_demo_market_transition_review import materials


def environment(tmp_path, monkeypatch):
    values = materials(tmp_path)
    monkeypatch.setattr(tool, 'verify_source', lambda _: None)
    monkeypatch.setattr(tool.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(tool, 'SETUP', tmp_path/'setup')
    monkeypatch.setattr(tool, 'cycle_lock', lambda _: nullcontext())
    monkeypatch.setattr(tool.installer, 'now_ms', lambda: values['now_ms'])
    spec = tool.setup.propose(origin=values['legacy_origin'], registry=values['registry'],
        position_context=values['legacy_context'], now_ms=values['now_ms'], source_commit='c'*40)
    records = {'spec.json':spec, 'result.json':dict(spec_sha256=tool.setup.digest(spec), source_commit='c'*40)}
    monkeypatch.setattr(tool, 'read', lambda path: deepcopy(records[path.name]))
    calls = []
    def context(source, registry, cycle, *, allow_day_checkpoint):
        assert allow_day_checkpoint is False
        calls.append('context')
        return values['legacy_context']
    monkeypatch.setattr(tool, 'context_for', context)
    def inputs(source, *, continuation, symbol):
        assert source=='c'*40 and continuation is True
        calls.append(symbol)
        return values['registry'], values['legacy_origin' if symbol=='BTCUSDC' else 'gold_origin']
    monkeypatch.setattr(tool.setup, '_runtime_inputs', inputs)
    monkeypatch.setattr(tool.setup, 'operator_confirmation', lambda *_: pytest.fail('cannot enroll'))
    monkeypatch.setattr(tool, 'install_successor', lambda *a, **k: pytest.fail('cannot install'))
    return values, calls


def test_review_uses_original_context_and_each_fixed_market_without_new_authority(tmp_path, monkeypatch, capsys):
    values, calls = environment(tmp_path, monkeypatch)
    before = deepcopy(values)
    assert tool.main(['--source-commit','c'*40,'--review-market-transition','XAUUSDT'])==0
    result = json.loads(capsys.readouterr().out)
    assert result['status']=='UNAPPROVED_MARKET_TRANSITION_REVIEW'
    assert result['review']['gold_observation']['wallet']=='4200'
    assert result['review_sha256']==tool.setup.digest(result['review'])
    assert calls==['BTCUSDC','context','BTCUSDC','XAUUSDT']
    assert not result['orders_submitted'] and not result['services_started']
    assert values==before and not (tmp_path/'setup').exists()


@pytest.mark.parametrize('argument', ['--enroll', '--run-cycle', '--portfolio-config',
    '--research-identity-sha256', '--symbol'])
def test_review_cannot_be_combined_with_new_permissions_or_market_override(monkeypatch, capsys, argument):
    monkeypatch.setattr(tool, 'verify_source', lambda _: pytest.fail('no protected reads'))
    argv=['--source-commit','c'*40,'--review-market-transition','XAUUSDT',argument]
    if argument in ('--portfolio-config','--research-identity-sha256','--symbol'):
        argv.append({'--portfolio-config':'/tmp/synthetic', '--research-identity-sha256':'a'*64,
                     '--symbol':'BTCUSDC'}[argument])
    assert tool.main(argv)==3
    assert json.loads(capsys.readouterr().out)['phase']=='PREFLIGHT'


def test_review_requires_existing_day_evidence_without_creating_a_checkpoint(monkeypatch):
    from tools import demo_functional_cycle
    calls=[]
    class Runtime:
        def __init__(self,*_):pass
        def invoke(self,operation,**_):
            calls.append(operation)
            assert operation=='context'
            return dict(status='CANONICAL_CONTEXT',context={'day_id':0})
        def close(self):calls.append('close')
    monkeypatch.setattr(demo_functional_cycle,'Runtime',Runtime)
    monkeypatch.setattr(tool.installer,'now_ms',lambda:86400000)
    with pytest.raises(ValueError,match='SUCCESSOR_DAY_CHECKPOINT_REQUIRED'):
        tool.context_for('c'*40,{},'a'*64,allow_day_checkpoint=False)
    assert calls==['context','close']


def test_original_successor_still_rejects_cross_market_enrollment(tmp_path,monkeypatch,capsys):
    _, calls = environment(tmp_path,monkeypatch)
    assert tool.main(['--source-commit','c'*40,'--symbol','XAUUSDT'])==3
    assert calls==[] and not (tmp_path/'setup').exists()


def test_pending_install_cannot_be_interpreted_as_a_new_transition(tmp_path,monkeypatch,capsys):
    _, calls = environment(tmp_path,monkeypatch)
    tool.SETUP.mkdir()
    (tool.SETUP/'pending.json').write_bytes(b'unchanged incomplete enrollment')
    assert tool.main(['--source-commit','c'*40,'--review-market-transition','XAUUSDT'])==3
    assert calls==[] and (tool.SETUP/'pending.json').read_bytes()==b'unchanged incomplete enrollment'


def test_changed_registry_during_gold_observation_rejects_review(tmp_path,monkeypatch,capsys):
    values, _ = environment(tmp_path,monkeypatch)
    original=tool.setup._runtime_inputs
    def changed(*a,**kw):
        registry,origin=original(*a,**kw)
        if kw['symbol']=='XAUUSDT':
            registry=deepcopy(registry);registry['keys']=[]
        return registry,origin
    monkeypatch.setattr(tool.setup,'_runtime_inputs',changed)
    assert tool.main(['--source-commit','c'*40,'--review-market-transition','XAUUSDT'])==3
    assert not (tmp_path/'setup').exists()
