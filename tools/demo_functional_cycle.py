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


def drive(*,opening_task,execute,settle,prepare_reduce,cash_finish,progress):
    result={'schema':'DEMO_FUNCTIONAL_CYCLE_RESULT_V1','status':'RECOVERY_REQUIRED',
        'orders_attempted':0,'final_quantity':None,'fill_count':None,'cash_reconciled':False,
        'economic_authority':False,'retry_submission':False}
    task=opening_task
    try:
        for index in range(3):
            progress('OPEN_IOC' if index==0 else 'REDUCE_ONLY_IOC')
            result['orders_attempted']+=1
            # A lost supervisor response may follow an actual send. Always
            # query the retained original IDs before deciding whether to reduce.
            try:execute(task)
            except Exception:pass
            progress('RECEIPT_RECOVERY')
            position=settle()
            if position.get('status')!='SETTLED':break
            quantity=Decimal(position['quantity'])
            if not quantity.is_finite() or quantity<0:break
            result.update(final_quantity=position['quantity'],fill_count=position['fill_count'])
            if quantity==0:
                result['status']='CASH_RECONCILIATION_REQUIRED' if index>=1 and position['fill_count']>=2 else 'NO_TRADING_EVIDENCE'
                break
            if index==2:
                result['status']='REDUCTION_INCOMPLETE';break
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
    require(operation in ('cash','close-cash','settle','reduce','reconcile'),'FUNCTIONAL_OPERATION')
    command=supervisor.child_command('executor',source_commit=source_commit,registry=registry,purpose='submission',unit=unit)
    command[command.index('tools.demo_execution_session')]='tools.demo_functional_worker'
    command[command.index('RuntimeMaxSec=90s')]='RuntimeMaxSec=840s' if operation=='cash' else 'RuntimeMaxSec=160s'
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


def main(argv=None):
    import argparse
    from live.execution.demo_authority import _read_root_owned_json
    from tools.demo_operating_bundle import write_new
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True);args=parser.parse_args(argv)
    previous=os.umask(0o077);runtime=None;area=None;phase='CONFIGURATION';claimed=False;cycle=None
    try:
        registry,policy=supervisor.configuration(args.source_commit)
        installed=load_json_without_duplicate_keys(_read_root_owned_json(SETUP/'result.json'))
        spec=demo_operator_setup.spec_check(load_json_without_duplicate_keys(_read_root_owned_json(SETUP/'spec.json')))
        require(installed['status']=='INSTALLED_PENDING_CYCLE' and installed['source_commit']==args.source_commit and
            installed['spec_sha256']==demo_operator_setup.digest(spec) and policy['account_ref_sha256']==spec['account_ref_sha256'],
            'FUNCTIONAL_INSTALLATION_BINDING')
        cycle=demo_operator_setup.digest(installed)
        template=load_json_without_duplicate_keys(supervisor.read_document(supervisor.STATE/'tasks',installed['task_sha256']))
        require(demo_operator_setup.digest(template)==installed['task_sha256'],'FUNCTIONAL_INITIAL_TASK')
        demo_task.task_check(template,policy=policy,source_commit=args.source_commit,now_ms=supervisor.now_ms())
        area=supervisor.STATE/'functional-cycle'
        # One enrollment has one cycle. An interrupted or completed run cannot
        # create another opening task by calling this command again.
        area.mkdir(mode=0o700)
        supervisor._root_private_directory(area)
        write_new(area/'claim.json',demo_operator_setup.canonical({'cycle_sha256':cycle,
            'source_commit':args.source_commit,'started_at_ms':supervisor.now_ms()}))
        claimed=True
        runtime=Runtime(args.source_commit,registry,cycle)
        def progress(value):
            nonlocal phase
            phase=value
            _retain(area/'events',{'cycle_sha256':cycle,'phase':value,'observed_at_ms':supervisor.now_ms()})
            if value in demo_functional_status.ACTIVE:
                demo_functional_status.save(area/'views',demo_functional_status.build(phase=value,
                    source_commit=args.source_commit,cycle_sha256=cycle,observed_at_ms=supervisor.now_ms()))
            print(json.dumps({'phase':value,'cycle_sha256':cycle}),flush=True)
        progress('OPENING_CASH_CAPTURE');started=supervisor.now_ms()
        cash=runtime.start('cash',{});deadline=time.monotonic()+30
        while True:
            try:marker=_opening_marker(registry,cycle,args.source_commit,started)
            except FileNotFoundError:
                require(cash.poll() is None and time.monotonic()<deadline,'FUNCTIONAL_OPENING_WALLET_UNAVAILABLE')
                time.sleep(.2);continue
            require(marker['account_ref_sha256']==spec['account_ref_sha256'],'FUNCTIONAL_OPENING_ACCOUNT')
            break
        def execute(task):
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
            require(runtime.invoke('close-cash',timeout=20)['status']=='CLOSE_REQUESTED','FUNCTIONAL_CASH_CLOSE_FAILED')
            capture=runtime.finish(cash,240)
            require(capture['status']=='CAPTURED','FUNCTIONAL_CASH_CAPTURE_INCOMPLETE')
            return runtime.invoke('reconcile')
        result=drive(opening_task=template,execute=execute,settle=lambda:runtime.invoke('settle',timeout=60),
            prepare_reduce=reduce,cash_finish=finish_cash,progress=progress)
        result.update(cycle_sha256=cycle,source_commit=args.source_commit,observed_at_ms=supervisor.now_ms())
        write_new(area/'result.json',demo_operator_setup.canonical(result))
        demo_functional_status.save(area/'views',demo_functional_status.build(phase=result['status'],
            source_commit=args.source_commit,cycle_sha256=cycle,observed_at_ms=result['observed_at_ms'],result=result))
        print(json.dumps(result,sort_keys=True),flush=True)
        return 0 if result['status']=='DEMO_CYCLE_COMPLETED' else 2
    except Exception:
        result={'schema':'DEMO_FUNCTIONAL_CYCLE_RESULT_V1','status':'CYCLE_NOT_COMPLETED','phase':phase,
            'cash_reconciled':False,'economic_authority':False,'retry_submission':False}
        if claimed:
            try:
                _retain(area/'events',dict(result,observed_at_ms=supervisor.now_ms()))
                demo_functional_status.save(area/'views',demo_functional_status.build(phase='CYCLE_NOT_COMPLETED',
                    source_commit=args.source_commit,cycle_sha256=cycle,observed_at_ms=supervisor.now_ms()))
            except Exception:pass
        print(json.dumps(result),flush=True);return 3
    finally:
        if runtime is not None:runtime.close()
        os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
