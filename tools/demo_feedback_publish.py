"""Deliver one pinned executor feedback artifact after permanent research UID drop.

A separate root-owned profile enrolls this handoff. It does not configure a
campaign, sign an execution intent, or grant authority to the feedback.
"""
import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import time

from tools import demo_protected_publish as custody
from tools.demo_issuer import verify_source
from tools.demo_operator_setup import canonical,digest
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.gold_execution_feedback import research_trigger, is_completed_episode

PROFILE=Path('/etc/quantos/demo-observation/research-feedback.json')
EXECUTOR=Path('/var/lib/quantos-demo-executor')
MAXIMUM=2_000_000
RETRYABLE_FAILURE_SCHEMA='DEMO_RESEARCH_FEEDBACK_RETRYABLE_FAILURE_V1'
RETRYABLE_FAILURE_CODE=4
REPORTED_BAR_SCHEMAS=('GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V1','GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V2')
_RETRYABLE_STORAGE_ERRNOS=frozenset((errno.ENOSPC,errno.EDQUOT,errno.EIO,errno.EROFS,errno.EAGAIN,errno.ETIMEDOUT))
FIELDS=set('schema environment scope strategy_id symbol account_ref_sha256 dispatch_evidence_sha256 cash_capture_sha256 cash_report_sha256 available_at_ms source_commit orders positions position_flat cash_reconciliation canonical_wallet wallet_residual candidate_attribution research_trigger_enabled strategy_pnl source_verified coverage_verified economic_eligible submission_enabled batches research_identity result_hash'.split())


class _DestinationStorageUnavailable(ValueError):
    """A narrowly classified storage failure, never an input or custody error."""


class FeedbackPublicationPending(ValueError):
    """Safe diagnostic emitted only after original validation and UID drop."""
    def __init__(self, receipt):
        super().__init__('RESEARCH_FEEDBACK_DELIVERY_PENDING')
        self.receipt=receipt


def pending_receipt(*,source_commit,cycle,artifact_sha256,feedback_sha256):
    return dict(schema=RETRYABLE_FAILURE_SCHEMA,status='RESEARCH_FEEDBACK_DELIVERY_PENDING',
        reason='FEEDBACK_PUBLICATION_DESTINATION_UNAVAILABLE',source_commit=source_commit,
        cycle_sha256=cycle,artifact_sha256=artifact_sha256,feedback_sha256=feedback_sha256,
        source_verified=False,submission_enabled=False,economic_authority=False)


def hash_value(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None,'FEEDBACK_PUBLICATION_HASH')


def configuration(source_commit):
    cfg=load_json_without_duplicate_keys(custody._read_root_owned_json(PROFILE))
    return validate_configuration(cfg,source_commit=source_commit)


def validate_configuration(cfg,*,source_commit):
    require(type(cfg) is dict and set(cfg)==set('schema source_commit executor_uid research_uid research_gid research_state_root'.split()),'FEEDBACK_PUBLICATION_PROFILE')
    require(cfg['schema']=='DEMO_RESEARCH_FEEDBACK_PUBLICATION_V1' and cfg['source_commit']==source_commit,'FEEDBACK_PUBLICATION_SOURCE')
    require(all(type(cfg[k]) is int and 0<cfg[k]<2**31 for k in ('executor_uid','research_uid','research_gid')) and
        cfg['executor_uid'] not in (cfg['research_uid'],cfg['research_gid']),'FEEDBACK_PUBLICATION_IDENTITIES')
    path=cfg['research_state_root']
    require(type(path) is str and 0<len(path)<=4096 and '\0' not in path and Path(path).is_absolute() and '..' not in Path(path).parts,'FEEDBACK_PUBLICATION_DESTINATION')
    return cfg


