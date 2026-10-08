"""An uncertain retained retry chain cannot start another account observation."""
from types import SimpleNamespace
from copy import deepcopy

import pytest

from tests.tools.test_gold_pilot_retention import enrolled as enrolled, bundle, packet, NOW, SOURCE


@pytest.mark.parametrize('damage', ['read_error', 'schema', 'extra', 'bool', 'both', 'neither'])
def test_unverified_reobservation_cannot_start_worker(enrolled, monkeypatch, damage):
    from tools import gold_pilot_closure_controller as terminal, gold_strategy_controller as controller
    f = enrolled
    window = packet(f, NOW)['original_pilot_window']
    released = []
    inspection = dict(schema='GOLD_PILOT_REOBSERVATION_INSPECTION_V1',
        candidate={'unverified_candidate': True}, reobservation=None,
        controller_checkpoint=None, executor_checkpoint=None, submission_enabled=False)
    if damage == 'schema': inspection['schema'] = 'UNKNOWN'
    elif damage == 'extra': inspection['permission'] = True
    elif damage == 'bool': inspection['submission_enabled'] = 0
    elif damage == 'both': inspection['reobservation'] = {'unverified_chain': True}
    elif damage == 'neither': inspection['candidate'] = None
    def inspect(**kwargs):
        assert kwargs['current_runtime'] == bundle()
        if damage == 'read_error': raise ValueError('REOBSERVATION_INTEGRITY')
        return inspection
    owner = SimpleNamespace(inspect_reobservation=inspect,
        release=lambda: released.append(True))
    monkeypatch.setattr(controller.demo_task, 'preparation_command',
        lambda **kwargs: ['tools.demo_request_prepare', 'RuntimeMaxSec=155s'])
    monkeypatch.setattr(terminal.subprocess, 'Popen',
        lambda *args, **kwargs: pytest.fail('unverified retry state launched a worker'))
    with pytest.raises(ValueError):
        terminal.finish_idle(source_commit=SOURCE, registry={},
            policy={'account_ref_sha256': f.account}, owner=owner,
            current_runtime=bundle(), original_window=window)
    assert released, 'inspection failure retained the cross-role store lease'


@pytest.mark.parametrize('changed', [None, 'request', 'result'])
def test_complete_chain_requires_exact_json_identity(changed):
    from tools import gold_pilot_closure_controller as terminal
    req={'submission_enabled':False}
    result={'closure_intent_sha256':'f'*64,'submission_enabled':False}
    view=dict(status='COMPLETE',intent_sha256='f'*64,request=deepcopy(req),result=deepcopy(result))
    if changed is not None:view[changed]['submission_enabled']=0
    inspected=dict(schema='GOLD_PILOT_REOBSERVATION_INSPECTION_V1',candidate=None,
        reobservation=view,controller_checkpoint=None,executor_checkpoint=None,submission_enabled=False)
    owner=SimpleNamespace(inspect_reobservation=lambda **kw:inspected)
    args=dict(owner=owner,current_runtime=bundle(),request=req,result=result,now_ms=NOW)
    if changed is None:assert terminal._completed_reobservation(**args)==view
    else:
        with pytest.raises(ValueError,match='PILOT_REOBSERVATION_FINAL_CHAIN'):
            terminal._completed_reobservation(**args)
