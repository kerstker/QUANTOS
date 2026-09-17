"""Concrete first-use Demo proposal and operator-owned installation materials.

Proposal construction is offline and unsigned. Enrollment is a separate local
operator operation; it does not claim a profitable strategy or reset stop state.
The original installer, independent issuer, order gates and ledger stay in use.
"""
import base64
from dataclasses import asdict
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import re

from live.execution.demo_batch_risk import _json_value
from live.execution.demo_issuer_policy import require
from live.execution.demo_pre_submit import _venue_rules, _venue_account_cap
from services.execution.perpetual_ledger_v2 import ledger_decimal_context, canonical_decimal
from services.research.demo_execution_origin import assess
from services.research.demo_market_inputs import VOLATILITY_POLICY
from services.risk.pre_trade.config import RiskLimits
from services.validation.demo_submission import validate_registry, registry_sha256
from tools.binance_demo_market_inputs import source_hash as market_source_hash
from tools import demo_operating_bundle as installer

SCHEMA='DEMO_FUNCTIONAL_SETUP_SPEC_V1'
PORTFOLIO_SCHEMA='DEMO_PORTFOLIO_SETUP_SPEC_V1'
PURPOSE='DEMO_FUNCTIONAL_TEST_ONLY'
FIXED={
    'schema':SCHEMA,'environment':'BINANCE_USDM_DEMO','symbol':'BTCUSDC',
    'status':'AWAITING_OPERATOR_ENROLLMENT','max_order_notional':'250',
    'max_total_notional':'750','max_quantity':'0.01','daily_loss_limit':'10',
    'window_seconds':3600,'max_orders':3,'max_cancel_orders':3,'ioc_price_offset_bps':5,
    'steps':['OPEN_IOC','REDUCE_ONLY_IOC','RECONCILE'],
    'orders_submitted':False,'economic_authority':False,
}


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def encoded(value):return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def spec_check(spec):
    portfolio=type(spec) is dict and spec.get('schema')==PORTFOLIO_SCHEMA
    expected={**FIXED,**({'schema':PORTFOLIO_SCHEMA,'steps':['PORTFOLIO_DECISION','REDUCE_ONLY_IOC','RECONCILE']} if portfolio else {})}
    require(type(spec) is dict and set(spec)==set(expected)|{
        'source_commit','account_ref_sha256','registry_sha256','observed_origin_sha256'}|({'portfolio'} if portfolio else set()),'SETUP_SPEC_FIELDS')
    for key,value in expected.items():require(type(spec[key]) is type(value) and spec[key]==value,'SETUP_SPEC_BOUNDARY')
    require(type(spec['source_commit']) is str and re.fullmatch('[a-f0-9]{40}',spec['source_commit']),'SETUP_SOURCE')
    for key in ('account_ref_sha256','registry_sha256','observed_origin_sha256'):
        require(type(spec[key]) is str and re.fullmatch('[a-f0-9]{64}',spec[key]),'SETUP_BINDING')
    if portfolio:
        from pathlib import Path
        from live.execution.demo_portfolio_task import strategy_id
        value=spec['portfolio']
        require(type(value) is dict and set(value)=={'allocation','binding','research_root'},'SETUP_PORTFOLIO_FIELDS')
        allocation=value['allocation'];strategy_id(allocation)
        require(allocation['schema']=='DEMO_PORTFOLIO_ALLOCATION_V2' and
            allocation['source_commit']==spec['source_commit'] and
            allocation['account_ref_sha256']==spec['account_ref_sha256'] and
            allocation['source_binding_sha256']==digest(value['binding']) and
            Decimal(allocation['capital'])<=Decimal(spec['max_order_notional']) and
            allocation['ioc_offset_bps']==spec['ioc_price_offset_bps'],'SETUP_PORTFOLIO_SCOPE')
        binding=value['binding']
        require(type(binding) is dict and set(binding)=={'schema','attempt','freeze_sha256','observer_source_sha256'} and
            binding['schema']=='DEMO_PORTFOLIO_SOURCE_BINDING_V1' and type(binding['attempt']) is int and
            binding['attempt']>0 and all(type(binding[k]) is str and re.fullmatch('[a-f0-9]{64}',binding[k])
            for k in ('freeze_sha256','observer_source_sha256')),'SETUP_PORTFOLIO_BINDING')
        root=value['research_root']
        require(type(root) is str and len(root)<=4096 and Path(root).is_absolute() and '..' not in Path(root).parts,
            'SETUP_PORTFOLIO_ROOT')
    return spec