def read_export(*,cycle,artifact_sha256,uid,strategy=False,bar=False):
    hash_value(cycle);hash_value(artifact_sha256)
    require(type(strategy) is bool and type(bar) is bool and not (bar and strategy),'FEEDBACK_PUBLICATION_MODE')
    directory=EXECUTOR/('strategy-bar-feedback' if bar else ('strategy-feedback' if strategy else 'resident-recovery/functional-cycles'))/cycle/'research-feedback'
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC);current=Path('/')
    try:
        custody._require_protected(os.fstat(fd),directory=True)
        for name in directory.parts[1:]:
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child;current=current/name
            if current==EXECUTOR or EXECUTOR in current.parents:custody._owned(os.fstat(fd),uid,directory=True)
            else:custody._require_protected(os.fstat(fd),directory=True)
        raw=load_json_without_duplicate_keys(custody._read(fd,artifact_sha256+'.json',uid,MAXIMUM))
        require(digest(raw)==artifact_sha256,'FEEDBACK_PUBLICATION_ARTIFACT_CHANGED')
        return raw
    except OSError:raise ValueError('FEEDBACK_PUBLICATION_SOURCE_UNAVAILABLE') from None
    finally:os.close(fd)


def validate_export(raw,*,source_commit,feedback_sha256,now):
    hash_value(feedback_sha256)
    single = type(raw) is dict and raw.get('schema')=='DEMO_EXECUTION_RESEARCH_FEEDBACK_V1'
    fields = (FIELDS-{'batches'} | {'reservation_sha256','proposal_sha256','resolution_plan_sha256',
        'manifest_hash','journal_tail'}) if single else FIELDS
    require(type(raw) is dict and set(raw)==fields and is_completed_episode(raw) and
        raw['source_commit']==source_commit,'FEEDBACK_PUBLICATION_FIELDS')
    identity=raw['research_identity']
    require(type(identity) is dict,'FEEDBACK_PUBLICATION_IDENTITY')
    config={'input_sha256':identity.get('base_input_sha256'),'execution_feedback_sha256':feedback_sha256}
    require(research_trigger(config,raw,now=now)['ready'],'FEEDBACK_PUBLICATION_NOT_READY')
    return raw



def _validate_bar_definition(raw):
    from services.execution.backtest_authority import exact_identity_hash
    definition = raw['definition']
    require(type(definition) is dict and definition.get('schema') == 'GOLD_BAR_RESEARCH_DEFINITION_V1'
        and definition.get('symbol') == 'XAUUSDT' and definition.get('candidate') in ('channel','trend','reversion')
        and all(definition.get(k) is False for k in ('authoritative','economic_eligible','submission_enabled','selected_for_execution'))
        and raw['definition_hash'] == definition.get('definition_hash') == exact_identity_hash(
            {k:v for k,v in definition.items() if k != 'definition_hash'}, name='gold_bar_research_definition'),
        'BAR_FEEDBACK_PUBLICATION_DEFINITION')
    hash_value(raw['bar_signal_sha256'])
    require(type(raw['entry_client_order_id']) is str and re.fullmatch('QD[a-f0-9]{32}', raw['entry_client_order_id']) is not None,
        'BAR_FEEDBACK_PUBLICATION_ENTRY')


def validate_bar_export(raw, *, source_commit, feedback_sha256, now, cycle=None):
    if type(raw) is dict and raw.get('schema') in REPORTED_BAR_SCHEMAS:
        return validate_reported_bar_export(raw, source_commit=source_commit,
            feedback_sha256=feedback_sha256, now=now, cycle=cycle)
    hash_value(feedback_sha256)
    fields = set('schema environment scope definition definition_hash entry_decision_ms entry_client_order_id bar_signal_sha256 cash_feedback cash_feedback_sha256 candidate_attribution source_verified coverage_verified economic_eligible submission_enabled research_trigger_enabled strategy_pnl result_hash'.split())
    require(type(raw) is dict and set(raw) == fields
        and raw['schema'] == 'GOLD_BAR_CASH_RESEARCH_FEEDBACK_V1'
        and raw['environment'] == 'BINANCE_USDM_DEMO' and raw['scope'] == 'TECHNICAL_REVIEW_ONLY'
        and raw['candidate_attribution'] == 'BAR_DEFINITION_BOUND_TO_SIGNED_CONSTRAINTS'
        and raw['strategy_pnl'] is None
        and all(raw[k] is False for k in ('source_verified','coverage_verified','economic_eligible','submission_enabled','research_trigger_enabled'))
        and raw['result_hash'] == feedback_sha256 == digest({k:v for k,v in raw.items() if k != 'result_hash'}),
        'BAR_FEEDBACK_PUBLICATION_SCOPE')
    _validate_bar_definition(raw)
    cash = raw['cash_feedback']
    single = type(cash) is dict and cash.get('schema') == 'DEMO_EXECUTION_RESEARCH_FEEDBACK_V1'
    cash_fields = FIELDS-{'research_identity'}
    if single:
        cash_fields = cash_fields-{'batches'} | {'reservation_sha256','proposal_sha256','resolution_plan_sha256','manifest_hash','journal_tail'}
    require(type(cash) is dict and set(cash) == cash_fields and is_completed_episode(cash)
        and cash['source_commit'] == source_commit and cash['environment'] == 'BINANCE_USDM_DEMO'
        and cash['symbol'] == 'XAUUSDT' and cash['scope'] == 'TECHNICAL_REVIEW_ONLY'
        and cash['candidate_attribution'] == 'UNRESOLVED' and cash['strategy_pnl'] is None
        and all(cash[k] is False for k in ('source_verified','coverage_verified','economic_eligible','submission_enabled','research_trigger_enabled'))
        and cash['result_hash'] == raw['cash_feedback_sha256'] == digest({k:v for k,v in cash.items() if k != 'result_hash'}),
        'BAR_FEEDBACK_PUBLICATION_CASH')
    require(type(now) is int and type(raw['entry_decision_ms']) is int and type(cash['available_at_ms']) is int
        and 0 <= raw['entry_decision_ms'] <= cash['available_at_ms'] <= now, 'BAR_FEEDBACK_PUBLICATION_TIME')
    return raw


