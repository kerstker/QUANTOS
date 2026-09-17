"""One finite operator-enrolled Demo cycle through the original task executor.

An opening wallet must be retained before the first task. Confirmed position
quantity determines reductions; unknown delivery never causes a new submission.
"""
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import time

from live.execution.demo_issuer_policy import require
from live.execution.demo_pre_submit import _retain
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research import demo_functional_status
from tools import demo_supervisor as supervisor, demo_task, demo_operator_setup
from tools.demo_execution_session import read_frame

SETUP=Path('/etc/quantos/demo-operator-setup')


def _save_status(area,value):
    demo_functional_status.save(area/'views',value)
    common=supervisor.STATE/'functional-cycle'
    if common!=area:demo_functional_status.save(common/'views',value)


def _stage():
    from tools.demo_successor_setup import active_setup
    return active_setup(root=SETUP)


def drive(*,opening_task,execute,settle,prepare_reduce,cash_finish,progress,
          recovery_checks=1,checkpoint=None,resume=None,allow_new_tasks=True):
    """Persist each attempt before dispatch; a resumed attempt is query-only.

    The original task executor still owns the actual at-most-once send barrier,
    current mandate, issuer and risk checks. This checkpoint cannot grant a send.
    """
    require(type(recovery_checks) is int and 1<=recovery_checks<=3,'FUNCTIONAL_RECOVERY_BUDGET')
    state={'schema':'DEMO_CYCLE_CONTINUATION_V1','attempts':[]} if resume is None else json.loads(json.dumps(resume))
    require(type(state) is dict and set(state)=={'schema','attempts'} and
        state['schema']=='DEMO_CYCLE_CONTINUATION_V1' and type(state['attempts']) is list and
        len(state['attempts'])<=3 and all(type(t) is dict for t in state['attempts']), 'FUNCTIONAL_CONTINUATION_SHAPE')
    attempts=state['attempts']
    require(not attempts or attempts[0]==opening_task,'FUNCTIONAL_CONTINUATION_OPENING')
    require(len({demo_operator_setup.digest(t) for t in attempts})==len(attempts),'FUNCTIONAL_CONTINUATION_DUPLICATE')
    checkpoint=checkpoint or (lambda _:None)
    result={'schema':'DEMO_FUNCTIONAL_CYCLE_RESULT_V1','status':'RECOVERY_REQUIRED',
        'orders_attempted':len(attempts),'final_quantity':None,'fill_count':None,'cash_reconciled':False,
        'economic_authority':False,'retry_submission':False}
    task=attempts[-1] if attempts else opening_task
    pending=bool(attempts)
    try:
        for index in range(max(0,len(attempts)-1),3):
            if not pending:
                if not allow_new_tasks:
                    result['status']='REDUCTION_AUTHORIZATION_REQUIRED';break
                progress('OPEN_IOC' if index==0 else 'REDUCE_ONLY_IOC')
                require(task not in attempts,'FUNCTIONAL_CONTINUATION_DUPLICATE')
                next_state={'schema':state['schema'],'attempts':attempts+[task]}
                checkpoint(next_state)
                attempts=next_state['attempts'];result['orders_attempted']=len(attempts)
            # A lost supervisor response may follow an actual send. Always
            # query the retained original IDs before deciding whether to reduce.
                try:execute(task)
                except Exception:pass
            pending=False
            progress('RECEIPT_RECOVERY')
            position={}
            for _ in range(recovery_checks):
                position=settle()
                if position.get('status') in ('SETTLED','EVIDENCE_REPAIR_REQUIRED'):break
            if position.get('status')=='EVIDENCE_REPAIR_REQUIRED':
                result['status']='EVIDENCE_REPAIR_REQUIRED';break
            if position.get('status')!='SETTLED':break
            quantity=Decimal(position['quantity'])
            if not quantity.is_finite() or quantity<0:break
            result.update(final_quantity=position['quantity'],fill_count=position['fill_count'])
            if quantity==0:
                result['status']='CASH_RECONCILIATION_REQUIRED' if index>=1 and position['fill_count']>=2 else 'NO_TRADING_EVIDENCE'
                break
            if index==2:
                result['status']='REDUCTION_INCOMPLETE';break
            if not allow_new_tasks:
                result['status']='REDUCTION_AUTHORIZATION_REQUIRED';break
            task=prepare_reduce(position,index+1)
    except Exception:
        result['status']='RECOVERY_REQUIRED'
    finally:
        progress('CASH_RECONCILIATION')
        try:cash=cash_finish()
        except Exception:cash={'status':'UNRESOLVED'}
        result['cash_status']=cash.get('status','UNRESOLVED')
        if (result['status']=='CASH_RECONCILIATION_REQUIRED' and result['cash_status']=='MATCHED_REPORTED_INPUTS' and
            cash.get('final_quantity')==result['final_quantity'] and cash.get('fill_count')==result['fill_count']):
            result.update(status='DEMO_CYCLE_COMPLETED',cash_reconciled=True)
    progress(result['status'])
    return result


