"""Exercise real display/origin readers across isolated retention and rollback."""
import hashlib
import json
import os
from copy import deepcopy

import pytest

from services.research import demo_execution_origin as origin
from services.research import demo_resident_status as resident
from services.research import demo_executor_observation as observation
from tools import demo_retention_transaction as retention
from tests.research.test_demo_resident_status import report, NOW
from tests.tools.test_binance_demo_execution_origin import collect


@pytest.fixture
def private_files():
    previous=os.umask(0o077)
    try:yield
    finally:os.umask(previous)


def test_native_latest_and_pinned_origin_survive_reviewed_retention(tmp_path,private_files):
    root=tmp_path/'source';root.mkdir(mode=0o700)
    archived=tmp_path/'archive'
    original,_=collect(tmp_path)
    rows={scope:[] for scope in retention.SCOPES}
    saved={};expected={}
    for scope,model in [('execution-origins/BTCUSDC',origin),
                        ('resident-recovery/views',resident),('observation-views',observation)]:
        directory=root/scope;directory.mkdir(parents=True,mode=0o700)
        saved[scope]=[]
        for index in range(3):
            if model is origin:
                value=deepcopy(original);value['completed_at_ms']+=index
            elif model is resident:
                value=report();value['observed_at_ms']+=index
            else:
                value={'schema':observation.SCHEMA,'environment':'BINANCE_USDM_DEMO',
                    'scope':'TECHNICAL_VALIDATION_ONLY','phase':'PARTIAL_RECORDS',
                    'observed_at_ms':NOW+index,'account_ref_sha256':'a'*64,'source_commit':'b'*40,
                    'components':[{'name':name,'status':'NO_SNAPSHOT','phase':None,
                        'observed_at_ms':None,'report_sha256':None,'counts':None} for name in observation.NAMES],
                    'source_verified':False,'submission_enabled':False,'cash_reconciled':False,'economic_authority':False}
            path=model.save(directory,value);saved[scope].append(path)
            if index<2:rows[scope].append({'sha256':path.stem,'bytes':path.stat().st_size})
        expected[scope]=(model,model.load_latest(directory))
    pinned=saved['execution-origins/BTCUSDC'][0]
    pinned_name=pinned.relative_to(root).as_posix();pinned_value=origin.load(pinned)
    before={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    plan=tmp_path/'plan.json';review=tmp_path/'review.json'
    plan.write_text(json.dumps({'schema':'DEMO_RETENTION_REVIEW_PLAN_V1','apply_available':False,
        'directories':{scope:{'candidates':values} for scope,values in rows.items()}}))
    review.write_text(json.dumps({'schema':'DEMO_RETENTION_REFERENCE_REVIEW_V2','status':'SCOPED_REVIEW_ONLY',
        'plan_sha256':hashlib.sha256(plan.read_bytes()).hexdigest(),'candidate_reference_closure_checked':True,
        'all_references_proven':False,'apply_available':False,'production_changes':False,
        'candidate_files':6,'pinned_files':1,'pinned_candidates':[pinned_name]}))
    retention.prepare_reviewed(root,archived,plan=plan,review=review,
        expected_review_sha256=hashlib.sha256(review.read_bytes()).hexdigest())
    assert retention.apply(root,archived)['moved']==5
    assert origin.load(pinned)==pinned_value
    for scope,(model,value) in expected.items():assert model.load_latest(root/scope)==value
    for scope,paths in saved.items():
        name=paths[1].relative_to(root).as_posix()
        assert not paths[1].exists()
        assert retention.lookup(root,archived,name)==before[paths[1].relative_to(root)]
    retention.rollback(root,archived)
    assert before=={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