def validate_reported_bar_export(raw, *, source_commit, feedback_sha256, now, cycle=None):
    """Accept protected reported-account diagnostics without upgrading gap coverage."""
    from decimal import Decimal, InvalidOperation
    from services.research.ai_factory_store import digest as ledger_digest
    from services.research.demo_income_time import POLICY as income_time_policy
    fields = set('schema environment scope source_commit available_at_ms definition definition_hash entry_decision_ms entry_client_order_id bar_signal_sha256 inputs_sha256 dispatch_evidence_sha256 reservations account_report account_report_sha256 candidate_attribution stream_coverage_repaired source_verified coverage_verified economic_eligible submission_enabled research_trigger_enabled strategy_pnl result_hash'.split())
    v2 = type(raw) is dict and raw.get('schema') == 'GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V2'
    if v2:
        fields.update(('opening_record_sha256','bundle_sha256'))
    hash_value(feedback_sha256)
    require(type(raw) is dict and set(raw) == fields
        and raw['schema'] in REPORTED_BAR_SCHEMAS
        and raw['environment'] == 'BINANCE_USDM_DEMO' and raw['scope'] == 'TECHNICAL_REVIEW_ONLY'
        and raw['source_commit'] == source_commit
        and raw['candidate_attribution'] == 'BAR_DEFINITION_BOUND_TO_SIGNED_CONSTRAINTS'
        and raw['strategy_pnl'] is None
        and all(raw[k] is False for k in ('stream_coverage_repaired','source_verified','coverage_verified',
                                         'economic_eligible','submission_enabled','research_trigger_enabled'))
        and raw['result_hash'] == feedback_sha256 == digest({k:v for k,v in raw.items() if k != 'result_hash'}),
        'BAR_REPORTED_PUBLICATION_SCOPE')
    if v2:
        for value in (raw['opening_record_sha256'],raw['bundle_sha256'],cycle):
            hash_value(value)
        require(raw['bundle_sha256'] == cycle, 'BAR_REPORTED_PUBLICATION_RUNTIME_BINDING')
    _validate_bar_definition(raw)
    for key in ('inputs_sha256', 'dispatch_evidence_sha256', 'account_report_sha256'):
        hash_value(raw[key])
    reservations = raw['reservations']
    require(type(reservations) is list and 2 <= len(reservations) <= 32, 'BAR_REPORTED_PUBLICATION_RESERVATIONS')
    for value in reservations:
        hash_value(value)
    require(len(set(reservations)) == len(reservations), 'BAR_REPORTED_PUBLICATION_RESERVATIONS')
    report = raw['account_report']
    fields = set('schema status issues account_ref_sha256 symbol opening_sha256 closing_sha256 statement_sha256 windows_sha256 system_orders_sha256 system_order_ids manual_order_ids stream_gaps stream_coverage_repaired canonical_wallet reported_wallet wallet_residual final_quantity reported_fee reported_realized_pnl net_after_fees ledger_state ledger_state_hash fill_count source_verified economic_authority automatic_cycle_completed submission_enabled strategy_pnl income_time_policy'.split())
    if type(report) is dict and 'unfilled_system_order_ids' in report:
        fields.add('unfilled_system_order_ids')
    episode = type(report) is dict and report.get('schema') == 'DEMO_REPORTED_ACCOUNT_EPISODE_V1'
    if episode:
        fields.update(('risk_budget_reset', 'daily_valuation_status', 'episode_policy'))
        require(report.get('risk_budget_reset') is False
            and report.get('daily_valuation_status') == 'NOT_RECONSTRUCTED'
            and report.get('episode_policy') == 'FLAT_CHECKPOINTS_CONTINUOUS_LEDGER_V1',
            'BAR_REPORTED_PUBLICATION_EPISODE')
    require(type(report) is dict and set(report) == fields
        and report['schema'] == ('DEMO_REPORTED_ACCOUNT_EPISODE_V1' if episode else 'DEMO_REPORTED_ACCOUNT_RECONCILIATION_V1')
        and report['status'] == 'REPORTED_ACCOUNT_MATCHED' and report['symbol'] == 'XAUUSDT'
        and report['income_time_policy'] == income_time_policy
        and report['issues'] == [] and report['manual_order_ids'] == []
        and report['wallet_residual'] == '0' and report['final_quantity'] == '0'
        and report['strategy_pnl'] is None and type(report['stream_gaps']) is list
        and all(report[k] is False for k in ('stream_coverage_repaired','source_verified','economic_authority',
                                             'automatic_cycle_completed','submission_enabled'))
        and raw['account_report_sha256'] == digest(report), 'BAR_REPORTED_PUBLICATION_REPORT')
    for key in ('account_ref_sha256','opening_sha256','closing_sha256','windows_sha256',
                'system_orders_sha256','ledger_state_hash'):
        hash_value(report[key])
    require(type(report['statement_sha256']) is list and len(report['statement_sha256']) == 2,
        'BAR_REPORTED_PUBLICATION_STATEMENTS')
    for value in report['statement_sha256']:
        hash_value(value)
    ids = report['system_order_ids']
    unfilled = report.get('unfilled_system_order_ids', [])
    require(type(unfilled) is list and all(type(v) is str for v in unfilled)
        and len(set(unfilled)) == len(unfilled) and type(ids) is list
        and all(v in ids for v in unfilled), 'BAR_REPORTED_PUBLICATION_UNFILLED')
    require(type(ids) is list and len(ids) == len(reservations)
        and all(type(v) is str and re.fullmatch('[0-9]+', v) for v in ids)
        and len(set(ids)) == len(ids) and type(report['fill_count']) is int
        and 1 <= len(ids)-len(unfilled) <= report['fill_count'] <= 1000, 'BAR_REPORTED_PUBLICATION_ORDERS')
    ledger = report['ledger_state']
    require(type(ledger) is dict and ledger_digest(ledger) == report['ledger_state_hash']
        and ledger.get('wallet_balance') == report['canonical_wallet'], 'BAR_REPORTED_PUBLICATION_LEDGER')
    try:
        wallet = Decimal(report['canonical_wallet'])
        reported = Decimal(report['reported_wallet'])
        valid = wallet.is_finite() and reported.is_finite() and wallet == reported
    except (InvalidOperation, TypeError, ValueError):
        valid = False
    require(valid, 'BAR_REPORTED_PUBLICATION_WALLET')
    require(all(type(v) is int for v in (now, raw['entry_decision_ms'], raw['available_at_ms']))
        and 0 <= raw['entry_decision_ms'] <= raw['available_at_ms'] <= now,
        'BAR_REPORTED_PUBLICATION_TIME')
    return raw


