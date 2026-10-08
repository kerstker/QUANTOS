"""Original legacy writers and read-only files; no claim or account simulation."""
from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _retain
from tools.gold_pilot_retention_store import canonical
from tests.tools.test_gold_bar_rotation import bundle as auto_bundle, outcome
from tests.tools.test_gold_pilot_original_deadline import enrolled as enrolled, window

SOURCE='c'*40
ACCOUNT='d'*64


@pytest.fixture
def local(tmp_path, monkeypatch):
    from tools import demo_supervisor as supervisor, demo_operating_bundle as installer
    root=tmp_path/'controller';executor=tmp_path/'legacy-executor'
    root.mkdir(mode=0o700);executor.mkdir(mode=0o700)
    # Host ancestors and root caller UID are explicitly mapped. Leaf readers
    # keep actual UID, permissions, nlink, nofollow and stable metadata checks.
    original=supervisor._require_protected
    ancestors={(p.stat().st_dev,p.stat().st_ino) for p in tmp_path.parents}
    def protected(info,*,directory):
        mode=info.st_mode
        if (info.st_dev,info.st_ino) in ancestors:mode &= ~0o022
        return original(SimpleNamespace(st_mode=mode,st_uid=0 if info.st_uid==os.getuid() else info.st_uid),directory=directory)
    monkeypatch.setattr(supervisor,'_require_protected',protected)
    monkeypatch.setattr(installer,'EXECUTOR',executor)
    # Rotation's original root-only JSON reader gets only a test UID mapping.
    from tools.gold_pilot_install import _read_target
    monkeypatch.setattr(supervisor,'_read_root_owned_json',lambda path:_read_target(path,uid=os.getuid()))
    base=dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1',source_commit=SOURCE,
        trial_manifest_sha256='b'*64,observation_sha256='e'*64)
    return dict(root=root,executor_root=executor,base_runtime=base,current_runtime=base,
        source_commit=SOURCE,account=ACCOUNT,executor_uid=os.getuid())


def read(local):
    from tools.gold_feedback_legacy import discover_feedback
    return discover_feedback(**local)


def frame(local,*,status='STRATEGY_EPISODE_SETTLED',runtime=None,exported=True,tag=None,pilot=None):
    runtime=local['current_runtime'] if runtime is None else runtime
    result=outcome(runtime,status)
    result['decision']['account_ref_sha256']=local['account']
    result.pop('research_feedback',None)
    if pilot is not None:
        result['decision']['pilot_control']=dict(status='ACTIVE',configuration_sha256=_digest(pilot.config),submission_enabled=False)
        result['original_pilot_window']=window(pilot)
    if tag is not None:result['decision']['reason']=tag
    result['decision_sha256']=_digest(result['decision'])
    if status=='STRATEGY_EPISODE_SETTLED':
        if exported:
            result['feedback']=dict(status='BAR_STRATEGY_FEEDBACK_RETAINED' if runtime['schema'].startswith('GOLD_BAR_')
                else 'STRATEGY_FEEDBACK_RETAINED',feedback_sha256='f'*64,artifact_sha256='a'*64,
                path='/untrusted/not-read',submission_enabled=False,economic_authority=False)
            if runtime['schema'].startswith('GOLD_BAR_'):result['feedback']['cash_reconciled']=True
        else:result.pop('feedback',None)
    _retain(local['executor_root']/'strategy-decisions',result['decision'])
    _retain(local['root']/'strategy-decisions',result)
    return result


def delivered(local,result):
    value=dict(status='RESEARCH_FEEDBACK_DELIVERED',cycle_sha256=result['bundle_sha256'],
        feedback_sha256=result['feedback']['feedback_sha256'],source_verified=False,
        submission_enabled=False,economic_authority=False)
    _retain(local['root']/'strategy-feedback-deliveries',value)
    return value


