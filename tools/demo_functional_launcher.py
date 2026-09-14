"""Render the fixed local Demo entrypoint; never selects a caller's source path."""
import re


def render(source_commit):
    if type(source_commit) is not str or re.fullmatch('[a-f0-9]{40}',source_commit) is None:
        raise ValueError('FUNCTIONAL_LAUNCHER_SOURCE')
    return '''#!/usr/bin/python3
"""Local finite Demo setup, same-cycle continuation, or query-only recovery."""
import os,sys
from pathlib import Path
if os.geteuid()!=0:raise SystemExit('Run with sudo from the operator terminal.')
args=sys.argv[1:]
if args not in ([],['--recover'],['--resume']):raise SystemExit('Use --recover (no orders) or --resume (original active mandate).')
source='''+repr(source_commit)+'''
root='/opt/quantos/releases/'+source
python='/opt/quantos/venvs/'+source+'/bin/python'
installed=Path('/etc/quantos/demo-operator-setup').exists()
if args or installed:
    module='tools.demo_functional_cycle'
    operation=args or ['--recover']
else:
    module='tools.demo_operator_setup'
    operation=['--enroll','--run-cycle']
env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONPATH':root,'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1',
    'LIVE_TRADING_ENABLED':'false','REAL_BROKER_ENABLED':'false','ALLOW_ORDER_SUBMISSION':'false',
    'ALLOW_AI_DIRECT_TRADING':'false','REQUIRE_HUMAN_APPROVAL':'true'}
for name in ('SUDO_UID','SUDO_USER'):env[name]=os.environ.get(name,'')
os.chdir(root)
os.execve(python,[python,'-P','-B','-m',module,'--source-commit',source,*operation],env)
'''