def _first_origin(origin,now_ms):
    report=assess(origin,as_of_ms=now_ms)
    require(origin['symbol']=='BTCUSDC' and report['status']=='OBSERVED_FLAT_QUOTE','SETUP_FLAT_USDC_REQUIRED')
    require(Decimal(report['wallet'])>=Decimal('2500'),'SETUP_DEMO_WALLET_TOO_SMALL')
    return report


def _continuation(origin,registry,context,now_ms):
    from live.execution.demo_position_context import validate_origin
    require(context is not None and registry['keys'] and
        all(k['valid_until_ms'] <= now_ms for k in registry['keys']), 'SETUP_EXPIRED_PRIOR_ENROLLMENT_REQUIRED')
    require(context['quantity']=='0','SETUP_FLAT_CONTINUATION_REQUIRED')
    return validate_origin(origin,context,as_of_ms=now_ms)


def propose(*,origin,registry,source_commit,now_ms,position_context=None,portfolio=None):
    validate_registry(registry);_first_origin(origin,now_ms)
    if position_context is None:require(not registry['keys'],'SETUP_FIRST_ENROLLMENT_ONLY')
    else:_continuation(origin,registry,position_context,now_ms)
    extra={} if portfolio is None else {'schema':PORTFOLIO_SCHEMA,'portfolio':json.loads(canonical(portfolio)),
        'steps':['PORTFOLIO_DECISION','REDUCE_ONLY_IOC','RECONCILE']}
    spec=spec_check({**json.loads(canonical(FIXED)),**extra, 'source_commit':source_commit,
        'account_ref_sha256':origin['account_ref_sha256'],'registry_sha256':registry_sha256(registry),
        'observed_origin_sha256':digest(origin)})
    # The example order checks feasibility but is neither stored nor authorized.
    if portfolio is None:opening_order(origin,spec,client_order_id='QDsetupfeasibility',now_ms=now_ms)
    return spec


def _ceil_grid(value,minimum,step):
    return minimum+max(Decimal(0),((value-minimum)/step).to_integral_value(rounding=ROUND_CEILING))*step


@ledger_decimal_context()
def opening_order(origin,spec,*,client_order_id,now_ms):
    spec_check(spec);_first_origin(origin,now_ms)
    require(spec['schema']==SCHEMA,'SETUP_FIXED_OPENING_NOT_PORTFOLIO')
    require(origin['account_ref_sha256']==spec['account_ref_sha256'],'SETUP_ACCOUNT_CHANGED')
    data={r['kind']:r['data'] for r in origin['reads']}
    filters={f['filterType']:f for f in data['rules']['terms']['filters']}
    tick,lot=filters['PRICE_FILTER'],filters['LOT_SIZE']
    price=_ceil_grid(Decimal(data['book']['askPrice'])*(1+Decimal(spec['ioc_price_offset_bps'])/10000),
        Decimal(tick['minPrice']),Decimal(tick['tickSize']))
    quantity=_ceil_grid(Decimal(filters['MIN_NOTIONAL']['notional'])/price,Decimal(lot['minQty']),Decimal(lot['stepSize']))
    require(quantity<=Decimal(spec['max_quantity']) and quantity*price<=Decimal(spec['max_order_notional']),
        'SETUP_MINIMUM_ORDER_EXCEEDS_BUDGET')
    order={'symbol':'BTCUSDC','side':'BUY','type':'LIMIT','timeInForce':'IOC',
        'quantity':canonical_decimal(quantity),'price':canonical_decimal(price),'reduceOnly':False,
        'positionSide':'BOTH','newClientOrderId':client_order_id,'newOrderRespType':'ACK'}
    _venue_rules([order],origin);_venue_account_cap([order],origin)
    return order