def files(local):
    return {str(p):(p.read_bytes() if p.is_file() else None,p.stat().st_ino,p.stat().st_mtime_ns)
        for root in (local['root'],local['executor_root']) for p in (root,*root.rglob('*'))}


@pytest.mark.parametrize('kind',['strategy','retained','fresh','auto'])
def test_original_decision_and_controller_result_discover_pending_without_writes(local,kind):
    if kind=='auto':base=auto_bundle()
    elif kind=='fresh':base=dict(schema='GOLD_BAR_FRESH_EPISODE_INPUT_V1',source_commit=SOURCE,
        trial_manifest_sha256='b'*64,archive_source_commit='e'*40,candidate='channel',seconds=30,history_sha256='a'*64)
    elif kind=='retained':base=dict(schema='GOLD_BAR_RETAINED_EPISODE_INPUT_V1',source_commit=SOURCE,
        trial_manifest_sha256='b'*64,archive_source_commit='e'*40,observation_sha256='a'*64,entry_decision_ms=1000)
    else:base=local['base_runtime']
    local.update(base_runtime=base,current_runtime=base)
    result=frame(local);before=files(local);found=read(local)
    assert found['status']=='PENDING_DELIVERY' and found['result']==result
    assert found['result_bytes']==canonical(result) and found['result_sha256']==_digest(result)
    assert found['claim'] is None and found['mode']=='LEGACY_DISCOVERED'
    assert found['result_provenance']=='CONTROLLER_ORIGINAL' and found['decision_provenance']=='EXECUTOR_ORIGINAL'
    assert found['delivery_state']=='ABSENCE_OBSERVED' and found['delivery'] is None
    assert found['relation']=='CURRENT' and files(local)==before


def test_original_delivery_is_selected_and_no_result_is_enriched_on_disk(local):
    result=frame(local);receipt=delivered(local,result);before=files(local)
    found=read(local)
    assert found['status']=='DELIVERED' and found['delivery']==receipt
    assert found['delivery_bytes']==canonical(receipt) and found['delivery_sha256']==_digest(receipt)
    assert found['delivery_state']=='PRESENT' and found['result']==result and files(local)==before


def test_real_nonbar_original_pilot_enrollment_is_legacy_compatible(local,enrolled,monkeypatch):
    from tools import demo_operating_bundle as installer
    local.update(executor_root=enrolled.root,account=enrolled.account)
    monkeypatch.setattr(installer,'EXECUTOR',enrolled.root)
    result=frame(local,pilot=enrolled);before=files(local)
    found=read(local)
    assert found['status']=='PENDING_DELIVERY' and found['result']==result and found['claim'] is None
    assert files(local)==before


@pytest.mark.parametrize('damage',['source','account','configuration','missing_receipt','missing_intent','window','marker'])
def test_nonbar_pilot_originals_and_window_remain_strict(local,enrolled,monkeypatch,damage):
    from tools import demo_operating_bundle as installer
    local.update(executor_root=enrolled.root,account=enrolled.account)
    monkeypatch.setattr(installer,'EXECUTOR',enrolled.root)
    result=frame(local,pilot=enrolled)
    area=enrolled.root/'gold-pilot'
    if damage in ('source','account'):
        request=deepcopy(enrolled.request)
        request['source_commit' if damage=='source' else 'account_ref_sha256']='9'*(40 if damage=='source' else 64)
        (enrolled.root/'gold-pilot-request.json').write_bytes(canonical(request))
    elif damage=='configuration':
        config=deepcopy(enrolled.config);config['initial_equity']='4999'
        (area/'configuration.json').write_bytes(canonical(config))
    elif damage=='missing_receipt':(area/'enrollment.json').unlink()
    elif damage=='missing_intent':(area/'enrollment-intent.json').unlink()
    elif damage=='marker':(enrolled.root/'gold-pilot-retention.json').write_text('{}')
    else:
        path=local['root']/'strategy-decisions'/(_digest(result)+'.json');path.unlink()
        result['original_pilot_window']['request_sha256']='9'*64
        _retain(local['root']/'strategy-decisions',result)
    with pytest.raises(ValueError):read(local)


