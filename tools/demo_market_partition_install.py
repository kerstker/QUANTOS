"""Independent local installation of a retained Demo market partition.

Review is the default. A sudo/TTY operator may seal the original executor's
candidate in two protected role-specific copies. This does not enroll gold,
install authority, sign, submit, reset a store or control a service.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import stat

from live.execution.demo_authority import _read_root_owned_json, _require_protected
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from live.execution.demo_market_partition import _candidate
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.validation.demo_submission import validate_registry, registry_sha256
from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
from tools import demo_successor_setup as successor
from tools.gold_strategy_policy_install import local_operator

EXECUTOR_RECORD=Path('/etc/quantos/demo-submission/market-partition.json')
ISSUER_RECORD=Path('/etc/quantos/demo-issuer/market-partition.json')
INTENT=Path('/etc/quantos/demo-issuer/market-partition-intent.json')
ACTIVATION=Path('/etc/quantos/demo-market-partition-activation.json')
INTENT_MAXIMUM=3*65536


class InstallIncomplete(ValueError):
    def __init__(self,result):
        super().__init__('PARTITION_INSTALLATION_INCOMPLETE')
        self.result=result


def read_root(path):
    return _read_root_owned_json(path)


def read_intent():
    return installer.read_private(INTENT,maximum=INTENT_MAXIMUM)


def read_audit(path):
    return installer.read_private(path,maximum=65536)


def read_mirror(path,*,gid,public=False):
    def custody(info):
        require(info.st_gid==gid and stat.S_IMODE(info.st_mode)==(0o644 if public else 0o640),
            'PARTITION_INSTALL_READERS_CHANGED')
    return _read_root_owned_json(path,file_check=custody)


def protected_parents():
    for path in (EXECUTOR_RECORD,ISSUER_RECORD,INTENT,ACTIVATION):
        for parent in reversed(path.parents):
            _require_protected(parent.lstat(),directory=True)


def _absent():
    require(all(not p.exists() and not p.is_symlink()
        for p in (EXECUTOR_RECORD,ISSUER_RECORD,INTENT,ACTIVATION)),
        'PARTITION_ALREADY_OR_PARTIALLY_INSTALLED')


def _setup_inputs(source_commit):
    require(os.geteuid()==0, 'PARTITION_INSTALL_ROOT_REQUIRED')
    successor.verify_source(source_commit)
    registry=validate_registry(load_json_without_duplicate_keys(read_root(installer.REGISTRY)))
    require(load_json_without_duplicate_keys(read_root(supervisor.ISSUER_REGISTRY))==registry,
        'PARTITION_REGISTRY_MIRROR_CHANGED')
    stage=successor.active_setup()
    result=successor.read(stage/'result.json')
    spec=successor.setup.spec_check(successor.read(stage/'spec.json'))
    require(spec['symbol']=='BTCUSDC' and spec['source_commit']==result['source_commit']
        and successor.setup.digest(spec)==result['spec_sha256'], 'PARTITION_ORIGINAL_ENROLLMENT')
    pending=successor.SETUP/'pending.json'
    require(not pending.exists() and not pending.is_symlink(), 'PARTITION_PENDING_ENROLLMENT')
    return registry,stage,result,spec


def _unchanged_setup(registry,stage,result,spec):
    pending=successor.SETUP/'pending.json'
    require(load_json_without_duplicate_keys(read_root(installer.REGISTRY))==registry
        and load_json_without_duplicate_keys(read_root(supervisor.ISSUER_REGISTRY))==registry
        and successor.active_setup()==stage and successor.read(stage/'result.json')==result
        and successor.read(stage/'spec.json')==spec
        and not pending.exists() and not pending.is_symlink(), 'PARTITION_ORIGINAL_INPUTS_CHANGED')


def fresh_candidate(source_commit):
    """Original accepted source/setup; controller and executor hold their leases."""
    registry,stage,result,spec=_setup_inputs(source_commit)
    candidate=successor.market_partition_candidate(source_commit,registry,successor.setup.digest(result))
    _unchanged_setup(registry,stage,result,spec)
    return candidate,registry


def fresh_revalidation(source_commit,candidate,legacy_registry):
    """Ask the original private worker to replay the sealed time and check today."""
    registry,stage,result,spec=_setup_inputs(source_commit)
    require(registry==legacy_registry,'PARTITION_REVALIDATION_REGISTRY_CHANGED')
    report=successor.market_partition_revalidation(source_commit,registry,successor.setup.digest(result),candidate)
    _unchanged_setup(registry,stage,result,spec)
    return report


def _validate(candidate,registry,source_commit):
    now=supervisor.now_ms()
    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}',source_commit),
        'PARTITION_INSTALL_SOURCE')
    binding=_candidate(candidate,now)
    validate_registry(registry)
    require(binding['source_commit']==source_commit
        and binding['registry_sha256']==registry_sha256(registry)
        and registry['keys'] and all(k['valid_until_ms']<=binding['recorded_at_ms'] for k in registry['keys'])
        and binding['recorded_at_ms']//86400000==now//86400000,
        'PARTITION_INSTALL_LEGACY_BINDING')
    return binding


def _stable(candidate):
    """Keep the approved cut and cash; new observations may change their times."""
    binding=deepcopy(candidate['binding'])
    for key in ('recorded_at_ms','review_sha256'):binding.pop(key)
    review=deepcopy(candidate['review'])
    for key in ('reviewed_at_ms','legacy_origin_sha256','gold_origin_sha256'):review.pop(key)
    review['gold_observation'].pop('observed_at_ms')
    return binding,review


def _same_inputs(candidate,registry,source_commit):
    current,observed_registry=fresh_candidate(source_commit)
    _validate(current,observed_registry,source_commit)
    require(observed_registry==registry and _stable(current)==_stable(candidate),
        'PARTITION_INSTALL_INPUTS_CHANGED')
    require(current['binding']['recorded_at_ms']>=candidate['binding']['recorded_at_ms'],
        'PARTITION_INSTALL_CLOCK_REVERSED')
    return current


def _same_retained_inputs(intent,source_commit):
    from live.execution.demo_market_partition import validate_pre_activation_report
    record,candidate,registry=_intent_materials(intent,source_commit)
    report=fresh_revalidation(source_commit,candidate,registry)
    validate_pre_activation_report(report,candidate=candidate,registry=registry,
        source_commit=source_commit,as_of_ms=supervisor.now_ms())
    require(report['reviewed_at_ms']>=record['installed_at_ms'],'PARTITION_REVALIDATION_CLOCK_REVERSED')
    return report


def confirm(candidate,operator):
    require(local_operator()==operator, 'PARTITION_INSTALL_OPERATOR_CHANGED')
    print(json.dumps(candidate,ensure_ascii=False,indent=2))
    print('仅封存黄金 Demo 分区。旧账本和权益保留；黄金登记及交易批准另行办理。')
    expected='APPROVE PARTITION '+_digest(candidate)
    require(input('请输入 '+expected+'\n').strip()==expected, 'PARTITION_INSTALL_NOT_CONFIRMED')
    return dict(schema='DEMO_PARTITION_LOCAL_OPERATOR_APPROVAL_V1',
        candidate_sha256=_digest(candidate),operator_id=operator,
        approved_at_ms=supervisor.now_ms(),purpose='DEMO_MARKET_PARTITION_INSTALL_ONLY')


def _approval(value,candidate,operator):
    require(type(value) is dict and set(value)=={'schema','candidate_sha256','operator_id',
        'approved_at_ms','purpose'} and value['schema']=='DEMO_PARTITION_LOCAL_OPERATOR_APPROVAL_V1'
        and value['purpose']=='DEMO_MARKET_PARTITION_INSTALL_ONLY'
        and value['candidate_sha256']==_digest(candidate) and value['operator_id']==operator
        and type(value['approved_at_ms']) is int
        and candidate['binding']['recorded_at_ms']<=value['approved_at_ms']<=supervisor.now_ms(),
        'PARTITION_INSTALL_APPROVAL_BINDING')


def review(*,source_commit):
    protected_parents()
    with supervisor.controller_lease(maintenance=True):
        _absent()
        candidate,registry=fresh_candidate(source_commit)
        _validate(candidate,registry,source_commit)
        return dict(status='UNINSTALLED_MARKET_PARTITION_CANDIDATE',source_commit=source_commit,
            candidate=candidate,installed=False,orders_submitted=False,economic_authority=False)


def _stage(candidate):
    return supervisor.STATE/'market-partition-installations'/_digest(candidate)


def _ready(record):
    return dict(schema='DEMO_MARKET_PARTITION_INSTALLATION_V1',
        status='PARTITION_READY_FOR_ACTIVATION',source_commit=record['source_commit'],
        candidate_sha256=record['candidate_sha256'],audit_directory=str(_stage(record['candidate'])),
        installed=False,orders_submitted=False,economic_authority=False,
        record_sha256=_digest(record))


def _new_intent(candidate,registry,approval,checked,source_commit):
    record=dict(schema='DEMO_INSTALLED_MARKET_PARTITION_V1',source_commit=source_commit,
        candidate=candidate,candidate_sha256=_digest(candidate),legacy_registry=registry,
        operator_approval=approval,installed_at_ms=supervisor.now_ms(),
        revalidation_sha256=_digest(checked),installed=True,
        submission_enabled=False,economic_authority=False)
    intent=dict(schema='DEMO_MARKET_PARTITION_INSTALL_INTENT_V2',source_commit=source_commit,
        candidate_sha256=_digest(candidate),operator_approval_sha256=_digest(approval),
        record=record,revalidation=checked,ready_result=_ready(record))
    _intent_materials(intent,source_commit)
    require(len(installer.canonical(intent))<=INTENT_MAXIMUM,'PARTITION_INSTALL_INTENT_SIZE')
    return intent


def _intent_materials(intent,source_commit):
    require(type(intent) is dict and set(intent)==set('schema source_commit candidate_sha256 '
        'operator_approval_sha256 record revalidation ready_result'.split())
        and intent['schema']=='DEMO_MARKET_PARTITION_INSTALL_INTENT_V2'
        and intent['source_commit']==source_commit,'PARTITION_RECOVERY_INTENT_BINDING')
    record=intent['record']
    require(type(record) is dict and set(record)==set('schema source_commit candidate candidate_sha256 '
        'legacy_registry operator_approval installed_at_ms revalidation_sha256 installed '
        'submission_enabled economic_authority'.split())
        and record['schema']=='DEMO_INSTALLED_MARKET_PARTITION_V1'
        and record['source_commit']==source_commit and record['installed'] is True
        and record['submission_enabled'] is False and record['economic_authority'] is False,
        'PARTITION_RECOVERY_RECORD_FIELDS')
    candidate=record['candidate'];registry=validate_registry(record['legacy_registry'])
    binding=_candidate(candidate,supervisor.now_ms())
    require(binding['source_commit']==source_commit and registry['keys']
        and binding['registry_sha256']==registry_sha256(registry)
        and all(k['valid_until_ms']<=binding['recorded_at_ms'] for k in registry['keys'])
        and intent['candidate_sha256']==record['candidate_sha256']==_digest(candidate),
        'PARTITION_RECOVERY_ORIGINAL_CUT')
    approval=record['operator_approval']
    require(type(approval) is dict and type(approval.get('operator_id')) is str
        and re.fullmatch('[a-zA-Z0-9_-]{1,64}',approval['operator_id']), 'PARTITION_RECOVERY_ORIGINAL_OPERATOR')
    _approval(approval,candidate,approval['operator_id'])
    checked=intent['revalidation'];checked_binding=_candidate(checked,supervisor.now_ms())
    require(intent['operator_approval_sha256']==_digest(approval)
        and _stable(checked)==_stable(candidate)
        and type(record['installed_at_ms']) is int
        and max(checked_binding['recorded_at_ms'],approval['approved_at_ms'])<=record['installed_at_ms']<=supervisor.now_ms()
        and checked_binding['recorded_at_ms']>=binding['recorded_at_ms']
        and record['revalidation_sha256']==_digest(checked)
        and len(installer.canonical(record))<=65536 and len(installer.canonical(checked))<=65536
        and intent['ready_result']==_ready(record),'PARTITION_RECOVERY_MATERIALS_CHANGED')
    return record,candidate,registry


def _files(intent):
    record=intent['record'];stage=_stage(record['candidate']);registry=record['legacy_registry']
    values=(('candidate.json',record['candidate']),('legacy-registry.json',registry),
        ('operator-approval.json',record['operator_approval']),('revalidation.json',intent['revalidation']),
        ('result.json',intent['ready_result']))
    return [(stage/name,installer.canonical(value),None) for name,value in values]+[
        (ISSUER_RECORD,installer.canonical(record),registry['issuer_uid']),
        (EXECUTOR_RECORD,installer.canonical(record),registry['executor_uid'])]


def _activation(intent,at):
    return dict(schema='DEMO_MARKET_PARTITION_ACTIVATION_V1',source_commit=intent['source_commit'],
        installed_record_sha256=_digest(intent['record']),candidate_sha256=intent['candidate_sha256'],
        ready_result_sha256=_digest(intent['ready_result']),activated_at_ms=at,
        installed=True,submission_enabled=False,economic_authority=False)


def _inspect(intent):
    require(read_intent()==installer.canonical(intent),'PARTITION_RECOVERY_INTENT_CHANGED')
    stage=_stage(intent['record']['candidate'])
    for directory in (stage.parent,stage):
        if directory.exists() or directory.is_symlink():supervisor._root_private_directory(directory)
    missing=[]
    for path,body,gid in _files(intent):
        if path.exists() or path.is_symlink():
            actual=read_audit(path) if gid is None else read_mirror(path,gid=gid)
            require(actual==body,'PARTITION_RECOVERY_EXISTING_FILE_CHANGED')
        else:missing.append(path)
    if ACTIVATION.exists() or ACTIVATION.is_symlink():
        value=load_json_without_duplicate_keys(read_mirror(ACTIVATION,gid=0,public=True))
        at=value.get('activated_at_ms') if type(value) is dict else None
        require(not missing and type(at) is int
            and intent['record']['installed_at_ms']<=at<=supervisor.now_ms()
            and value==_activation(intent,at),'PARTITION_RECOVERY_ACTIVATION_CHANGED')
        return True
    return False


def _write_or_exact(path,body,*,gid=None):
    if not path.exists() and not path.is_symlink():
        installer.write_new(path,body,gid=0 if gid is None else gid,mode=0o600 if gid is None else 0o640)
    actual=read_audit(path) if gid is None else read_mirror(path,gid=gid)
    require(actual==body,'PARTITION_INSTALL_READBACK_CHANGED')


def _publish(intent,source_commit):
    record,candidate,registry=_intent_materials(intent,source_commit)
    protected_parents();require(not _inspect(intent),'PARTITION_ALREADY_ACTIVATED')
    report=_same_retained_inputs(intent,source_commit)
    stage=_stage(candidate)
    for directory in (stage.parent,stage):
        if not directory.exists():installer.directory(directory,0,0)
        supervisor._root_private_directory(directory)
    _retain_revalidation(stage,report)
    # The same intent drives first installation and operator recovery. Existing
    # files are verified byte for byte; no persistent record is replaced.
    files=_files(intent)
    for path,body,gid in files[:4]:_write_or_exact(path,body,gid=gid)
    protected_parents();require(read_intent()==installer.canonical(intent),'PARTITION_INSTALL_TARGET_CHANGED')
    for path,body,gid in files[5:]:_write_or_exact(path,body,gid=gid)
    report=_same_retained_inputs(intent,source_commit)
    _retain_revalidation(stage,report)
    _write_or_exact(*files[4][:2])
    require(not _inspect(intent),'PARTITION_ALREADY_ACTIVATED')
    at=supervisor.now_ms()
    require(report['reviewed_at_ms']<=at<=report['valid_until_ms'], 'PARTITION_REVALIDATION_EXPIRED_BEFORE_ACTIVATION')
    activation=_activation(intent,at)
    installer.write_new(ACTIVATION,installer.canonical(activation),mode=0o644)
    return dict(intent['ready_result'],status='PARTITION_INSTALLED_REQUIRES_NEW_GOLD_ENROLLMENT',
        installed=True,activation_sha256=_digest(activation))


def _retain_revalidation(stage,report):
    directory=stage/'pre-activation-revalidations'
    if not directory.exists():installer.directory(directory,0,0)
    supervisor._root_private_directory(directory)
    _write_or_exact(directory/(_digest(report)+'.json'),installer.canonical(report))


def _failure(intent,exc,path):
    result=dict(intent['ready_result'],status='PARTITION_INSTALLATION_INCOMPLETE',installed=None,
        reason='PARTITION_INSTALL_PERSISTENT_FAILURE',exception_type=type(exc).__name__)
    if path.parent.exists() and not path.parent.is_symlink():
        try:installer.write_new(path,installer.canonical(result))
        except Exception:pass
    return InstallIncomplete(result)


def install_approved(*,source_commit):
    operator=local_operator()
    with supervisor.controller_lease(maintenance=True):
        protected_parents();_absent()
        candidate,registry=fresh_candidate(source_commit)
        _validate(candidate,registry,source_commit)
        approval=confirm(candidate,operator);_approval(approval,candidate,operator)
        checked=_same_inputs(candidate,registry,source_commit)
        require(local_operator()==operator,'PARTITION_INSTALL_OPERATOR_CHANGED')
        _absent();protected_parents()
        area=_stage(candidate).parent
        if area.exists() or area.is_symlink():
            supervisor._root_private_directory(area)
            require(next(area.iterdir(),None) is None,'PARTITION_INSTALL_HISTORY_ALREADY_EXISTS')
        intent=_new_intent(candidate,registry,approval,checked,source_commit)
        # This first durable record contains every intended byte. A complete
        # V2 intent can be explicitly recovered; normal install never retries.
        try:
            installer.write_new(INTENT,installer.canonical(intent))
            return _publish(intent,source_commit)
        except Exception as exc:
            raise _failure(intent,exc,_stage(candidate)/'failure.json') from None


def _load_intent(source_commit):
    require(os.geteuid()==0,'PARTITION_RECOVERY_ROOT_REQUIRED')
    successor.verify_source(source_commit);protected_parents()
    intent=load_json_without_duplicate_keys(read_intent())
    _intent_materials(intent,source_commit)
    return intent


def review_recovery(*,source_commit):
    with supervisor.controller_lease(maintenance=True):
        intent=_load_intent(source_commit);complete=_inspect(intent)
        if not complete:_same_retained_inputs(intent,source_commit)
        return dict(status='PARTITION_INSTALL_ALREADY_COMPLETE' if complete else 'PARTITION_INSTALL_RECOVERY_REVIEW',
            source_commit=source_commit,intent_sha256=_digest(intent),candidate_sha256=intent['candidate_sha256'],
            installed=True if complete else None,orders_submitted=False,economic_authority=False)


def confirm_recovery(intent,operator):
    require(local_operator()==operator,'PARTITION_INSTALL_OPERATOR_CHANGED')
    print(json.dumps(intent['record']['candidate'],ensure_ascii=False,indent=2))
    expected='RECOVER PARTITION '+_digest(intent)
    require(input('仅补齐原批准分区；请输入 '+expected+'\n').strip()==expected,'PARTITION_RECOVERY_NOT_CONFIRMED')
    return dict(schema='DEMO_PARTITION_LOCAL_RECOVERY_APPROVAL_V1',intent_sha256=_digest(intent),
        candidate_sha256=intent['candidate_sha256'],operator_id=operator,approved_at_ms=supervisor.now_ms(),
        purpose='COMPLETE_ORIGINAL_PARTITION_INSTALL_ONLY')


def recover_approved(*,source_commit):
    operator=local_operator()
    with supervisor.controller_lease(maintenance=True):
        intent=_load_intent(source_commit)
        if _inspect(intent):
            return dict(intent['ready_result'],status='PARTITION_INSTALL_ALREADY_COMPLETE',installed=True)
        record,candidate,registry=_intent_materials(intent,source_commit)
        reviewed=_same_retained_inputs(intent,source_commit)
        approval=confirm_recovery(intent,operator)
        require(type(approval) is dict and set(approval)==set('schema intent_sha256 candidate_sha256 '
            'operator_id approved_at_ms purpose'.split())
            and approval['schema']=='DEMO_PARTITION_LOCAL_RECOVERY_APPROVAL_V1'
            and approval['purpose']=='COMPLETE_ORIGINAL_PARTITION_INSTALL_ONLY'
            and approval['intent_sha256']==_digest(intent) and approval['candidate_sha256']==_digest(candidate)
            and approval['operator_id']==operator and type(approval['approved_at_ms']) is int
            and max(record['installed_at_ms'],reviewed['reviewed_at_ms'])<=approval['approved_at_ms']<=supervisor.now_ms(),
            'PARTITION_RECOVERY_APPROVAL_BINDING')
        require(local_operator()==operator,'PARTITION_INSTALL_OPERATOR_CHANGED')
        _same_retained_inputs(intent,source_commit);protected_parents();require(not _inspect(intent),'PARTITION_ALREADY_ACTIVATED')
        stage=_stage(candidate);attempt=stage/'recovery-attempts'/os.urandom(16).hex()
        try:
            for directory in (stage.parent,stage,attempt.parent,attempt):
                if not directory.exists():installer.directory(directory,0,0)
                supervisor._root_private_directory(directory)
            installer.write_new(attempt/'approval.json',installer.canonical(approval))
            installer.write_new(attempt/'intent-binding.json',installer.canonical(dict(
                source_commit=source_commit,intent_sha256=_digest(intent),record_sha256=_digest(record),
                installed=None,orders_submitted=False,economic_authority=False)))
            result=_publish(intent,source_commit)
            # Activation remains the final durable operation. The original
            # ready result and this attempt's approval are already retained.
            return dict(result,recovery_audit_directory=str(attempt))
        except Exception as exc:
            raise _failure(intent,exc,attempt/'failure.json') from None


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--install-approved',action='store_true')
    action.add_argument('--review-recovery',action='store_true')
    action.add_argument('--recover-approved',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.recover_approved:result=recover_approved(source_commit=args.source_commit)
        elif args.review_recovery:result=review_recovery(source_commit=args.source_commit)
        else:result=install_approved(source_commit=args.source_commit) if args.install_approved else review(source_commit=args.source_commit)
        print(json.dumps(result,sort_keys=True));return 0
    except InstallIncomplete as exc:
        print(json.dumps(exc.result,sort_keys=True));return 3
    except Exception:
        # A rejected preflight can be a previous partial operation. Failure to
        # validate is never evidence that protected records are absent.
        print(json.dumps(dict(status='PARTITION_INSTALL_VALIDATION_FAILED',installed=None,
            orders_submitted=False,economic_authority=False)));return 3


if __name__=='__main__':
    raise SystemExit(main())
