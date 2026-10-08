"""A completed bar must traverse receipts and the original cash/export gates."""
import pytest

from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tests.live.gold_feedback_recovery_fixture import completed_feedback


@pytest.fixture
def completed_bar_feedback(case, original_evidence, tmp_path, monkeypatch):
    with completed_feedback(case, original_evidence, tmp_path, monkeypatch) as value:
        yield value


def test_original_completed_bar_exports_publishable_retained_feedback(completed_bar_feedback):
    f = completed_bar_feedback
    assert f.exported['status'] == 'BAR_STRATEGY_FEEDBACK_RETAINED'
    assert f.exported['cash_reconciled'] is True


def test_auto_opening_is_immutable_and_cash_builder_requires_external_runtime(completed_bar_feedback,monkeypatch):
    from copy import deepcopy
    import json
    from live.execution.demo_batch_risk import _digest
    from tools import gold_bar_feedback, gold_bar_reported_feedback, demo_executor_origin
    from tools.gold_bar_fresh_inputs import FreshEpisode
    f=completed_bar_feedback
    assert f.base_runtime['schema']=='GOLD_BAR_AUTO_EPISODE_INPUT_V1'
    assert f.result['decision']['status']=='STRATEGY_EPISODE_SETTLED'
    assert json.loads(f.result_bytes)==f.result
    assert len(f.result['original_pilot_window'])==8
    assert [row['kind'] for row in f.issuer_rows]==['RESERVED','RESERVED','SIGNED','RESERVED','SIGNED']
    assert f.artifact['schema']=='GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V2'
    assert f.artifact['opening_record_sha256']==_digest(f.opening)
    assert f.artifact['bundle_sha256']==_digest(f.base_runtime)
    assert f.artifact['account_report']['fill_count']==2
    assert f.artifact['account_report']['wallet_residual']=='0'
    assert f.artifact['account_report']['final_quantity']=='0'
    # Native opening/closing bounds: original venue receipts remain +13/+2013.
    path=next((f.root/'strategy-bar-reported-captures'/_digest(f.base_runtime)/'inputs').glob('*.json'))
    inputs=json.loads(path.read_bytes())['inputs']
    assert [t['time']-f.now_ms for t in inputs['statements'][0]['trades']]==[13,2013]
    assert inputs['opening']==f.opening['origin']
    assert inputs['opening']['completed_at_ms']+5000<f.now_ms+13
    assert inputs['closing']['observed_at_ms']-5000>f.now_ms+2013
    _,batches,_=gold_bar_feedback._retained_episode(f.root,engine=f.engine,entry_decision_ms=f.entry_decision_ms,
        account=f.account,stores=dict(f.stores,stream=f.stream),as_of_ms=f.clock[0])
    args=dict(engine=f.engine,entry_decision_ms=f.entry_decision_ms,prepared=[b['prepared'] for b in batches],
        dispatch=f.stores['dispatch'],inputs=inputs,source_commit=f.source_commit,as_of_ms=f.clock[0],
        opening_record=f.opening,bundle_sha256=_digest(f.base_runtime))
    assert gold_bar_reported_feedback.build(**args)==f.artifact
    for change in (dict(bundle_sha256=None),dict(bundle_sha256='f'*64),
            dict(opening_record=dict(f.opening,state_sha256='f'*64)),
            dict(opening_record=dict(f.opening,pilot_configuration_sha256='f'*64))):
        with pytest.raises(ValueError):gold_bar_reported_feedback.build(**dict(args,**change))
    original=deepcopy(f.opening)
    monkeypatch.setattr(demo_executor_origin,'run',lambda **kw:pytest.fail('restart recaptured account opening'))
    monkeypatch.setattr(__import__('time'),'sleep',lambda _:pytest.fail('restart renewed the pre-signal wait'))
    replay=FreshEpisode(f.root,f.base_runtime,source_commit=f.source_commit,uid=f.executor_uid,cash_account=f.account)
    assert replay.account_opening==original
    assert json.loads(f.result_bytes)==f.result


def test_retained_without_presignal_origin_remains_fail_closed(case,original_evidence,tmp_path,monkeypatch):
    # Historical RETAINED input has no pre-signal full origin. This test does
    # not manufacture one after entry or relabel the legacy episode as AUTO.
    with pytest.raises(ValueError,match='ACCOUNT_UNRESOLVED'):
        with completed_feedback(case,original_evidence,tmp_path,monkeypatch,automatic=False):
            pytest.fail('legacy quick-fill episode invented an opening')
