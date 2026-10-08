"""Offline feedback deployment material must remain finite and maintenance-bound."""
import json
import pytest
from scripts.render_demo_feedback_units import render


def test_feedback_service_uses_pending_only_and_exact_maintenance_enrollment():
    files=render(release_commit='a'*40,research_state_root='/home/research/state')
    service=files['quantos-demo-feedback.service'];timer=files['quantos-demo-feedback.timer']
    assert '--feedback-pending' in service and '--resume' not in service and '--run-cycle' not in service
    assert 'PrivateNetwork=true' in service and 'Restart=' not in service
    assert 'CAP_SETUID CAP_SETGID CAP_DAC_READ_SEARCH' in service
    assert '/etc/quantos/demo-submission/credentials.json' in service
    assert 'OnUnitInactiveSec=5min' in timer
    from tools.demo_maintenance_services import FEEDBACK_UNITS
    assert json.loads(files['feedback-automation.json'])['units']==list(FEEDBACK_UNITS)
    assert '[Install]' not in service and '[Install]' not in timer


@pytest.mark.parametrize('commit,root',[('bad','/home/research/state'),('a'*40,'/'),
    ('a'*40,'/home/../root'),('a'*40,'/etc'),('a'*40,'/home/a\nExecStart=/bin/sh')])
def test_feedback_render_rejects_ambiguous_paths(commit,root):
    with pytest.raises(ValueError):render(release_commit=commit,research_state_root=root)


def test_complete_feedback_materials_are_accepted_by_runtime_reader(monkeypatch):
    from tools import demo_feedback_publish as publisher
    files=render(release_commit='a'*40,research_state_root='/srv/research/state',
        executor_uid=2001,research_uid=2003,research_gid=2004)
    body=files['research-feedback.json'].encode()
    monkeypatch.setattr(publisher.custody,'_read_root_owned_json',lambda path:body)
    cfg=publisher.configuration('a'*40)
    assert cfg['executor_uid']==2001 and cfg['research_uid']==2003 and cfg['research_gid']==2004
    assert cfg['research_state_root']=='/srv/research/state'
    with pytest.raises(ValueError,match='FEEDBACK_PUBLICATION_SOURCE'):publisher.configuration('b'*40)


@pytest.mark.parametrize('ids',[(2001,None,None),(2001,2001,2003),(2001,2003,2001),(0,2003,2004),(2001,True,2004)])
def test_incomplete_or_aliasing_identities_cannot_render_publication(ids):
    with pytest.raises(ValueError):
        render(release_commit='a'*40,research_state_root='/srv/research/state',
            executor_uid=ids[0],research_uid=ids[1],research_gid=ids[2])


def test_cli_keeps_both_profiles_in_same_hashed_review(tmp_path,capsys):
    import hashlib
    from scripts.render_demo_feedback_units import main
    directory=tmp_path/'materials'
    assert main(['--release-commit','a'*40,'--research-state-root','/srv/research/state',
        '--executor-uid','2001','--research-uid','2003','--research-gid','2004',
        '--output-dir',str(directory)])==0
    review=json.loads(capsys.readouterr().out)
    assert review['publication_profile_included'] and not review['installed'] and not review['started']
    assert set(review['files'])=={'quantos-demo-feedback.service','quantos-demo-feedback.timer',
        'feedback-automation.json','research-feedback.json'}
    for name,digest in review['files'].items():
        path=directory/name
        assert hashlib.sha256(path.read_bytes()).hexdigest()==digest and path.stat().st_mode&0o777==0o600


def test_invalid_cli_identity_leaves_no_partial_materials(tmp_path):
    from scripts.render_demo_feedback_units import main
    directory=tmp_path/'materials'
    with pytest.raises(ValueError):
        main(['--release-commit','a'*40,'--research-state-root','/srv/research/state',
            '--executor-uid','2001','--output-dir',str(directory)])
    assert not directory.exists()
