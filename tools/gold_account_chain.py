"""Append-only research account history by deterministic full-prefix replay.

No saved balance is trusted or added to another window's P&L. Each transition
reconstructs the same initial account and checks all prior ledger/decision events.
This is bounded research replay, not a broker account or online execution loop.
"""
from decimal import Decimal
import fcntl
import hashlib
from pathlib import Path
import os
import tempfile

from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_ledger_v2 import canonical_decimal
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_bar_capture import _write
from tools.gold_session_io import read as _read, ACCOUNT_CHAIN_BYTES
from tools.gold_session_replay import replay


def _canonical(value):
    if isinstance(value,Decimal):
        return canonical_decimal(value)
    if isinstance(value,dict):
        return {k:_canonical(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):
        return [_canonical(v) for v in value]
    return value


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _history(root):
    directories=sorted(p for p in root.iterdir() if p.name.isdigit())
    previous=None
    total=0
    for index,directory in enumerate(directories,1):
        if directory.name!=f'{index:06d}' or index>96:
            raise ValueError('ACCOUNT_CHAIN_SEQUENCE')
        if (directory/'failure.json').exists():
            raise ValueError('ACCOUNT_CHAIN_PUBLICATION_INCOMPLETE')
        transition_raw=_read(directory,'transition.json')
        total+=len(transition_raw)
        row=decode(transition_raw)
        claimed=row.pop('transition_hash')
        if (row['sequence']!=index or row['previous_transition_hash']!=previous
                or row['submission_enabled'] is not False
                or exact_identity_hash(row,name='gold_account_transition')!=claimed):
            raise ValueError('ACCOUNT_CHAIN_TRANSITION_HASH')
        if set(row['files']) != {'report.json','configuration.json','rule.json','funding.json',
                                 'prefix-summary.json','inputs.json','sources.json','warmup.json','warmup-receipt.json'}:
            raise ValueError('ACCOUNT_CHAIN_FILE_SCOPE')
        for name,digest in row['files'].items():
            if name not in ('report.json','configuration.json','rule.json','funding.json',
                            'prefix-summary.json','inputs.json','sources.json','warmup.json','warmup-receipt.json'):
                raise ValueError('ACCOUNT_CHAIN_FILE_SCOPE')
            raw=_read(directory,name)
            total+=len(raw)
            if total>ACCOUNT_CHAIN_BYTES:
                raise ValueError('ACCOUNT_CHAIN_BYTE_BUDGET')
            if _hash(raw)!=digest:
                raise ValueError('ACCOUNT_CHAIN_FILE_HASH')
        previous=claimed
    return directories,previous


def advance(directory,prefix,**configuration):
    root=Path(directory).absolute()
    if root.resolve()!=root:
        raise ValueError('ACCOUNT_CHAIN_UNSAFE_PATH')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (root/'writer.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        directories,previous=_history(root)
        if len(directories)>=96:
            raise ValueError('ACCOUNT_CHAIN_TRANSITION_BUDGET')
        fixed=_canonical({k:v for k,v in configuration.items()
                          if k not in ('exchange_after','observed_funding_records')})
        config_hash=exact_identity_hash(fixed,name='gold_account_configuration')
        old=None
        if directories:
            old=directories[-1]
            if decode(_read(old,'configuration.json'))!=fixed:
                raise ValueError('ACCOUNT_CHAIN_CONFIGURATION_CHANGED')
        prefix=Path(prefix).absolute()
        summary_raw=_read(prefix,'summary.json')
        summary=decode(summary_raw)
        names=('inputs.json','sources.json','warmup.json','warmup-receipt.json')
        raw={name:_read(prefix,name) for name in names}
        if set(summary['files'])!=set(names) or any(_hash(raw[name])!=summary['files'][name] for name in names):
            raise ValueError('ACCOUNT_CHAIN_PREFIX_HASH')
        inputs=decode(raw['inputs.json'])
        if old is not None:
            prior=decode(_read(old,'inputs.json'))
            if (inputs['as_of_ms']<=prior['as_of_ms']
                    or len(inputs['samples'])<=len(prior['samples'])):
                raise ValueError('ACCOUNT_CHAIN_NOT_EXTENDED')
            if (any(inputs[k]!=prior[k] for k in ('bootstrap','quote_source','bar_source'))
                    or any(raw[k]!=_read(old,k) for k in ('warmup.json','warmup-receipt.json'))
                    or any(inputs[k][:len(prior[k])]!=prior[k] for k in ('samples','bar_messages'))):
                raise ValueError('ACCOUNT_CHAIN_INPUT_PREFIX_CHANGED')
            prior_capacity = prior.get('capacity_observations', [])
            if inputs.get('capacity_observations', [])[:len(prior_capacity)] != prior_capacity:
                raise ValueError('ACCOUNT_CHAIN_CAPACITY_PREFIX_CHANGED')
        stage=Path(tempfile.mkdtemp(prefix='attempt-',dir=root))
        try:
            for name,body in raw.items():
                (stage/name).write_bytes(body)
            (stage/'summary.json').write_bytes(summary_raw)
            _write(stage/'configuration.json',fixed)
            _write(stage/'rule.json',configuration['exchange_after'])
            _write(stage/'funding.json',configuration.get('observed_funding_records'))
            result=replay(stage,**configuration)
            if old is not None:
                prior_report=decode(_read(old,'report.json'))
                for key in ('ledger_events','equity_history','order_events','decision_events','risk_events'):
                    before=prior_report['simulation'][key]
                    if result['simulation'][key][:len(before)]!=before:
                        raise ValueError('ACCOUNT_CHAIN_LEDGER_PREFIX_CHANGED_'+key)
                if result['target_trace'][:len(prior_report['target_trace'])]!=prior_report['target_trace']:
                    raise ValueError('ACCOUNT_CHAIN_TARGET_PREFIX_CHANGED')
                before=prior_report['loss_control']
                after=result['loss_control']
                if (before['policy']!=after['policy'] or before['trigger'] is not None
                        and (before['trigger']!=after['trigger'] or after['exit_attempts']<before['exit_attempts'])):
                    raise ValueError('ACCOUNT_CHAIN_LOSS_STOP_RESET')
            _write(stage/'report.json',result)
            (stage/'summary.json').rename(stage/'prefix-summary.json')
            files=(*names,'prefix-summary.json','configuration.json','rule.json','funding.json','report.json')
            transition=dict(schema='GOLD_ACCOUNT_REPLAY_TRANSITION_V1',sequence=len(directories)+1,
                previous_transition_hash=previous,configuration_hash=config_hash,
                as_of_ms=inputs['as_of_ms'],result_hash=result['result_hash'],
                reconstruction='FULL_PREFIX_ORIGINAL_LEDGER_NOT_WINDOW_PNL_SUM',
                files={name:_hash(_read(stage,name)) for name in files},
                economic_eligible=False,submission_enabled=False)
            transition['transition_hash']=exact_identity_hash(transition,name='gold_account_transition')
            _write(stage/'transition.json',transition)
            published_bytes=sum((folder/name).stat().st_size
                for folder in (*directories,stage) for name in (*files,'transition.json'))
            if published_bytes>ACCOUNT_CHAIN_BYTES:
                raise ValueError('ACCOUNT_CHAIN_BYTE_BUDGET')
            for path in stage.iterdir():
                with path.open('rb') as handle:
                    os.fsync(handle.fileno())
            descriptor=os.open(stage,os.O_RDONLY|os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.rename(stage,root/f'{len(directories)+1:06d}')
            descriptor=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return transition
        except BaseException as exc:
            failure_root=stage if stage.exists() else root/f'{len(directories)+1:06d}'
            _write(failure_root/'failure.json',dict(status='INCOMPLETE',error_type=type(exc).__name__,
                reason=str(exc),previous_transition_hash=previous,submission_enabled=False))
            raise