def _approval_check(approval,spec,now_ms):
    require(type(approval) is dict and set(approval)=={
        'schema','spec_sha256','operator_id','approved_at_ms','purpose'},'SETUP_OPERATOR_RECORD_REQUIRED')
    purpose='DEMO_PORTFOLIO_TEST_ONLY' if spec['schema']==PORTFOLIO_SCHEMA else PURPOSE
    require(approval['schema']=='DEMO_OPERATOR_ENROLLMENT_V1' and approval['purpose']==purpose and
        approval['spec_sha256']==digest(spec),'SETUP_OPERATOR_SCOPE')
    name=approval['operator_id']
    require(type(name) is str and re.fullmatch('[A-Za-z0-9_.@-]{1,96}',name) and
        name.upper() not in ('AI','AUTO','SYSTEM','AGENT','CODEX'),'SETUP_OPERATOR_IDENTITY')
    at=approval['approved_at_ms']
    require(type(at) is int and 0<=now_ms-at<spec['window_seconds']*1000,'SETUP_OPERATOR_WINDOW')


def assemble(*,spec,approval,origin,registry,private_key,stop_image,now_ms,position_context=None):
    """Called after operator enrollment, with an existing stop image unchanged.

    This pure assembly function does not authenticate humans. The runtime CLI
    establishes the operator operation and protected custody before calling it.
    """
    spec_check(spec);_approval_check(approval,spec,now_ms);validate_registry(registry)
    require(registry_sha256(registry)==spec['registry_sha256'],'SETUP_REGISTRY_CHANGED')
    if position_context is None:require(not registry['keys'],'SETUP_FIRST_ENROLLMENT_ONLY')
    else:_continuation(origin,registry,position_context,now_ms)
    report=_first_origin(origin,now_ms)
    require(origin['account_ref_sha256']==spec['account_ref_sha256'],'SETUP_ACCOUNT_CHANGED')
    at=approval['approved_at_ms'];until=at+spec['window_seconds']*1000
    identity=digest(approval);sid='demo-functional-'+identity[:16]
    portfolio=spec.get('portfolio')
    if portfolio is not None:
        from live.execution.demo_portfolio_task import strategy_id
        sid=strategy_id(portfolio['allocation'])
    public=private_key.public_key().public_bytes_raw()
    key={'authority_id':'demo-operator-'+identity[:16],'key_id':'key-'+identity[:16],
        'public_key_base64url':encoded(public),'public_key_sha256':hashlib.sha256(public).hexdigest(),
        'status':'ACTIVE','valid_from_ms':at,'valid_until_ms':until}
    expanded=json.loads(canonical(registry));expanded['keys'].append(key)
    limits=RiskLimits(allowed_instruments=frozenset({'BTCUSDC'}),allowed_accounts=frozenset({spec['account_ref_sha256']}),
        default_max_position_qty=.01,max_position_qty=(('BTCUSDC',.01),),fat_finger_qty=.01,
        max_order_notional=250,max_gross_exposure=250,max_net_exposure=250,max_price_deviation_pct=.01,
        max_leverage=.1,max_single_name_pct=.1,daily_loss_limit=10,max_drawdown=.01,
        max_turnover=.3,max_orders_per_window=3,rate_window_ms=60000,max_cancel_rate=.8,
        min_cancel_rate_samples=20,max_participation_rate=.001,max_instrument_vol=.8,cash_buffer_pct=.9)
    mandate={'mandate_id':'demo-mandate-'+identity[:16],'venue':'testnet','exchange':'binance',
        'allowed_instruments':['BTCUSDC'],'max_single_order_notional':250,'max_gross_notional':250,
        'max_net_notional':250,'per_instrument_max_notional':[['BTCUSDC',250]],
        'approved_by':[approval['operator_id']],'issued_at':at,'expires_at':until}
    attestation={'strategy_id':sid,'kind':'human_override','attested_by':approval['operator_id'],
        'attested_at':at,'reason':'Finite Demo functional test only; operator enrollment '+identity+
        '; no profitability or mainnet validation.','validation_result_hash':''}
    profile={'schema':'DEMO_REQUEST_PROFILE_V1','environment':'BINANCE_USDM_DEMO','symbol':'BTCUSDC',
        'market_source_sha256':market_source_hash(),'volatility_policy':VOLATILITY_POLICY,
        'native_limits':_json_value(asdict(limits)),
        'strategy':{'strategy_id':sid,'approved':True,'approval_id':identity},'mandate':mandate,
        'attestations':{sid:attestation},'initial_day_pnl':'0','initial_peak_equity':report['wallet'],
        'minimum_plan_turnover':0}
    if position_context is not None:
        from live.execution.demo_position_context import continuation_risk
        account=_continuation(origin,registry,position_context,now_ms)
        profile['initial_day_pnl']=account['day_pnl']
        profile['initial_peak_equity']=canonical_decimal(max(Decimal(position_context['initial_equity']),
            Decimal(position_context['peak_equity_floor']),Decimal(account['equity'])))
        profile['minimum_plan_turnover']=continuation_risk({'account':account,'plan_turnover':0},position_context)['plan_turnover']
    policy={'schema':'DEMO_ISSUER_POLICY_V1','environment':'BINANCE_USDM_DEMO','policy_id':'demo-policy-'+identity[:16],
        'deployment_sha256':registry['deployment_sha256'],'registry_sha256':registry_sha256(expanded),
        'executor_release_commit':spec['source_commit'],'account_ref_sha256':spec['account_ref_sha256'],
        'authority_id':key['authority_id'],'key_id':key['key_id'],'valid_from_ms':at,'valid_until_ms':until,
        'grant_ttl_ms':30000,'max_grants':3,'max_orders':3,'max_total_notional':'750',
        'max_cancel_grants':3,'max_cancel_orders':3,
        'scope':{'symbol':'BTCUSDC','sides':['BUY','SELL'],'time_in_force':['IOC'],
            'allow_opening':True,'max_quantity_per_order':spec['max_quantity'],
            'max_notional_per_order':'250','max_notional_per_grant':'250'},
        'bindings':{'strategy_id':sid,'mandate_sha256':digest(mandate),'validation_sha256':digest(profile['attestations']),
            'native_limits_sha256':digest(profile['native_limits']),'strategy_approval_sha256':digest(profile['strategy'])}}
    # Read the supplied image through the existing store verifier. No state is
    # synthesized from a missing file and no arm/reset operation is performed.
    import tempfile
    from pathlib import Path
    from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore
    with tempfile.TemporaryDirectory(prefix='demo-operator-stop-') as directory:
        path=Path(directory)/'stop.sqlite3';path.write_bytes(stop_image);path.chmod(0o600)
        store=KillSwitchStateStore(str(path),read_only=True)
        try:snapshot=store.verified_snapshot()
        finally:store.close()
    require(not snapshot['state'].tripped,'SETUP_STOP_ACTIVE')
    require(max(snapshot['recorded_at'],snapshot['state'].armed_at,snapshot['state'].reset_at)<=now_ms,'SETUP_STOP_FUTURE')
    documents={'registry.json':expanded,'policy.json':policy,'profile.json':profile,
        'signing-key.json':{'schema':'DEMO_ISSUER_KEY_V1','authority_id':key['authority_id'],'key_id':key['key_id'],
            'private_key_base64url':encoded(private_key.private_bytes_raw())}}
    tasks=[]
    if portfolio is not None:
        documents['portfolio-runtime.json']={'schema':'DEMO_PORTFOLIO_RUNTIME_V1','source_commit':spec['source_commit'],
            **portfolio,'kill_switch':snapshot['state'].model_dump(mode='json')}
    else:
        task={'schema':'DEMO_REQUEST_TASK_V1','source_commit':spec['source_commit'],'valid_from_ms':at,'valid_until_ms':until,
            'issuer_policy_sha256':digest(policy),'profile':profile,'kill_switch':snapshot['state'].model_dump(mode='json'),
            'orders':[opening_order(origin,spec,client_order_id='QD'+identity[:24]+'open',now_ms=now_ms)]}
        tasks=[digest(task)];documents[digest(task)+'.json']=task
    files={name:canonical(value) for name,value in documents.items()};files['kill-switch.sqlite3']=stop_image
    manifest={'schema':'DEMO_PORTFOLIO_OPERATING_BUNDLE_V1' if portfolio is not None else 'DEMO_OPERATING_BUNDLE_V1','source_commit':spec['source_commit'],
        'expected_registry_sha256':registry_sha256(registry),'files':{name:hashlib.sha256(body).hexdigest() for name,body in files.items()},
        'tasks':tasks}
    installer.validate_materials(manifest=manifest,files=files,existing_registry=registry,
        source_commit=spec['source_commit'],account_ref=spec['account_ref_sha256'],now_ms=now_ms)
    return manifest,files