def test_valid_nonbar_request_only_allows_empty_original_start(local,enrolled):
    local['account']=enrolled.account
    path=local['executor_root']/'gold-pilot-request.json'
    # This matches the original installer's JSON spacing; do not rewrite the
    # original request into canonical record storage or fabricate enrollment.
    path.write_text(json.dumps(enrolled.request,sort_keys=True)+'\n');path.chmod(0o600)
    before=files(local);assert read(local) is None;assert files(local)==before
    frame(local)
    with pytest.raises(ValueError,match='LEGACY_FEEDBACK_PILOT_PARTIAL'):read(local)


def test_request_with_partial_enrollment_never_becomes_empty_start(local,enrolled):
    local['account']=enrolled.account
    path=local['executor_root']/'gold-pilot-request.json'
    path.write_bytes(canonical(enrolled.request));path.chmod(0o600)
    (local['executor_root']/'gold-pilot').mkdir(mode=0o700)
    with pytest.raises(ValueError,match='LEGACY_FEEDBACK_PILOT_PARTIAL'):read(local)


def test_real_bar_pilot_enrollment_requires_native_reader(local,enrolled,monkeypatch):
    from tools import demo_operating_bundle as installer
    local.update(executor_root=enrolled.root,account=enrolled.account,
        base_runtime=auto_bundle(),current_runtime=auto_bundle())
    monkeypatch.setattr(installer,'EXECUTOR',enrolled.root)
    with pytest.raises(ValueError,match='LEGACY_FEEDBACK_NATIVE_HISTORY_PRESENT'):read(local)


def test_nonbar_original_request_inode_is_rechecked(local,enrolled,monkeypatch):
    from tools import gold_feedback_legacy as legacy, demo_operating_bundle as installer
    local.update(executor_root=enrolled.root,account=enrolled.account)
    monkeypatch.setattr(installer,'EXECUTOR',enrolled.root)
    frame(local,pilot=enrolled);original=legacy._snapshot;calls=0
    def capture(*args,**kwargs):
        nonlocal calls
        answer=original(*args,**kwargs);calls+=1
        if calls==1:
            path=enrolled.root/'gold-pilot-request.json';copy=enrolled.root/'request.replace'
            copy.write_bytes(path.read_bytes());copy.chmod(0o600);os.replace(copy,path)
        return answer
    monkeypatch.setattr(legacy,'_snapshot',capture)
    with pytest.raises(ValueError,match='LEGACY_FEEDBACK_CHANGED'):read(local)


def test_later_nonsettled_record_does_not_hide_original_completion(local):
    result=frame(local)
    frame(local,status='NO_ENTRY_SIGNAL',tag='later observation')
    assert read(local)['result_sha256']==_digest(result)


@pytest.mark.parametrize('damage',['executor_uid','current_runtime','source_argument','account_argument'])
def test_fixed_reader_inputs_cannot_relabel_originals(local,damage):
    frame(local)
    if damage=='executor_uid':local['executor_uid']=os.getuid()+1
    elif damage=='current_runtime':local['current_runtime']=dict(local['current_runtime'],observation_sha256='7'*64)
    elif damage=='source_argument':local['source_commit']='7'*40
    else:local['account']='7'*64
    with pytest.raises(ValueError):read(local)


@pytest.mark.parametrize('state',['empty','nonsettled','unretained'])
def test_empty_and_complete_nonsettled_are_distinct_from_unretained_completion(local,state):
    if state!='empty':frame(local,status='HOLDING' if state=='nonsettled' else 'STRATEGY_EPISODE_SETTLED',exported=False)
    before=files(local);found=read(local)
    if state=='unretained':assert found['status']=='FEEDBACK_NOT_RETAINED'
    else:assert found is None
    assert files(local)==before


