"""Publish fixed Demo views after permanently dropping to the research UID.

The accepted tool and root-owned publication profile select fixed display
schemas. Original private journals, keys and account payloads are never copied.
Each invocation publishes one view, so missing components cannot hide others.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from live.execution.demo_authority import _read_root_owned_json,_require_protected
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research import demo_market_inputs,demo_executor_observation,demo_issuer_status
from services.research import demo_oms_status,demo_cancel_status,demo_resident_status
from services.research import demo_backup_status,demo_functional_status,demo_cash_chain_status
from tools.demo_issuer import verify_source

PROFILE=Path('/etc/quantos/demo-observation/publication.json')
EXECUTOR=Path('/var/lib/quantos-demo-executor')
ISSUER=Path('/var/lib/quantos-demo-issuer')
CHECKPOINTS=Path('/var/lib/quantos-demo-checkpoints')
CONTROLLER=Path('/var/lib/quantos-demo-controller')
KINDS=('market','executor-observation','issuer','oms','cancellation','resident','backup','functional-cycle','cash-chain')


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def selection(kind,symbol):
    require(kind in KINDS and ((kind=='market')==(symbol is not None)),'PUBLICATION_KIND')
    if kind=='market':
        require(symbol in ('BTCUSDC','BTCUSDT'),'PUBLICATION_QUOTE')
        return demo_market_inputs,EXECUTOR,'market-inputs-'+symbol,'binance_demo_market_inputs/'+symbol,'completed_at_ms'
    return {
        'functional-cycle':(demo_functional_status,CONTROLLER,'functional-cycle/views','binance_demo_functional_cycle','observed_at_ms'),
        'cash-chain':(demo_cash_chain_status,EXECUTOR,'resident-recovery/functional-cash/views','binance_demo_cash_chain','observed_at_ms'),
        'backup':(demo_backup_status,CHECKPOINTS,'views','binance_demo_backup_status','observed_at_ms'),
        'executor-observation':(demo_executor_observation,EXECUTOR,'observation-views','binance_demo_executor_observation','observed_at_ms'),
        'issuer':(demo_issuer_status,ISSUER,'views','binance_demo_issuer_status','observed_at_ms'),
        'oms':(demo_oms_status,EXECUTOR,'receipt-reconciliation/views','binance_demo_oms_status','observed_at_ms'),
        'cancellation':(demo_cancel_status,EXECUTOR,'evidence/cancellations/views','binance_demo_cancel_status','observed_at_ms'),
        'resident':(demo_resident_status,EXECUTOR,'resident-recovery/views','binance_demo_resident_status','observed_at_ms'),
    }[kind]


def configuration(source_commit):
    value=load_json_without_duplicate_keys(_read_root_owned_json(PROFILE))
    require(type(value) is dict and set(value)==set('schema source_commit executor_uid issuer_uid research_uid research_gid research_state_root'.split()),'PUBLICATION_PROFILE')
    require(value['schema']=='DEMO_DISPLAY_PUBLICATION_V1' and value['source_commit']==source_commit,'PUBLICATION_SOURCE')
    require(all(type(value[k]) is int and 0<value[k]<2**31 for k in ('executor_uid','issuer_uid','research_uid','research_gid')) and
        len({value[k] for k in ('executor_uid','issuer_uid','research_uid')})==3 and
        value['research_gid'] not in (value['executor_uid'],value['issuer_uid']),'PUBLICATION_IDENTITIES')
    path=value['research_state_root']
    require(type(path) is str and 0<len(path)<=4096 and '\0' not in path and Path(path).is_absolute() and '..' not in Path(path).parts,'PUBLICATION_RESEARCH_PATH')
    return value


def _owned(info,uid,*,directory,maximum=None):
    require(info.st_uid==uid and not info.st_mode&0o022 and
        (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and
         info.st_nlink==1 and 0<info.st_size<=maximum),'PUBLICATION_SOURCE_CUSTODY')


def _read(fd,name,uid,maximum):
    leaf=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
    try:
        before=os.fstat(leaf);_owned(before,uid,directory=False,maximum=maximum);body=bytearray()
        while len(body)<=maximum:
            chunk=os.read(leaf,min(8192,maximum+1-len(body)))
            if not chunk:break
            body.extend(chunk)
        after=os.fstat(leaf);_owned(after,uid,directory=False,maximum=maximum)
        require(len(body)==before.st_size and all(getattr(before,k)==getattr(after,k) for k in
            ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')),'PUBLICATION_SOURCE_CHANGED')
        return bytes(body)
    finally:os.close(leaf)


def read_snapshot(directory,*,root,uid,model):
    require(directory==root or root in directory.parents,'PUBLICATION_FIXED_SOURCE')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC);current=Path('/')
    try:
        _require_protected(os.fstat(fd),directory=True)
        for name in directory.parts[1:]:
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child;current=current/name
            if current==root or root in current.parents:_owned(os.fstat(fd),uid,directory=True)
            else:_require_protected(os.fstat(fd),directory=True)
        pointer=load_json_without_duplicate_keys(_read(fd,'latest.json',uid,1024))
        require(type(pointer) is dict and set(pointer)=={'sha256'} and type(pointer['sha256']) is str and
            re.fullmatch('[a-f0-9]{64}',pointer['sha256']) is not None,'PUBLICATION_POINTER')
        body=_read(fd,pointer['sha256']+'.json',uid,65536)
        require(hashlib.sha256(body).hexdigest()==pointer['sha256'],'PUBLICATION_DIGEST')
        return model.validate(load_json_without_duplicate_keys(body))
    except OSError:raise ValueError('PUBLICATION_SOURCE_UNAVAILABLE') from None
    finally:os.close(fd)


def drop_identity(uid,gid):
    os.setgroups([]);os.setresgid(gid,gid,gid);os.setresuid(uid,uid,uid)
    require(os.getresuid()==(uid,uid,uid) and os.getresgid()==(gid,gid,gid) and not os.getgroups(),'PUBLICATION_IDENTITY_DROP')


def run(*,source_commit,kind,symbol=None):
    verify_source(source_commit);require(os.geteuid()==0,'PUBLICATION_ROOT_REQUIRED')
    cfg=configuration(source_commit);model,root,relative,cache,time_key=selection(kind,symbol)
    owner=0 if kind in ('backup','functional-cycle') else cfg['issuer_uid' if kind=='issuer' else 'executor_uid']
    raw=read_snapshot(root/relative,root=root,uid=owner,model=model)
    if kind=='market':require(raw['symbol']==symbol,'PUBLICATION_SOURCE_QUOTE')
    drop_identity(cfg['research_uid'],cfg['research_gid'])
    # No user-controlled file or directory is accessed before all saved root
    # identities and supplementary groups have been removed.
    destination=Path(cfg['research_state_root'])/'data/cache'/cache
    if (destination/'latest.json').exists() or (destination/'latest.json').is_symlink():
        require(model.load_latest(destination)[time_key]<=raw[time_key],'PUBLICATION_TIME_REVERSED')
    model.save(destination,raw)
    return {'status':'PUBLISHED_UNVERIFIED_DISPLAY','kind':kind,'observed_at_ms':raw[time_key],
        'phase':raw['status'] if kind=='market' else raw['phase'],'source_verified':False,
        'submission_enabled':False,'cash_reconciled':False,'economic_authority':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True);parser.add_argument('--kind',required=True,choices=KINDS)
    parser.add_argument('--symbol',choices=('BTCUSDC','BTCUSDT'));args=parser.parse_args(argv)
    os.umask(0o077)
    try:result=run(source_commit=args.source_commit,kind=args.kind,symbol=args.symbol)
    except Exception:
        print(json.dumps({'status':'DISPLAY_PUBLICATION_FAILED','submission_enabled':False}));return 3
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
