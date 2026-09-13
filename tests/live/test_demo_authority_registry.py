"""Deployment key custody and executor isolation; no real trust files are written."""
import json
import os
import stat
from types import SimpleNamespace

import pytest

from live.execution import demo_authority as boundary
from services.validation.demo_submission import DemoSubmissionRejected
from tests.live.test_demo_submission_verification import material,NOW


def metadata(**changes):
    value=dict(st_mode=stat.S_IFREG|0o644,st_uid=0,st_nlink=1,st_size=200)
    value.update(changes);return SimpleNamespace(**value)


@pytest.mark.parametrize('change',[{'st_uid':1000},{'st_mode':stat.S_IFREG|0o664},
    {'st_mode':stat.S_IFREG|0o646},{'st_mode':stat.S_IFLNK|0o644},{'st_nlink':2},
    {'st_size':65537},{'st_mode':stat.S_IFIFO|0o644}])
def test_uncontrolled_or_unbounded_registry_files_are_rejected(change):
    with pytest.raises(DemoSubmissionRejected):boundary._require_protected(metadata(**change),directory=False)


def test_root_owned_readable_regular_file_is_allowed_but_mutable_directory_is_not():
    boundary._require_protected(metadata(),directory=False)
    boundary._require_protected(metadata(st_mode=stat.S_IFDIR|0o755),directory=True)
    with pytest.raises(DemoSubmissionRejected):
        boundary._require_protected(metadata(st_mode=stat.S_IFDIR|0o775),directory=True)


def test_additional_private_file_check_precedes_any_secret_bytes(tmp_path,monkeypatch):
    path=tmp_path/'isolated-private.json';path.write_text('{"fixture":true}')
    monkeypatch.setattr(boundary,'_require_protected',lambda *a,**k:None)
    monkeypatch.setattr(boundary.os,'read',lambda *a:pytest.fail('private bytes read before file check'))
    def private_check(info):raise ValueError('ISOLATED_FILE_MODE_REJECTED')
    with pytest.raises(ValueError,match='ISOLATED_FILE_MODE_REJECTED'):
        boundary._read_root_owned_json(path,file_check=private_check)


def test_real_user_owned_tree_cannot_be_a_production_trust_root(tmp_path):
    if os.geteuid()==0:pytest.skip('requires an unprivileged test process')
    path=tmp_path/'registry.json';path.write_text('{}')
    with pytest.raises(DemoSubmissionRejected):boundary._read_root_owned_json(path)
    path.unlink();path.symlink_to('/etc/passwd')
    with pytest.raises(DemoSubmissionRejected):boundary._read_root_owned_json(path)


def test_executor_boundary_reads_fixed_registry_and_rejects_wrong_process(monkeypatch):
    _,registry,orders,context,envelope=material();seen=[]
    def read(path):seen.append(str(path));return json.dumps(registry).encode()
    monkeypatch.setattr(boundary,'_read_root_owned_json',read)
    monkeypatch.setattr(boundary.os,'geteuid',lambda:1000)
    with pytest.raises(DemoSubmissionRejected,match='EXECUTOR_IDENTITY'):
        boundary.verify_for_executor(json.dumps(envelope).encode(),expected_context=context,orders=orders,now_ms=NOW)
    assert seen==['/etc/quantos/demo-submission/authority.json']


def test_signed_old_process_grant_cannot_be_reused_after_executor_restart(monkeypatch):
    _,registry,orders,context,envelope=material()
    monkeypatch.setattr(boundary,'_read_root_owned_json',lambda path:json.dumps(registry).encode())
    monkeypatch.setattr(boundary.os,'geteuid',lambda:2001)
    monkeypatch.setattr(boundary,'executor_boot_sha256',lambda:'d'*64)
    with pytest.raises(DemoSubmissionRejected,match='EXECUTOR_BOOT'):
        boundary.verify_for_executor(json.dumps(envelope).encode(),expected_context=context,orders=orders,now_ms=NOW)


def test_executor_boundary_verifies_signed_current_process_material_in_isolated_fixture(monkeypatch):
    from tests.live.test_demo_submission_verification import encoded
    from services.validation.demo_submission import signing_payload
    private,registry,orders,context,envelope=material()
    context['executor_boot_sha256']=boundary.executor_boot_sha256()
    envelope['signature_base64url']=encoded(private.sign(signing_payload(envelope)))
    monkeypatch.setattr(boundary,'_read_root_owned_json',lambda path:json.dumps(registry).encode())
    monkeypatch.setattr(boundary.os,'geteuid',lambda:2001)
    result=boundary.verify_for_executor(json.dumps(envelope).encode(),expected_context=context,orders=orders,now_ms=NOW)
    assert result['signature_verified'] is True and result['one_time_consumed'] is False
