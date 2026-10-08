"""Opening equity comes from original flat-account observations, never a balance input."""
import copy

import pytest

from tests.live.test_demo_pre_submit import NOW, stores, signed
from tests.live.test_gold_demo_preparation import setup


def test_prepare_uses_observed_wallet_without_writing_execution_state(tmp_path,monkeypatch):
    from live.execution.demo_gold_pilot_enrollment import prepare
    from live.execution.demo_batch_risk import _digest
    _,_,values=setup(tmp_path,monkeypatch)
    ds,recovery,oms=stores(tmp_path)
    try:
        result=prepare(origin=values['origin'],dispatch=ds,recovery=recovery,oms=oms,now_ms=NOW)
        config=result['configuration']
        assert config['initial_equity']=='5000'
        assert config['opening_origin_sha256']==_digest(values['origin'])
        assert config['started_at_ms']==NOW and config['ends_at_ms']==NOW+86400000
        assert config['max_loss_fraction']=='0.05'
        assert result['submission_enabled'] is False
        assert ds.snapshot()==[] and recovery.latest_records()==() and oms.orders()==[]
        assert not (tmp_path/'gold-pilot').exists()
    finally:
        for store in (ds,recovery,oms):store.close()


@pytest.mark.parametrize('damage',['stale','account_change','pending'])
def test_unverified_or_unsettled_start_is_rejected(tmp_path,monkeypatch,damage):
    from live.execution.demo_gold_pilot_enrollment import prepare
    from live.execution import demo_pre_submit as prep
    private,envelope,values=setup(tmp_path,monkeypatch)
    ds,recovery,oms=stores(tmp_path)
    origin=copy.deepcopy(values['origin'])
    try:
        if damage=='pending':
            kwargs=dict(values,dispatch=ds,recovery=recovery,oms=oms)
            proposal=prep.proposal(**kwargs)
            prep.prepare(signed(private,envelope,proposal),evidence_directory=tmp_path/'evidence',**kwargs)
        if damage=='account_change':
            next(r for r in origin['reads'] if r['kind']=='account_after')['data']['assets'][0]['walletBalance']='6000'
        with pytest.raises(ValueError):
            prepare(origin=origin,dispatch=ds,recovery=recovery,oms=oms,now_ms=NOW+(86400000 if damage=='stale' else 0))
        assert not (tmp_path/'gold-pilot').exists() and ds.wire_snapshot()==[]
    finally:
        for store in (ds,recovery,oms):store.close()


def test_executor_enrolls_once_from_fresh_origin_and_preserves_start(tmp_path,monkeypatch):
    import json
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution import demo_position_context as position
    _,_,values=setup(tmp_path,monkeypatch)
    ds,recovery,oms=stores(tmp_path)
    monkeypatch.setattr(position,'_ROOT',tmp_path)
    monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256=values['origin']['account_ref_sha256'],duration_seconds=86400,
        max_loss_fraction='0.05',submission_enabled=False)
    path=tmp_path/'gold-pilot-request.json'
    kwargs=dict(root=tmp_path,source_commit='c'*40,origin=values['origin'],dispatch=ds,recovery=recovery,oms=oms)
    try:
        assert enrollment.enroll_requested(**kwargs,now_ms=NOW) is None
        path.write_text(json.dumps(request))
        first=enrollment.enroll_requested(**kwargs,now_ms=NOW)
        assert first['initial_equity']=='5000' and first['started_at_ms']==NOW
        assert enrollment.enroll_requested(**kwargs,now_ms=NOW+1)==first
        assert ds.snapshot()==[] and recovery.latest_records()==()
        request['source_commit']='d'*40
        path.write_text(json.dumps(request))
        with pytest.raises(ValueError):enrollment.enroll_requested(**kwargs,now_ms=NOW+2)
        assert json.loads((tmp_path/'gold-pilot'/'configuration.json').read_bytes())==first
    finally:
        for store in (ds,recovery,oms):store.close()


def test_interrupted_enrollment_never_replaces_baseline(tmp_path,monkeypatch):
    import json
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution import demo_position_context as position
    _,_,values=setup(tmp_path,monkeypatch)
    ds,recovery,oms=stores(tmp_path)
    monkeypatch.setattr(position,'_ROOT',tmp_path)
    monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256=values['origin']['account_ref_sha256'],duration_seconds=86400,
        max_loss_fraction='0.05',submission_enabled=False)
    (tmp_path/'gold-pilot-request.json').write_text(json.dumps(request))
    (tmp_path/'gold-pilot').mkdir()
    try:
        with pytest.raises((ValueError,FileNotFoundError)):
            enrollment.enroll_requested(root=tmp_path,source_commit='c'*40,origin=values['origin'],
                dispatch=ds,recovery=recovery,oms=oms,now_ms=NOW)
        assert not (tmp_path/'gold-pilot'/'configuration.json').exists()
    finally:
        for store in (ds,recovery,oms):store.close()


def test_partial_written_baseline_is_not_usable_for_opening(tmp_path,monkeypatch):
    import json
    from live.execution import demo_gold_pilot_enrollment as enrollment, demo_gold_pilot as pilot
    from live.execution import demo_position_context as position
    _,_,values=setup(tmp_path,monkeypatch)
    ds,recovery,oms=stores(tmp_path)
    monkeypatch.setattr(position,'_ROOT',tmp_path)
    monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256=values['origin']['account_ref_sha256'],duration_seconds=86400,
        max_loss_fraction='0.05',submission_enabled=False)
    (tmp_path/'gold-pilot-request.json').write_text(json.dumps(request))
    try:
        config=enrollment.prepare(origin=values['origin'],dispatch=ds,recovery=recovery,oms=oms,now_ms=NOW)['configuration']
        area=tmp_path/'gold-pilot';area.mkdir()
        (area/'configuration.json').write_text(json.dumps(config))
        with pytest.raises((ValueError,FileNotFoundError)):
            pilot.observe(values['risk_inputs']['account'],now_ms=NOW,origin_sha256=config['opening_origin_sha256'])
        assert not (area/'binding.json').exists()
    finally:
        for store in (ds,recovery,oms):store.close()
