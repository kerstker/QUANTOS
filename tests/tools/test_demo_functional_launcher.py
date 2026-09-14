"""The installed entrypoint cannot turn a recovery request into another enrollment."""
import os
from pathlib import Path
import sys

import pytest

from tools.demo_functional_launcher import render


@pytest.mark.parametrize('installed,args,module,operation',[
    (False,[], 'tools.demo_operator_setup',['--enroll','--run-cycle']),
    (True,[], 'tools.demo_functional_cycle',['--recover']),
    (True,['--recover'], 'tools.demo_functional_cycle',['--recover']),
    (True,['--resume'], 'tools.demo_functional_cycle',['--resume']),
])
def test_fixed_entrypoint_routes_without_mutating_setup_or_loading_keys(monkeypatch,installed,args,module,operation):
    calls=[]
    monkeypatch.setattr(os,'geteuid',lambda:0)
    monkeypatch.setattr(os,'chdir',lambda path:calls.append(('cwd',path)))
    monkeypatch.setattr(Path,'exists',lambda path:installed)
    monkeypatch.setattr(sys,'argv',['entry',*args])
    monkeypatch.setattr(os,'execve',lambda binary,argv,env:calls.append((binary,argv,env)))
    monkeypatch.setenv('PYTHONPATH','/untrusted')
    exec(compile(render('a'*40),'isolated-launcher','exec'),{})
    _,argv,env=calls[-1]
    assert argv[argv.index('-m')+1]==module and argv[-len(operation):]==operation
    assert env['ALLOW_ORDER_SUBMISSION']=='false' and env['REQUIRE_HUMAN_APPROVAL']=='true'
    assert '/untrusted' not in str(env) and argv[argv.index('--source-commit')+1]=='a'*40


def test_entrypoint_rejects_caller_source_or_extra_options_before_execution(monkeypatch):
    monkeypatch.setattr(os,'geteuid',lambda:0)
    monkeypatch.setattr(sys,'argv',['entry','--recover','--source-commit','f'*40])
    monkeypatch.setattr(os,'execve',lambda *a:pytest.fail('execution'))
    with pytest.raises(SystemExit):exec(compile(render('a'*40),'isolated-launcher','exec'),{})
