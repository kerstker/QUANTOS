"""Select a retained future result without inventing an exact policy approval.

This tests selection only. Full decision provenance and enabled proof remain
the dispatch/admission boundary, not a result hash or this selection helper.
"""
import json

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_gold_future_template import template_group_binding
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_future_configuration import installed_template as installed_template
from tools import demo_supervisor as supervisor
from tools import gold_strategy_dispatch as dispatch
from tools import gold_pilot_retention as retention


@pytest.fixture
def selection(installed_template, monkeypatch):
    template, _, state, _ = installed_template
    config=dict(schema='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1',
        source_commit=template['executor_release_commit'],account_ref_sha256=template['account_ref_sha256'],
        budget_group_binding_sha256=template_group_binding(template),kill_switch={'fixture':'selection only'})
    config_bytes=json.dumps(config).encode()
    result={'fixture':'retained result selection only; not executable decision evidence'}
    identity=_digest(result)
    def read_config(path):
        assert path==state/'strategy-dispatch.json'
        return config_bytes
    def read_result(root, identity_arg, *, source_commit, account):
        assert (root,identity_arg,source_commit,account)==(
            state,identity,template['executor_release_commit'],template['account_ref_sha256'])
        return json.dumps(result).encode()
    monkeypatch.setattr(supervisor,'_read_root_owned_json',read_config)
    monkeypatch.setattr(retention,'read_controller_result',read_result)
    monkeypatch.setattr(retention,'controller_result_ids',lambda *a,**kw:pytest.fail('future result search without exact identity'))
    return template,config,config_bytes,result,identity


def test_future_template_selects_only_the_explicit_retained_result(selection):
    template,_,raw,_,identity=selection
    _,retained,reference=dispatch.approved_reference(template['executor_release_commit'],template,result_sha256=identity)
    assert retained==raw
    assert reference['result_sha256']==identity
    assert reference['issuer_policy_sha256']==_digest(template)
    assert 'execution_constraints_sha256' not in template['bindings']


def test_future_template_without_target_does_not_search_old_exact_decisions(selection):
    template,*_=selection
    with pytest.raises(ValueError,match='STRATEGY_DISPATCH_FUTURE_TARGET_REQUIRED'):
        dispatch.approved_reference(template['executor_release_commit'],template)


def test_future_template_rejects_dispatcher_from_old_binding(selection,monkeypatch):
    template,config,_,_,identity=selection
    changed={**config,'budget_group_binding_sha256':'0'*64}
    monkeypatch.setattr(supervisor,'_read_root_owned_json',lambda path:json.dumps(changed).encode())
    with pytest.raises(ValueError,match='STRATEGY_DISPATCH_GROUP_CHANGED'):
        dispatch.approved_reference(template['executor_release_commit'],template,result_sha256=identity)