def _runtime_inputs(source_commit,*,continuation=False):
    """Obtain fresh originals under the existing executor UID, without secrets."""
    import os
    from pathlib import Path
    import subprocess
    import sys
    from live.execution.demo_authority import _read_root_owned_json
    from services.governance.json_integrity import load_json_without_duplicate_keys
    from services.research.demo_execution_origin import load
    from tools.demo_resident_recovery import _check_path
    from tools.demo_issuer import verify_source
    verify_source(source_commit);require(os.geteuid()==0,'SETUP_ROOT_REQUIRED')
    registry=validate_registry(load_json_without_duplicate_keys(_read_root_owned_json(installer.REGISTRY)))
    if not continuation:require(not registry['keys'],'SETUP_FIRST_ENROLLMENT_ONLY')
    uid=registry['executor_uid'];root=Path('/opt/quantos/releases')/source_commit
    child=subprocess.run([sys.executable,'-P','-B','-m','tools.demo_executor_origin',
        '--source-commit',source_commit,'--symbol','BTCUSDC'],user=uid,group=uid,extra_groups=[],
        cwd=root,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONPATH':str(root),
            'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'},stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=135,check=False)
    require(child.returncode==0 and len(child.stdout)<=65536,'SETUP_ORIGIN_CAPTURE_FAILED')
    result=load_json_without_duplicate_keys(child.stdout)
    require(result.get('status')=='CAPTURED' and result.get('source_commit')==source_commit,'SETUP_ORIGIN_SOURCE')
    path=Path(result['path']);directory=installer.EXECUTOR/'execution-origins'/'BTCUSDC'
    require(path.parent==directory and re.fullmatch(r'[a-f0-9]{64}\.json',path.name),'SETUP_ORIGIN_PATH')
    for parent in (installer.EXECUTOR,directory.parent,directory):_check_path(parent,uid,directory=True)
    _check_path(path,uid,directory=False)
    return registry,load(path)