@pytest.mark.parametrize('side',['root','executor_root'])
@pytest.mark.parametrize('status',['STRATEGY_EPISODE_SETTLED','HOLDING'])
def test_missing_either_original_is_not_silently_no_pending(local,side,status):
    frame(local,status=status)
    for p in (local[side]/'strategy-decisions').iterdir():p.unlink()
    with pytest.raises(ValueError,match='LEGACY_FEEDBACK'):read(local)


@pytest.mark.parametrize('kind',['same_decision','two_settled'])
def test_ambiguous_completion_is_never_selected_by_filename_or_last_seen(local,kind):
    original=frame(local)
    if kind=='two_settled':frame(local,tag='another original terminal decision')
    else:
        other=deepcopy(original);other['recovery_observation']={'fixture':'different result around same decision'}
        _retain(local['root']/'strategy-decisions',other)
    with pytest.raises(ValueError,match='LEGACY_FEEDBACK.*AMBIGUOUS'):read(local)


@pytest.mark.parametrize('damage',['source','account','bundle','not_flat','export_hash','export_authority','wrong_status'])
def test_self_consistent_files_still_require_original_bound_contract(local,damage):
    result=frame(local)
    for side in ('root','executor_root'):
        for p in (local[side]/'strategy-decisions').iterdir():p.unlink()
    if damage=='source':result['source_commit']=result['decision']['source_commit']='b'*40
    elif damage=='account':result['decision']['account_ref_sha256']='b'*64
    elif damage=='bundle':result['bundle_sha256']='9'*64
    elif damage=='not_flat':result['decision']['position_flat']=False
    elif damage=='export_hash':result['feedback']['artifact_sha256']='not-a-hash'
    elif damage=='export_authority':result['feedback']['economic_authority']=True
    else:result['decision']['status']='SELF_DECLARED_COMPLETED'
    result['decision_sha256']=_digest(result['decision'])
    _retain(local['executor_root']/'strategy-decisions',result['decision'])
    _retain(local['root']/'strategy-decisions',result)
    with pytest.raises(ValueError):read(local)


@pytest.mark.parametrize('damage',['wrong_hash','noncanonical','symlink','hardlink','writable','oversize','unknown_member'])
def test_all_original_members_keep_digest_canonical_and_custody_checks(local,damage):
    result=frame(local);path=local['root']/'strategy-decisions'/(_digest(result)+'.json')
    if damage=='wrong_hash':path.write_bytes(b'{}\n')
    elif damage=='noncanonical':path.write_bytes(canonical(result).rstrip(b'\n'))
    elif damage=='symlink':
        copy=local['root'].parent/'outside';copy.write_bytes(path.read_bytes());path.unlink();path.symlink_to(copy)
    elif damage=='hardlink':os.link(path,local['root'].parent/'outside')
    elif damage=='writable':path.chmod(0o666)
    elif damage=='oversize':path.write_bytes(b' '*65537)
    else:(path.parent/'unknown.tmp').write_bytes(b'x')
    with pytest.raises(ValueError):read(local)


@pytest.mark.parametrize('damage',['cycle','feedback','flag','extra','orphan','multiple'])
def test_delivery_requires_unique_exact_original_completion_binding(local,damage):
    result=frame(local);receipt=delivered(local,result)
    if damage!='multiple':
        for p in (local['root']/'strategy-feedback-deliveries').iterdir():p.unlink()
    if damage=='cycle':receipt['cycle_sha256']='8'*64
    elif damage=='feedback':receipt['feedback_sha256']='8'*64
    elif damage=='flag':receipt['source_verified']=0
    elif damage=='extra':receipt['arbitrary_path']='/not/read'
    elif damage=='orphan':
        for side in ('root','executor_root'):
            for p in (local[side]/'strategy-decisions').iterdir():p.unlink()
    else:receipt['feedback_sha256']='8'*64
    _retain(local['root']/'strategy-feedback-deliveries',receipt)
    with pytest.raises(ValueError):read(local)