def child_command(operation,*,source_commit,registry,cycle,unit):
    require(operation in ('cash','close-cash','settle','reduce','reconcile','resume-check','resume-cash','account-recover','account-cash','context','day-checkpoint'),'FUNCTIONAL_OPERATION')
    command=supervisor.child_command('executor',source_commit=source_commit,registry=registry,purpose='submission',unit=unit)
    command[command.index('tools.demo_execution_session')]='tools.demo_functional_worker'
    budget='840s' if operation=='cash' else '420s' if operation in ('account-recover','account-cash','day-checkpoint') else '300s' if operation=='resume-cash' else '160s'
    command[command.index('RuntimeMaxSec=90s')]='RuntimeMaxSec='+budget
    del command[-2:]
    return command+['--operation',operation,'--cycle-sha256',cycle]


class Runtime:
    def __init__(self,source_commit,registry,cycle):
        self.source_commit=source_commit;self.registry=registry;self.cycle=cycle;self.children=[]

    def start(self,operation,payload):
        unit='quantos-demo-executor-'+os.urandom(8).hex()
        process=subprocess.Popen(child_command(operation,source_commit=self.source_commit,
            registry=self.registry,cycle=self.cycle,unit=unit),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,bufsize=0,cwd='/',
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','SYSTEMD_COLORS':'0'})
        self.children.append((unit,process))
        supervisor._send(process.stdin,demo_operator_setup.canonical(payload),time.monotonic()+10)
        process.stdin.close()
        return process

    def finish(self,process,timeout):
        deadline=time.monotonic()+timeout
        body=read_frame(process.stdout.fileno(),maximum=65536,deadline=deadline)
        result=load_json_without_duplicate_keys(body)
        require(type(result) is dict and result.get('cycle_sha256')==self.cycle and
            result.get('source_commit')==self.source_commit,'FUNCTIONAL_CHILD_IDENTITY')
        supervisor._finish(process,deadline)
        return result

    def invoke(self,operation,payload=None,timeout=155):
        return self.finish(self.start(operation,payload or {}),timeout)

    def close(self):
        for unit,process in self.children:
            try:
                if process.poll() is None:
                    subprocess.run(['systemctl','stop',unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                        timeout=10,check=False,env={'PATH':'/usr/bin:/bin'})
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            except (OSError,subprocess.TimeoutExpired):pass
            for channel in (process.stdin,process.stdout):
                if channel is not None:channel.close()


def _opening_marker(registry,cycle,source_commit,started_ms):
    from tools.demo_protected_publish import _read,_owned
    from live.execution.demo_authority import _require_protected
    root=Path('/var/lib/quantos-demo-executor')
    directory=root/'resident-recovery/functional-cycles'/cycle
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC);current=Path('/')
    try:
        _require_protected(os.fstat(fd),directory=True)
        for part in directory.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child;current=current/part
            if current==root or root in current.parents:_owned(os.fstat(fd),registry['executor_uid'],directory=True)
            else:_require_protected(os.fstat(fd),directory=True)
        marker=load_json_without_duplicate_keys(_read(fd,'cash-opening.json',registry['executor_uid'],65536))
        require(marker.get('schema')=='DEMO_FUNCTIONAL_CASH_OPENING_V1' and marker.get('source_commit')==source_commit and
            type(marker.get('opened_at_ms')) is int and started_ms<=marker['opened_at_ms']<=supervisor.now_ms() and
            supervisor.now_ms()-marker['opened_at_ms']<=15000,'FUNCTIONAL_OPENING_WALLET_TIME')
        prefix_fd=os.open('cash-prefix',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
        try:
            _owned(os.fstat(prefix_fd),registry['executor_uid'],directory=True)
            identity=marker['capture_prefix_sha256']
            from services.validation.demo_submission import _digest
            _digest(identity)
            prefix=load_json_without_duplicate_keys(_read(prefix_fd,identity+'.json',registry['executor_uid'],65536))
        finally:os.close(prefix_fd)
        require(demo_operator_setup.digest(prefix)==identity and
            [r['kind'] for r in prefix['reads']]==['clock_before','wallet_before'] and
            prefix['reads'][-1]['received_at_ms']==marker['opened_at_ms'] and
            prefix['account_ref_sha256']==marker['account_ref_sha256'],'FUNCTIONAL_OPENING_WALLET_BINDING')
        return marker
    finally:os.close(fd)


def recover(*,source_commit):
    """Recover an already claimed cycle without asking the issuer for any grant.

    Expiry stops new exposure, not original-ID reads or cash reconciliation.
    The old result remains immutable; subsequent reports are appended separately.
    """
    from contextlib import ExitStack
    from live.execution.demo_authority import _read_root_owned_json
    from live.execution.demo_issuer_policy import validate_policy
    from services.validation.demo_submission import validate_registry,registry_sha256
    from tools.demo_issuer import verify_source
    from tools import demo_cycle_checkpoint as checkpoints
    require(os.geteuid()==0,'FUNCTIONAL_RECOVERY_ROOT_REQUIRED');verify_source(source_commit)
    registry=validate_registry(load_json_without_duplicate_keys(_read_root_owned_json(supervisor.EXECUTOR_REGISTRY)))
    require(load_json_without_duplicate_keys(_read_root_owned_json(supervisor.ISSUER_REGISTRY))==registry,
        'FUNCTIONAL_RECOVERY_REGISTRY_MIRROR')
    policy=validate_policy(load_json_without_duplicate_keys(_read_root_owned_json(supervisor.ISSUER_POLICY)))
    require(policy['registry_sha256']==registry_sha256(registry) and
        policy['deployment_sha256']==registry['deployment_sha256'],'FUNCTIONAL_RECOVERY_REGISTRY')
    stage=_stage()
    installed=load_json_without_duplicate_keys(_read_root_owned_json(stage/'result.json'))
    spec=demo_operator_setup.spec_check(load_json_without_duplicate_keys(_read_root_owned_json(stage/'spec.json')))
    require(installed['status']=='INSTALLED_PENDING_CYCLE' and installed['source_commit']==spec['source_commit']==
        policy['executor_release_commit'] and installed['spec_sha256']==demo_operator_setup.digest(spec) and
        policy['account_ref_sha256']==spec['account_ref_sha256'],'FUNCTIONAL_RECOVERY_INSTALLATION')
    cycle=demo_operator_setup.digest(installed);area=supervisor.STATE/'functional-cycle'
    if stage!=SETUP:area=area/'cycles'/cycle
    with checkpoints.cycle_lock(area):
        history=area/'recovery-results'
        if history.exists():
            supervisor._root_private_directory(history)
            require(len(list(history.iterdir()))<32,'FUNCTIONAL_RECOVERY_HISTORY_BUDGET')
        claim=load_json_without_duplicate_keys(_read_root_owned_json(area/'claim.json'))
        require(claim['cycle_sha256']==cycle and claim['source_commit']==installed['source_commit'],
            'FUNCTIONAL_RECOVERY_CLAIM')
        template=load_json_without_duplicate_keys(supervisor.read_document(supervisor.STATE/'tasks',installed['task_sha256']))
        require(demo_operator_setup.digest(template)==installed['task_sha256'],'FUNCTIONAL_RECOVERY_TASK')
        resume=checkpoints.read(area,cycle=cycle,source_commit=installed['source_commit'],opening_task=template)
        if not resume or not resume['attempts']:
            resume=checkpoints.legacy_attempts(opening_task=template,source_commit=installed['source_commit'])
        with ExitStack() as stack:
            runtime=Runtime(source_commit,registry,cycle);stack.callback(runtime.close)
            def progress(phase):
                _retain(area/'events',{'cycle_sha256':cycle,'phase':phase,'observed_at_ms':supervisor.now_ms(),
                    'source_commit':source_commit,'mode':'RECOVERY_ONLY'})
                print(json.dumps({'phase':phase,'cycle_sha256':cycle,'mode':'RECOVERY_ONLY'}),flush=True)
            def no_submission(*_):raise ValueError('FUNCTIONAL_RECOVERY_CANNOT_SUBMIT')
            result=drive(opening_task=template,execute=no_submission,settle=lambda:runtime.invoke('settle',timeout=60),
                prepare_reduce=no_submission,cash_finish=lambda:runtime.invoke('reconcile'),progress=progress,
                recovery_checks=3,resume=resume,allow_new_tasks=False)
            if result['status'] in ('EVIDENCE_REPAIR_REQUIRED','NO_TRADING_EVIDENCE'):
                try:recovered=runtime.invoke('account-recover',timeout=415)
                except Exception:recovered={}
                if recovered.get('status')=='MANUAL_ACCOUNT_RECONCILED':
                    result.update({k:recovered[k] for k in ('status','final_quantity','fill_count','cash_status','cash_reconciled')})
            result.update(cycle_sha256=cycle,source_commit=source_commit,observed_at_ms=supervisor.now_ms(),
                mode='RECOVERY_ONLY',original_source_commit=installed['source_commit'])
            _retain(area/'recovery-results',result)
            _save_status(area,demo_functional_status.build(phase=result['status'],
                source_commit=source_commit,cycle_sha256=cycle,observed_at_ms=result['observed_at_ms'],result=result))
            print(json.dumps(result,sort_keys=True),flush=True)
            return 0 if result['status']=='DEMO_CYCLE_COMPLETED' else 2


def main(argv=None):
    import argparse
    from live.execution.demo_authority import _read_root_owned_json
    from tools.demo_operating_bundle import write_new
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--recover',action='store_true',help='Original-ID recovery only; never submits or renews an order.')
    mode.add_argument('--resume',action='store_true',help='Continue a claimed cycle within its original active finite mandate.')
    args=parser.parse_args(argv)
    previous=os.umask(0o077);runtime=None;area=None;phase='CONFIGURATION';claimed=False;cycle=None;lease=None
    try:
        if args.recover:return recover(source_commit=args.source_commit)
        registry,policy=supervisor.configuration(args.source_commit)
        stage=_stage()
        installed=load_json_without_duplicate_keys(_read_root_owned_json(stage/'result.json'))
        spec=demo_operator_setup.spec_check(load_json_without_duplicate_keys(_read_root_owned_json(stage/'spec.json')))
        require(installed['status']=='INSTALLED_PENDING_CYCLE' and installed['source_commit']==args.source_commit and
            installed['spec_sha256']==demo_operator_setup.digest(spec) and policy['account_ref_sha256']==spec['account_ref_sha256'],
            'FUNCTIONAL_INSTALLATION_BINDING')
        cycle=demo_operator_setup.digest(installed)
        template=load_json_without_duplicate_keys(supervisor.read_document(supervisor.STATE/'tasks',installed['task_sha256']))
        require(demo_operator_setup.digest(template)==installed['task_sha256'],'FUNCTIONAL_INITIAL_TASK')
        demo_task.task_check(template,policy=policy,source_commit=args.source_commit,now_ms=supervisor.now_ms())
        area=supervisor.STATE/'functional-cycle'
        if stage!=SETUP:
            parent=area/'cycles'
            parent.mkdir(mode=0o700,exist_ok=True);supervisor._root_private_directory(parent)
            area=parent/cycle
        # One enrollment has one cycle. An interrupted or completed run cannot
        # create another opening task by calling this command again.
        if not args.resume:area.mkdir(mode=0o700)
        supervisor._root_private_directory(area)
        from tools import demo_cycle_checkpoint as checkpoints
        lease=checkpoints.cycle_lock(area);lease.__enter__()
        resume=None
        if args.resume:
            claim=load_json_without_duplicate_keys(_read_root_owned_json(area/'claim.json'))
            require(claim['cycle_sha256']==cycle and claim['source_commit']==args.source_commit,'FUNCTIONAL_RESUME_CLAIM')
            resume=checkpoints.read(area,cycle=cycle,source_commit=args.source_commit,opening_task=template)
            require(resume and resume['attempts'],'FUNCTIONAL_RESUME_CHECKPOINT_REQUIRED')
            if (area/'result.json').exists():
                prior=load_json_without_duplicate_keys(_read_root_owned_json(area/'result.json'))
                require(prior['status']!='DEMO_CYCLE_COMPLETED','FUNCTIONAL_ALREADY_COMPLETED')
        else:
            write_new(area/'claim.json',demo_operator_setup.canonical({'cycle_sha256':cycle,
                'source_commit':args.source_commit,'started_at_ms':supervisor.now_ms()}))
        claimed=True
        runtime=Runtime(args.source_commit,registry,cycle)
        def progress(value):
            nonlocal phase
            phase=value
            _retain(area/'events',{'cycle_sha256':cycle,'phase':value,'observed_at_ms':supervisor.now_ms()})
            if value in demo_functional_status.ACTIVE:
                _save_status(area,demo_functional_status.build(phase=value,
                    source_commit=args.source_commit,cycle_sha256=cycle,observed_at_ms=supervisor.now_ms()))
            print(json.dumps({'phase':value,'cycle_sha256':cycle}),flush=True)
        if args.resume:
            require(runtime.invoke('resume-check')['status']=='ORIGINAL_OPENING_RETAINED','FUNCTIONAL_ORIGINAL_CASH_REQUIRED')
            cash=None
        else:
            progress('OPENING_CASH_CAPTURE');started=supervisor.now_ms()
            cash=runtime.start('cash',{'strategy_id':template['profile']['strategy']['strategy_id']});deadline=time.monotonic()+30
            while True:
                try:marker=_opening_marker(registry,cycle,args.source_commit,started)
                except FileNotFoundError:
                    require(cash.poll() is None and time.monotonic()<deadline,'FUNCTIONAL_OPENING_WALLET_UNAVAILABLE')
                    time.sleep(.2);continue
                require(marker['account_ref_sha256']==spec['account_ref_sha256'],'FUNCTIONAL_OPENING_ACCOUNT')
                break
        def execute(task):
            if cash is not None:
                require(cash.poll() is None,'FUNCTIONAL_CASH_CAPTURE_ALREADY_ENDED')
            identity=demo_operator_setup.digest(task)
            if identity!=installed['task_sha256']:
                demo_task.task_check(task,policy=policy,source_commit=args.source_commit,now_ms=supervisor.now_ms())
                write_new(supervisor.STATE/'tasks'/(identity+'.json'),demo_operator_setup.canonical(task))
            return demo_task.run(source_commit=args.source_commit,task_sha256=identity)
        def reduce(position,index):
            value=runtime.invoke('reduce',{'template':template,'spec':spec,'index':index})
            require(value['status']=='REDUCTION_TASK_PREPARED','FUNCTIONAL_REDUCTION_UNAVAILABLE')
            require(value['task']['orders'][0]['quantity']==position['quantity'],'FUNCTIONAL_POSITION_CHANGED')
            return value['task']
        def finish_cash():
            if args.resume:
                capture=runtime.invoke('resume-cash',timeout=295)
            else:
                require(runtime.invoke('close-cash',timeout=20)['status']=='CLOSE_REQUESTED','FUNCTIONAL_CASH_CLOSE_FAILED')
                capture=runtime.finish(cash,240)
            if capture['status']=='CAPTURED':
                try:
                    comparison=runtime.invoke('reconcile')
                    if comparison['status']=='MATCHED_REPORTED_INPUTS':return comparison
                except Exception:pass
            # Per-order receipt windows need not cover the whole cash window.
            # The fixed-account alternative must independently match the
            # already completed canonical book and this cycle's system fills.
            return runtime.invoke('account-cash',timeout=415)
        result=drive(opening_task=template,execute=execute,settle=lambda:runtime.invoke('settle',timeout=60),
            prepare_reduce=reduce,cash_finish=finish_cash,progress=progress,recovery_checks=3,
            resume=resume,
            checkpoint=lambda state:checkpoints.append(area,state,cycle=cycle,
                source_commit=args.source_commit,opening_task=template))
        result.update(cycle_sha256=cycle,source_commit=args.source_commit,observed_at_ms=supervisor.now_ms())
        if args.resume:_retain(area/'recovery-results',result)
        else:write_new(area/'result.json',demo_operator_setup.canonical(result))
        _save_status(area,demo_functional_status.build(phase=result['status'],
            source_commit=args.source_commit,cycle_sha256=cycle,observed_at_ms=result['observed_at_ms'],result=result))
        print(json.dumps(result,sort_keys=True),flush=True)
        return 0 if result['status']=='DEMO_CYCLE_COMPLETED' else 2
    except Exception as error:
        result={'schema':'DEMO_FUNCTIONAL_CYCLE_RESULT_V1','status':'CYCLE_NOT_COMPLETED','phase':phase,
            'cash_reconciled':False,'economic_authority':False,'retry_submission':False}
        safe_reasons={'ISSUER_POLICY_EXPIRED','ISSUER_KEY_EXPIRED','FUNCTIONAL_CYCLE_BUSY',
            'FUNCTIONAL_RESUME_CHECKPOINT_REQUIRED','FUNCTIONAL_ALREADY_COMPLETED',
            'FUNCTIONAL_ORIGINAL_CASH_REQUIRED','FUNCTIONAL_LEGACY_ATTEMPTS_REQUIRED'}
        result['reason']=str(error) if isinstance(error,ValueError) and str(error) in safe_reasons else 'FUNCTIONAL_INPUT_OR_WORKER_UNAVAILABLE'
        if claimed:
            try:
                _retain(area/'events',dict(result,observed_at_ms=supervisor.now_ms()))
                _save_status(area,demo_functional_status.build(phase='CYCLE_NOT_COMPLETED',
                    source_commit=args.source_commit,cycle_sha256=cycle,observed_at_ms=supervisor.now_ms()))
            except Exception:pass
        print(json.dumps(result),flush=True);return 3
    finally:
        if runtime is not None:runtime.close()
        if lease is not None:lease.__exit__(None,None,None)
        os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