def _publish_noreplace(directory,temporary,leaf):
    """Make one single-link file visible atomically inside the held directory."""
    libc=ctypes.CDLL(None,use_errno=True)
    rename=getattr(libc,'renameat2',None)
    require(rename is not None,'FEEDBACK_PUBLICATION_ATOMIC_RENAME_REQUIRED')
    rename.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint)
    rename.restype=ctypes.c_int
    if rename(directory,os.fsencode(temporary),directory,os.fsencode(leaf),1)==0:return
    code=ctypes.get_errno()
    if code==errno.EEXIST:raise FileExistsError(code,os.strerror(code),leaf)
    raise OSError(code,os.strerror(code),leaf)


def publish(root,raw,*,bar=False):
    """Research-UID-only immutable insertion, without following directory links."""
    hash_value(raw['result_hash']);root=Path(root)
    require(root.is_absolute() and '..' not in root.parts,'FEEDBACK_PUBLICATION_DESTINATION')
    require(type(bar) is bool,'FEEDBACK_PUBLICATION_MODE')
    destination=root/'data/cache/ai_factory'/('bar-execution-feedback' if bar else 'execution-feedback')
    body=canonical(raw)+b'\n';require(len(body)<=MAXIMUM,'FEEDBACK_PUBLICATION_SIZE')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC);current=Path('/');temporary=None
    try:
        for name in destination.parts[1:]:
            current=current/name
            if current==root or root in current.parents:
                try:os.mkdir(name,0o700,dir_fd=fd)
                except FileExistsError:pass
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child
            if current==root or root in current.parents:custody._owned(os.fstat(fd),os.geteuid(),directory=True)
        leaf=raw['result_hash']+'.json'
        staged_name='.feedback-'+os.urandom(16).hex()+'.tmp'
        out=os.open(staged_name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
        temporary=staged_name
        with os.fdopen(out,'wb') as handle:handle.write(body);handle.flush();os.fsync(handle.fileno())
        try:
            _publish_noreplace(fd,temporary,leaf)
            temporary=None
        except FileExistsError:
            require(custody._read(fd,leaf,os.geteuid(),MAXIMUM)==body,'FEEDBACK_PUBLICATION_DESTINATION_CONFLICT')
            os.unlink(temporary,dir_fd=fd);temporary=None
        os.fsync(fd)
    except OSError as error:
        failure=_DestinationStorageUnavailable if error.errno in _RETRYABLE_STORAGE_ERRNOS else ValueError
        raise failure('FEEDBACK_PUBLICATION_DESTINATION_UNAVAILABLE') from None
    finally:
        if temporary is not None:
            try:os.unlink(temporary,dir_fd=fd)
            except FileNotFoundError:pass
        os.close(fd)


def run(*,source_commit,cycle,artifact_sha256,feedback_sha256,strategy=False,bar=False):
    verify_source(source_commit);require(os.geteuid()==0,'FEEDBACK_PUBLICATION_ROOT_REQUIRED')
    cfg=configuration(source_commit)
    raw=read_export(cycle=cycle,artifact_sha256=artifact_sha256,uid=cfg['executor_uid'],strategy=strategy,bar=bar)
    raw=(validate_bar_export if bar else validate_export)(raw,source_commit=source_commit,
        feedback_sha256=feedback_sha256,now=time.time_ns()//1_000_000,**({'cycle':cycle} if bar else {}))
    custody.drop_identity(cfg['research_uid'],cfg['research_gid'])
    try:publish(Path(cfg['research_state_root']),raw, **({'bar':True} if bar else {}))
    except _DestinationStorageUnavailable:
        raise FeedbackPublicationPending(pending_receipt(source_commit=source_commit,cycle=cycle,
            artifact_sha256=artifact_sha256,feedback_sha256=feedback_sha256)) from None
    return {'status':'RESEARCH_FEEDBACK_DELIVERED','feedback_sha256':feedback_sha256,
        'cycle_sha256':cycle,'source_verified':False,'submission_enabled':False,'economic_authority':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source-commit','cycle','artifact-sha256','feedback-sha256'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--strategy',action='store_true',help='Read the fixed strategy runtime feedback namespace')
    parser.add_argument('--bar',action='store_true',help='Read the fixed bar strategy feedback namespace')
    args=parser.parse_args(argv);os.umask(0o077)
    try:result=run(**vars(args))
    except FeedbackPublicationPending as error:
        print(json.dumps(error.receipt,sort_keys=True));return RETRYABLE_FAILURE_CODE
    except Exception:
        print(json.dumps({'status':'RESEARCH_FEEDBACK_DELIVERY_FAILED','submission_enabled':False}));return 3
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
