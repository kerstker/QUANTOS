"""Only pinned completed-cycle feedback crosses into the research UID."""
import json
import os
from pathlib import Path

import pytest

from tools import demo_feedback_publish as tool
from tools.demo_operator_setup import digest


@pytest.mark.parametrize('strategy', [False, True])
def test_pinned_source_rejects_links_and_wrong_digest(tmp_path,monkeypatch,strategy):
    monkeypatch.setattr(tool,'EXECUTOR',tmp_path/'executor')
    monkeypatch.setattr(tool.custody,'_require_protected',lambda *a,**kw:None)
    value={'schema':'synthetic-source'};sha=digest(value);cycle='a'*64
    directory=tool.EXECUTOR/('strategy-feedback' if strategy else 'resident-recovery/functional-cycles')/cycle/'research-feedback'
    directory.mkdir(parents=True);path=directory/(sha+'.json');path.write_text(json.dumps(value))
    for member in (tool.EXECUTOR,*tool.EXECUTOR.rglob('*')):
        member.chmod(0o700 if member.is_dir() else 0o600)
    read = lambda **kw: tool.read_export(strategy=strategy, **kw)
    assert read(cycle=cycle,artifact_sha256=sha,uid=os.getuid())==value
    path.write_text('{}')
    with pytest.raises(ValueError):read(cycle=cycle,artifact_sha256=sha,uid=os.getuid())
    path.unlink();outside=tmp_path/'outside';outside.write_text(json.dumps(value));path.symlink_to(outside)
    with pytest.raises(ValueError):read(cycle=cycle,artifact_sha256=sha,uid=os.getuid())
    with pytest.raises(ValueError):read(cycle='../outside',artifact_sha256=sha,uid=os.getuid())


def test_publication_drops_all_privileges_before_destination_access(tmp_path,monkeypatch):
    cfg={'research_uid':2003,'research_gid':2003,'executor_uid':2001,'research_state_root':str(tmp_path/'research')}
    events=[];raw={'result_hash':'b'*64}
    monkeypatch.setattr(tool,'verify_source',lambda _:None)
    monkeypatch.setattr(tool.os,'geteuid',lambda:0)
    monkeypatch.setattr(tool,'configuration',lambda _:cfg)
    monkeypatch.setattr(tool,'read_export',lambda **kw:events.append('read') or raw)
    monkeypatch.setattr(tool,'validate_export',lambda *a,**kw:events.append('validate') or raw)
    monkeypatch.setattr(tool.custody,'drop_identity',lambda *a:events.append('drop'))
    def publish(root,value):
        assert events==['read','validate','drop'] and root==Path(cfg['research_state_root'])
        events.append('write');assert value==raw
    monkeypatch.setattr(tool,'publish',publish)
    result=tool.run(source_commit='c'*40,cycle='a'*64,artifact_sha256='d'*64,feedback_sha256='b'*64)
    assert events==['read','validate','drop','write']
    assert result['status']=='RESEARCH_FEEDBACK_DELIVERED' and not result['submission_enabled']


def test_destination_is_atomic_immutable_and_refuses_links(tmp_path):
    raw={'result_hash':'b'*64,'synthetic':True}
    tool.publish(tmp_path,raw);tool.publish(tmp_path,raw)
    directory=tmp_path/'data/cache/ai_factory/execution-feedback';path=directory/('b'*64+'.json')
    assert json.loads(path.read_bytes())==raw
    before=path.read_bytes()
    with pytest.raises(ValueError):tool.publish(tmp_path,dict(raw,synthetic=False))
    assert path.read_bytes()==before
    outside=tmp_path/'outside';outside.write_bytes(before);path.unlink();path.symlink_to(outside)
    with pytest.raises(ValueError):tool.publish(tmp_path,raw)
    assert outside.read_bytes()==before


@pytest.mark.parametrize('damage',['schema','source','uid','path','extra'])
def test_feedback_enrollment_is_separate_from_display_profile(monkeypatch,damage):
    cfg={'schema':'DEMO_RESEARCH_FEEDBACK_PUBLICATION_V1','source_commit':'c'*40,
        'executor_uid':2001,'research_uid':2003,'research_gid':2003,'research_state_root':'/srv/research'}
    if damage=='schema':cfg['schema']='DEMO_DISPLAY_PUBLICATION_V1'
    elif damage=='source':cfg['source_commit']='a'*40
    elif damage=='uid':cfg['research_gid']=2001
    elif damage=='path':cfg['research_state_root']='/srv/../executor'
    else:cfg['arbitrary_path']='/private'
    monkeypatch.setattr(tool.custody,'_read_root_owned_json',lambda _:json.dumps(cfg).encode())
    with pytest.raises(ValueError):tool.configuration('c'*40)


def test_destination_directory_symlink_is_not_followed(tmp_path):
    outside=tmp_path/'outside';outside.mkdir()
    root=tmp_path/'research';root.mkdir();(root/'data').symlink_to(outside)
    with pytest.raises(ValueError):tool.publish(root,{'result_hash':'b'*64})
    assert list(outside.iterdir())==[]
