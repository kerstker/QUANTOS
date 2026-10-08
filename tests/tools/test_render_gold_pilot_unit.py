import json
import pytest


def test_generated_request_is_consumed_by_original_executor_enrollment(tmp_path,monkeypatch):
    from scripts.render_gold_pilot_unit import render
    from tests.live.test_gold_demo_preparation import setup
    from tests.live.test_demo_pre_submit import stores,NOW
    from live.execution import demo_gold_pilot_enrollment as enrollment,demo_position_context as position
    _,_,values=setup(tmp_path,monkeypatch)
    ds,recovery,oms=stores(tmp_path)
    monkeypatch.setattr(position,'_ROOT',tmp_path)
    monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
    result=render(release_commit='c'*40,account_ref_sha256=values['origin']['account_ref_sha256'],
        research_state_root='/home/operator/research/state',execute_approved=True)
    (tmp_path/'gold-pilot-request.json').write_text(json.dumps(result['enrollment_request']))
    try:
        config=enrollment.enroll_requested(root=tmp_path,source_commit='c'*40,origin=values['origin'],
            dispatch=ds,recovery=recovery,oms=oms,now_ms=NOW)
        assert config['initial_equity']=='5000' and config['ends_at_ms']==NOW+86400000
        assert config['max_loss_fraction']=='0.05' and not ds.snapshot()
        assert '--execute-approved' in result['service'] and '\n[Install]' not in result['service']
    finally:
        for store in (ds,recovery,oms):store.close()


@pytest.mark.parametrize('field,value',[('release_commit','a'*40+'\nExecStart=bad'),
    ('account_ref_sha256',''),('research_state_root','/home/a/../state'),('execute_approved',1)])
def test_unbound_or_injected_service_input_is_rejected(field,value):
    from scripts.render_gold_pilot_unit import render
    args=dict(release_commit='c'*40,account_ref_sha256='a'*64,research_state_root='/home/operator/state',execute_approved=False)
    args[field]=value
    with pytest.raises(ValueError):render(**args)