@pytest.mark.parametrize('trace',['gold-pilot-request.json','gold-pilot','gold-pilot-retention.json',
    'gold-pilot-decisions','gold-pilot-retention-audit','gold-bar-pilot-capacity.json',
    'strategy-bar-pilot-storage','gold-bar-pilot-storage-audit'])
@pytest.mark.parametrize('side',['root','executor_root'])
def test_native_or_enrollment_trace_never_falls_back_to_legacy(local,trace,side):
    if trace in ('gold-pilot-request.json','gold-pilot'):
        # These traces identify the bar/native path; a valid original non-bar
        # pilot has a separate positive regression below.
        local.update(base_runtime=auto_bundle(),current_runtime=auto_bundle())
    path=local[side]/trace
    if trace.endswith('.json'):path.write_text('{}')
    else:path.mkdir(mode=0o700)
    with pytest.raises(ValueError):read(local)


def test_member_budget_is_checked_before_reading_or_selecting(local):
    area=local['root']/'strategy-decisions';area.mkdir(mode=0o700)
    for i in range(1001):(area/(f'{i:064x}.json')).write_bytes(b'{}\n')
    with pytest.raises(ValueError,match='LEGACY_FEEDBACK.*BUDGET'):read(local)


@pytest.mark.parametrize('mutation',['same_bytes_new_inode','append_receipt','remove_directory'])
def test_double_read_rejects_original_or_membership_changes(local,monkeypatch,mutation):
    from tools import gold_feedback_legacy as legacy
    result=frame(local);original=legacy._snapshot;calls=0
    def capture(*args,**kwargs):
        nonlocal calls
        answer=original(*args,**kwargs);calls+=1
        if calls==1:
            path=local['root']/'strategy-decisions'/(_digest(result)+'.json')
            if mutation=='same_bytes_new_inode':
                copy=path.with_suffix('.replace');copy.write_bytes(path.read_bytes());copy.chmod(0o600);os.replace(copy,path)
            elif mutation=='append_receipt':delivered(local,result)
            else:path.unlink();path.parent.rmdir()
        return answer
    monkeypatch.setattr(legacy,'_snapshot',capture)
    with pytest.raises(ValueError,match='LEGACY_FEEDBACK.*CHANGED'):read(local)


def test_auto_previous_settled_rotation_requires_its_exact_enriched_original(local):
    from tools import gold_bar_rotation as rotation
    base=auto_bundle();local.update(base_runtime=base,current_runtime=base)
    result=frame(local);receipt=delivered(local,result)
    current=rotation.advance(root=local['root'],base=base,account=ACCOUNT,current=base,
        result=dict(result,research_feedback=receipt))
    local['current_runtime']=current
    before=files(local);found=read(local)
    assert found['relation']=='PREVIOUS' and found['status']=='DELIVERED' and found['rotation_sha256']
    assert files(local)==before
    for p in (local['root']/'strategy-feedback-deliveries').iterdir():p.unlink()
    with pytest.raises(ValueError,match='LEGACY_FEEDBACK.*ROTATION'):read(local)


def test_auto_current_completion_wins_only_after_prior_rotation_original_is_verified(local):
    from tools import gold_bar_rotation as rotation
    base=auto_bundle();local.update(base_runtime=base,current_runtime=base)
    previous=frame(local);receipt=delivered(local,previous)
    local['current_runtime']=rotation.advance(root=local['root'],base=base,account=ACCOUNT,current=base,
        result=dict(previous,research_feedback=receipt))
    current=frame(local)
    found=read(local)
    assert found['relation']=='CURRENT' and found['status']=='PENDING_DELIVERY'
    assert found['result_sha256']==_digest(current)
    path=local['executor_root']/'strategy-decisions'/(_digest(previous['decision'])+'.json')
    path.unlink()
    with pytest.raises(ValueError,match='LEGACY_FEEDBACK.*ORIGINAL_MISSING'):read(local)
