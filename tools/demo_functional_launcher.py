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
portfolio_modes=['--portfolio-preflight','--portfolio-enroll','--portfolio-prepare','--portfolio-execute',
    '--portfolio-close','--portfolio-reconcile','--portfolio-cycle']
if args not in ([],['--recover'],['--resume'],['--new-cycle']) and args not in [[v] for v in portfolio_modes]:
    raise SystemExit('Use a fixed recovery, successor enrollment, or portfolio operation without extra arguments.')
source='''+repr(source_commit)+'''
root='/opt/quantos/releases/'+source
python='/opt/quantos/venvs/'+source+'/bin/python'
installed=Path('/etc/quantos/demo-operator-setup').exists()
if args and args[0] in portfolio_modes:
    if not installed:raise SystemExit('Complete the first setup before portfolio successor enrollment.')
    if args[0] in ('--portfolio-preflight','--portfolio-enroll'):
        module='tools.demo_successor_setup'
        operation=['--portfolio-config','/etc/quantos/demo-operator-setup/portfolio-proposal.json']
        if args[0]=='--portfolio-enroll':operation.append('--enroll')
    else:
        module='tools.demo_portfolio_controller'
        operation={'--portfolio-prepare':[],'--portfolio-execute':['--execute'],
            '--portfolio-close':['--close','--execute'],'--portfolio-cycle':['--cycle'],
            '--portfolio-reconcile':['--reconcile']}[args[0]]
elif args==['--new-cycle']:
    if not installed:raise SystemExit('Complete the first setup before a successor enrollment.')
    module='tools.demo_successor_setup'
    operation=['--enroll','--run-cycle']
elif args or installed:
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
