import hashlib
import os
import subprocess

import pytest

from tools.build_research_operator_bundle import operate


def binding(tmp_path):
    rows={}
    for name,raw in {'installer':b'root installer bytes','cutover':b'def main(args): return 0',
                     'delivery/plan.json':b'{}'}.items():
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw);path.chmod(0o600)
        rows[name]=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())
    return dict(uid=os.getuid(),commit='a'*40,files=rows,delivery=str(tmp_path/'delivery'),
        plan_sha256=rows['delivery/plan.json']['sha256'],operation=str(tmp_path/'operation'))


@pytest.mark.parametrize('failure',[False,True])
def test_install_precedes_user_cutover_and_failure_stops_it(tmp_path,monkeypatch,failure):
    data=binding(tmp_path);calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        if failure:raise subprocess.CalledProcessError(1,args)
    monkeypatch.setattr(subprocess,'run',run)
    if failure:
        with pytest.raises(subprocess.CalledProcessError):operate(data,apply=True)
        assert len(calls)==1
    else:
        assert operate(data,apply=True)['status']=='RESEARCH_DELIVERY_COMPLETE'
        assert len(calls)==2 and calls[1][0][0]=='/opt/quantos/venvs/'+'a'*40+'/bin/python'
        assert '--apply' in calls[1][0][-1] and data['plan_sha256'] in calls[1][0][-1]
    assert calls[0][0]==['sudo','/usr/bin/python3','-I','-']
    assert calls[0][1]['input']==b'root installer bytes'


def test_review_does_not_start_install_or_service_operations(tmp_path,monkeypatch):
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('review must not execute'))
    assert operate(binding(tmp_path))['status']=='REVIEWED_NOT_APPLIED'


def test_changed_plan_stops_before_sudo(tmp_path,monkeypatch):
    data=binding(tmp_path)
    (tmp_path/'delivery/plan.json').write_text('{"changed":true}')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('changed binding executed'))
    with pytest.raises(ValueError,match='PAYLOAD_CHANGED'):operate(data,apply=True)