def _stop_image(*,path=None):
    """Copy existing quiescent stop bytes, never initialize or reset them."""
    import os
    from pathlib import Path
    import stat
    path=Path('/srv/quantos/state/data/cache/live_kill_switch.db') if path is None else Path(path)
    # Nonempty sidecars require the original backup/export path; never pretend
    # copying a live WAL database's base file is a consistent state snapshot.
    for suffix in ('-wal','-journal'):
        sidecar=Path(str(path)+suffix)
        require(not sidecar.is_symlink() and (not sidecar.exists() or sidecar.stat().st_size==0),'SETUP_STOP_EXPORT_REQUIRED')
    require(not any(p.is_symlink() for p in (path,*path.parents)),'SETUP_STOP_SOURCE_LINK')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as handle:
        before=os.fstat(handle.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and
            not before.st_mode&0o022 and 0<before.st_size<=64*1024*1024,'SETUP_STOP_CUSTODY')
        body=handle.read(64*1024*1024+1);after=os.fstat(handle.fileno())
    require(len(body)==before.st_size and all(getattr(before,k)==getattr(after,k) for k in
        ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')),'SETUP_STOP_CHANGED')
    for suffix in ('-wal','-journal'):
        sidecar=Path(str(path)+suffix)
        require(not sidecar.is_symlink() and (not sidecar.exists() or sidecar.stat().st_size==0),'SETUP_STOP_EXPORT_REQUIRED')
    return body


def operator_confirmation(spec):
    """A local operator invokes this step; the research agent must not do so.

    Sudo/TTY establishes the host operation, not an AI-generated actor label.
    This is not a cryptographic defense against trusted root code (AGENTS.md).
    """
    import os
    import pwd
    import sys
    require(os.geteuid()==0 and sys.stdin.isatty() and sys.stdout.isatty(),'SETUP_LOCAL_OPERATOR_REQUIRED')
    raw=os.environ.get('SUDO_UID','')
    require(raw.isdigit() and int(raw)>0,'SETUP_SUDO_OPERATOR_REQUIRED')
    operator=pwd.getpwuid(int(raw)).pw_name
    spec_check(spec)
    require(os.environ.get('SUDO_USER')==operator,'SETUP_SUDO_OPERATOR_IDENTITY')
    print(json.dumps(spec,ensure_ascii=False,indent=2))
    print('仅 Binance DEMO：单笔最多 250 USDC、累计最多 750 USDC、最多 3 笔、有效期 1 小时。')
    print('沿用现有风控和停机状态。此操作登记测试授权，不证明策略盈利，不开放主网。')
    expected='DEMO '+digest(spec)
    require(input('登记此测试配置请输入 '+expected+'\n').strip()==expected,'SETUP_OPERATOR_NOT_CONFIRMED')
    return {'schema':'DEMO_OPERATOR_ENROLLMENT_V1','spec_sha256':digest(spec),'operator_id':operator,
        'approved_at_ms':installer.now_ms(),'purpose':'DEMO_PORTFOLIO_TEST_ONLY' if spec['schema']==PORTFOLIO_SCHEMA else PURPOSE}


def main(argv=None):
    import argparse
    import os
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--enroll',action='store_true',help='Local operator enrollment; never called by a research worker.')
    parser.add_argument('--run-cycle',action='store_true',help='Run the single finite cycle immediately after operator enrollment.')
    args=parser.parse_args(argv);previous=os.umask(0o077);phase='PREFLIGHT'
    stage=Path('/etc/quantos/demo-operator-setup')
    try:
        require(not args.run_cycle or args.enroll,'SETUP_ENROLLMENT_REQUIRED')
        require(not any(p.exists() or p.is_symlink() for p in
            (installer.CONFIG,installer.ISSUER,installer.CONTROLLER,installer.EXECUTOR/'kill-switch.sqlite3',
             installer.EXECUTOR/'cancellations.sqlite3')),'SETUP_ALREADY_OR_PARTIALLY_INSTALLED')
        registry,origin=_runtime_inputs(args.source_commit)
        spec=propose(origin=origin,registry=registry,source_commit=args.source_commit,now_ms=installer.now_ms())
        if not args.enroll:
            print(json.dumps({'spec':spec,'spec_sha256':digest(spec),'status':'UNSIGNED_PROPOSAL',
                'orders_submitted':False,'services_started':False},sort_keys=True));return 0
        phase='OPERATOR_ENROLLMENT';approval=operator_confirmation(spec)
        # Refresh observations after the operator takes time to read. The
        # confirmed finite rules and registry remain fixed; prices are current.
        phase='FRESH_INPUTS';current_registry,origin=_runtime_inputs(args.source_commit)
        require(current_registry==registry,'SETUP_REGISTRY_CHANGED')
        stop=_stop_image()
        require(not stage.exists() and not stage.is_symlink(),'SETUP_PREVIOUS_ENROLLMENT_RETAINED')
        stage.mkdir(mode=0o700)
        installer.write_new(stage/'spec.json',canonical(spec))
        installer.write_new(stage/'operator-enrollment.json',canonical(approval))
        phase='MATERIALS'
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        private=Ed25519PrivateKey.generate()
        manifest,files=assemble(spec=spec,approval=approval,origin=origin,registry=registry,
            private_key=private,stop_image=stop,now_ms=installer.now_ms())
        del private
        directory=stage/'bundle';directory.mkdir(mode=0o700)
        for name,body in files.items():installer.write_new(directory/name,body)
        installer.write_new(directory/'manifest.json',canonical(manifest))
        # The existing importer rechecks source, original account, materials,
        # file custody and registry before publishing the new public key last.
        phase='ORIGINAL_INSTALLER'
        code=installer.main(['--source-commit',args.source_commit,'--bundle-dir',str(directory),
            '--manifest-sha256',digest(manifest),'--apply'])
        require(code==0,'SETUP_INSTALLATION_INCOMPLETE')
        value={'schema':'DEMO_OPERATOR_SETUP_RESULT_V1','status':'INSTALLED_PENDING_CYCLE',
            'spec_sha256':digest(spec),'enrollment_sha256':digest(approval),'source_commit':args.source_commit,
            'task_sha256':manifest['tasks'][0],'orders_submitted':False,'economic_authority':False}
        installer.write_new(stage/'result.json',canonical(value))
        print(json.dumps(value,sort_keys=True),flush=True)
        if args.run_cycle:
            from tools import demo_functional_cycle
            return demo_functional_cycle.main(['--source-commit',args.source_commit])
        return 0
    except Exception:
        # Neither a broker credential, private key, raw account nor arbitrary
        # filesystem exception is ever printed as a setup failure.
        print(json.dumps({'schema':'DEMO_OPERATOR_SETUP_RESULT_V1','status':'SETUP_NOT_COMPLETED',
            'phase':phase,'orders_submitted':False,'economic_authority':False}));return 3
    finally:os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
