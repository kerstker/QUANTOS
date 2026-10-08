"""Disk-backed attempt barriers, process exclusion and fail-closed corruption."""
import json
import os

import pytest

from tools import demo_cycle_checkpoint as store


@pytest.fixture
def area(tmp_path,monkeypatch):
    # Temporary files belong to the test UID, unlike production root custody.
    monkeypatch.setattr(store.supervisor,'_root_private_directory',lambda _:None)
    monkeypatch.setattr(store.supervisor,'_read_root_owned_json',lambda p:p.read_bytes())
    write=store.bundle.write_new
    monkeypatch.setattr(store.bundle,'write_new',lambda p,b:write(p,b,uid=os.getuid(),gid=os.getgid()))
    monkeypatch.setattr(store.supervisor,'STATE',tmp_path)
    area=tmp_path/'cycle';area.mkdir()
    return area


def test_checkpoints_preserve_exact_prefix_and_reject_overwrite(area):
    args=dict(cycle='a'*64,source_commit='b'*40,opening_task={'open':True})
    first={'schema':'DEMO_CYCLE_CONTINUATION_V1','attempts':[{'open':True}]}
    store.append(area,first,**args)
    original=(area/'attempts/0.json').read_bytes()
    second={**first,'attempts':first['attempts']+[{'close':1}]}
    store.append(area,second,**args)
    assert store.read(area,**args)==second and (area/'attempts/0.json').read_bytes()==original
    with pytest.raises(ValueError):store.append(area,first,**args)
    record=json.loads((area/'attempts/1.json').read_bytes());record['previous_sha256']='c'*64
    (area/'attempts/1.json').write_text(json.dumps(record))
    with pytest.raises(ValueError,match='FUNCTIONAL_CHECKPOINT_BINDING'):store.read(area,**args)


def test_same_directory_lock_excludes_concurrent_controller_and_recovers_after_exit(area):
    with store.cycle_lock(area):
        with pytest.raises(ValueError,match='FUNCTIONAL_CYCLE_BUSY'):
            with store.cycle_lock(area):pytest.fail('duplicate controller')
    with store.cycle_lock(area):pass
    assert list(area.iterdir())==[]


def test_missing_checkpoint_in_middle_cannot_hide_an_attempt(area):
    args=dict(cycle='a'*64,source_commit='b'*40,opening_task={'open':True})
    state={'schema':'DEMO_CYCLE_CONTINUATION_V1','attempts':[{'open':True}]}
    store.append(area,state,**args)
    os.rename(area/'attempts/0.json',area/'attempts/1.json')
    with pytest.raises(ValueError,match='FUNCTIONAL_CHECKPOINT_SEQUENCE'):store.read(area,**args)
