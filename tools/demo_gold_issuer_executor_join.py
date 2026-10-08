"""Join original issuer consumption to executor submissions in isolated copies.

The caller still has to establish the complete checkpoint set, source identity,
fixed public records and real writer barrier. This verifier proves only the
historical submission relationship, and cannot restore or authorize execution.
It opens original SQLite bytes only through the existing private snapshotter.
"""
from contextlib import ExitStack, closing, contextmanager
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from live.execution.demo_batch_risk import _digest
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.demo_cancel_store import DemoCancelStore
from live.execution.demo_issuer_store import IssuerStore, approval_id, MAX_BYTES
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from services.validation import demo_submission, demo_cancellation
from tools import demo_namespace_checkpoint as checkpoint

SCHEMA = 'GOLD_ISSUER_EXECUTOR_SUBMISSION_JOIN_V1'
GRANTS_SCHEMA = 'GOLD_ISSUER_EXECUTOR_GRANT_JOIN_V2'
IDENTITY = ('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')


def require(value, reason):
    if not value:
        raise ValueError('GOLD_JOIN_'+reason)


def _identity(info):
    return tuple(getattr(info, key) for key in IDENTITY)


def _hash(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value), 'HASH')


class _Originals:
    def __init__(self):
        self.files = {}; self.directories = {}; self.absent = set(); self.total = 0

    def directory(self, path):
        path=Path(path).absolute()
        require('..' not in path.parts, 'PATH')
        require(not any(p.is_symlink() for p in (path,*path.parents)), 'PATH')
        info=path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid()
                and not info.st_mode&0o022, 'DIRECTORY_CUSTODY')
        for protected in checkpoint.PROTECTED_ROOTS:
            require(path!=protected and protected not in path.parents, 'ISOLATED_INPUT_REQUIRED')
        before=self.directories.setdefault(path,_identity(info))
        require(before==_identity(info), 'ORIGINAL_CHANGED')
        return path

    def file(self, path, *, maximum=MAX_BYTES, return_bytes=False):
        path=Path(path).absolute();self.directory(path.parent)
        before=path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_nlink==1
                and before.st_uid==os.getuid() and not before.st_mode&0o022
                and before.st_size<=maximum, 'FILE_CUSTODY')
        digest=hashlib.sha256();size=0;parts=[]
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        with os.fdopen(fd,'rb') as stream:
            require(_identity(os.fstat(stream.fileno()))==_identity(before),'ORIGINAL_CHANGED')
            while chunk:=stream.read(1024*1024):
                size+=len(chunk);require(size<=maximum,'FILE_BUDGET');digest.update(chunk)
                if return_bytes:parts.append(chunk)
            require(_identity(os.fstat(stream.fileno()))==_identity(before),'ORIGINAL_CHANGED')
        require(size==before.st_size and _identity(path.lstat())==_identity(before),'ORIGINAL_CHANGED')
        observation=(_identity(before),digest.hexdigest())
        if path not in self.files:
            self.total+=size;require(self.total<=3*MAX_BYTES+64*1024*1024,'TOTAL_BUDGET')
        require(self.files.setdefault(path,observation)==observation,'ORIGINAL_CHANGED')
        return b''.join(parts) if return_bytes else observation

    def database(self, path, scratch):
        path=Path(path).absolute();self.directory(path.parent)
        for suffix in ('','-wal','-shm','-journal'):
            member=Path(str(path)+suffix)
            try:member.lstat()
            except FileNotFoundError:
                require(bool(suffix),'DATABASE_MISSING');self.absent.add(member)
            else:self.file(member)
        snapshot,_,_=checkpoint.snapshot_readonly_sqlite(path,scratch)
        return snapshot

    def recheck(self):
        for path,identity in tuple(self.directories.items()):
            require(_identity(path.lstat())==identity,'ORIGINAL_CHANGED')
        for path in self.absent:
            try:path.lstat()
            except FileNotFoundError:continue
            raise ValueError('GOLD_JOIN_ORIGINAL_CHANGED')
        for path in tuple(self.files):self.file(path)


class VerifiedSubmissionJoin:
    """An operation-local read handle; it is not an execution capability."""
    def __init__(self, originals, report, pairs, cancellation_pairs):
        self._originals=originals;self._report=report;self._pairs=pairs;self._closed=False
        self._cancellation_pairs=cancellation_pairs

    def recheck(self):
        require(not self._closed,'CLOSED');self._originals.recheck()

    def report(self):
        self.recheck();return deepcopy(self._report)

    def original_grant(self,reservation_sha256):
        self.recheck();_hash(reservation_sha256)
        require(reservation_sha256 in self._pairs,'RESERVATION_UNKNOWN')
        return deepcopy(self._pairs[reservation_sha256])

    def original_cancellation_grant(self,grant_sha256):
        self.recheck();_hash(grant_sha256)
        require(grant_sha256 in self._cancellation_pairs,'CANCELLATION_GRANT_UNKNOWN')
        return deepcopy(self._cancellation_pairs[grant_sha256])

    def close(self):
        self._closed=True


def _cancellation_join(store, signed, reserved, matched, submission_pairs, execution,
                       originals, evidence, account, deployment):
    rows=store.snapshot()  # Full native grants, claims and results, including unknown outcomes.
    submissions={r['record_hash']:r for r in execution['reservations']}
    pairs={}
    for grant in rows:
        if grant['kind']!='GRANT':continue
        payload=grant['payload'];envelope=payload['envelope'];context=payload['context']
        ancestor=signed.get(_digest(envelope))
        require(ancestor is not None,'CANCEL_SIGNED_ANCESTOR_REQUIRED')
        identity=ancestor['proposal_sha256'];original=reserved[identity]
        plan=original['payload']['plan'];policy=original['payload']['policy'];registry=original['payload']['registry']
        require(plan['purpose']=='cancellation' and identity not in matched,'CANCEL_SIGNED_ANCESTOR_PURPOSE')
        require(plan['context']==context and plan['commands']==payload['commands']
                and ancestor['payload']['envelope']==envelope,'CANCEL_EXACT_PLAN_BINDING')
        require(grant['identity']==approval_id(plan,policy)
                and context['account_ref_sha256']==account==policy['account_ref_sha256']
                and registry['deployment_sha256']==deployment==context['deployment_sha256'],
                'CANCEL_ACCOUNT_DEPLOYMENT_BINDING')
        require(original['at_ms']<=ancestor['at_ms']<=grant['at_ms'],'CANCEL_SIGNING_CAUSALITY')
        demo_cancellation.verify(envelope,registry=registry,expected_context=context,
            commands=plan['commands'],now_ms=grant['at_ms'])
        submission=submissions.get(context['reservation_sha256'])
        require(submission is not None and submission['record_hash'] in submission_pairs,
                'CANCEL_SUBMISSION_ANCESTOR_REQUIRED')
        prior=submission['payload'];orders={o['newClientOrderId']:o for o in prior['orders']}
        require(prior['context']['batch_sha256']==context['original_batch_sha256']
                and prior['reserved_at_ms']<=original['at_ms']
                and all(c['origClientOrderId'] in orders and
                    orders[c['origClientOrderId']]['symbol']==c['symbol'] for c in plan['commands']),
                'CANCEL_ORIGINAL_ORDER_BINDING')
        raw=originals.file(evidence/'cancellation-proposals'/(identity+'.json'),
            maximum=16*1024*1024,return_bytes=True)
        proposal=decode(raw)
        require(type(proposal) is dict and _digest(proposal)==identity
                and proposal.get('context')==context and proposal.get('commands')==plan['commands'],
                'CANCEL_ORIGINAL_PROPOSAL_BINDING')
        matched.add(identity)
        pairs[grant['record_hash']]=dict(reserved=deepcopy(original),signed=deepcopy(ancestor),
            registry=deepcopy(registry),proposal=proposal,
            submission_reservation_sha256=submission['record_hash'])
    claims=[r for r in rows if r['kind']=='CLAIM']
    results={r['payload']['claim_sha256']:r for r in rows if r['kind']=='RESULT'}
    return dict(record_count=len(rows),history_sha256=_digest(rows),grant_count=len(pairs),
        claim_count=len(claims),result_count=len(results),
        unresolved_claim_count=sum(r['record_hash'] not in results or
            results[r['record_hash']]['payload']['outcome']['status']=='UNKNOWN' for r in claims),
        bindings=[dict(grant_sha256=key,proposal_sha256=pair['reserved']['proposal_sha256'],
            reserved_record_sha256=pair['reserved']['record_hash'],signed_record_sha256=pair['signed']['record_hash'],
            submission_reservation_sha256=pair['submission_reservation_sha256']) for key,pair in pairs.items()],
        cash_reconciled=False),pairs


def _join(issuer, dispatch, originals, evidence, account, deployment, cancellation=None):
    rows=issuer.snapshot()  # Includes native claims, signatures and all budget ancestry.
    execution=dispatch.evidence_snapshot()  # Includes the real reserved-order index.
    reserved={row['proposal_sha256']:row for row in rows if row['kind']=='RESERVED'}
    signed={}
    for row in rows:
        if row['kind']=='SIGNED':
            envelope=row['payload']['envelope'];key=_digest(envelope)
            require(key not in signed,'DUPLICATE_SIGNED_ENVELOPE');signed[key]=row
    pairs={};matched=set()
    for reservation in execution['reservations']:
        payload=reservation['payload'];envelope=payload['envelope']
        ancestor=signed.get(_digest(envelope))
        require(ancestor is not None,'SIGNED_ANCESTOR_REQUIRED')
        identity=ancestor['proposal_sha256'];original=reserved[identity]
        plan=original['payload']['plan'];policy=original['payload']['policy'];registry=original['payload']['registry']
        require(plan['purpose']=='submission' and identity not in matched,'SIGNED_ANCESTOR_PURPOSE')
        require(plan['context']==payload['context'] and plan['orders']==payload['orders']
                and ancestor['payload']['envelope']==envelope,'EXACT_PLAN_BINDING')
        require(reservation['approval_id']==approval_id(plan,policy)
                and reservation['account_ref_sha256']==account==policy['account_ref_sha256']
                and payload['context']['account_ref_sha256']==account
                and registry['deployment_sha256']==deployment==payload['context']['deployment_sha256'],
                'ACCOUNT_DEPLOYMENT_BINDING')
        require(original['at_ms']<=ancestor['at_ms']<=payload['reserved_at_ms'],'SIGNING_CAUSALITY')
        demo_submission.verify(envelope,registry=registry,expected_context=plan['context'],
            orders=plan['orders'],now_ms=payload['reserved_at_ms'])
        raw=originals.file(evidence/(identity+'.json'),maximum=16*1024*1024,return_bytes=True)
        proposal=decode(raw)
        require(type(proposal) is dict and _digest(proposal)==identity
                and proposal.get('context')==plan['context'] and proposal.get('orders')==plan['orders'],
                'ORIGINAL_PROPOSAL_BINDING')
        matched.add(identity)
        pairs[reservation['record_hash']]=dict(reserved=deepcopy(original),signed=deepcopy(ancestor),
            registry=deepcopy(registry),proposal=proposal)
    cancellations=None;cancellation_pairs={}
    if cancellation is not None:
        cancellations,cancellation_pairs=_cancellation_join(cancellation,signed,reserved,matched,pairs,
            execution,originals,evidence,account,deployment)
    signed_ids={row['proposal_sha256'] for row in signed.values()}
    unmatched=[dict(proposal_sha256=identity,purpose=row['payload']['plan']['purpose'],
        status=('UNSIGNED_RESERVED' if identity not in signed_ids else
            ('SIGNED_CANCELLATION_UNCHECKED' if cancellation is None else 'SIGNED_CANCELLATION_UNDELIVERED')
            if row['payload']['plan']['purpose']=='cancellation'
            else 'SIGNED_UNDELIVERED'),
        reserved_record_sha256=row['record_hash']) for identity,row in reserved.items() if identity not in matched]
    report=dict(schema=SCHEMA,status='ISSUER_EXECUTOR_SUBMISSIONS_VERIFIED',
        account_ref_sha256=account,deployment_sha256=deployment,
        issuer_record_count=len(rows),issuer_history_sha256=_digest(rows),
        executor_reservation_count=len(execution['reservations']),executor_history_sha256=_digest(execution),
        bindings=[dict(reservation_sha256=identity,proposal_sha256=pair['reserved']['proposal_sha256'],
            reserved_record_sha256=pair['reserved']['record_hash'],signed_record_sha256=pair['signed']['record_hash'])
            for identity,pair in pairs.items()],unmatched_reservations=unmatched,
        limitations=['COMPLETE_SET_AND_FIXED_PUBLIC_BINDING_REQUIRED','REAL_WRITER_BARRIER_REQUIRED',
            'CANCELLATION_DISPATCH_JOIN_NOT_CHECKED'],whole_checkpoint_set_verified=False,
        submission_enabled=False,economic_authority=False)
    if cancellations is not None:
        report.update(schema=GRANTS_SCHEMA,status='ISSUER_EXECUTOR_GRANTS_VERIFIED',cancellations=cancellations)
        report['limitations'][-1]='CANCELLATION_ORIGINAL_QUERY_REPLAY_NOT_CHECKED'
    return report,pairs,cancellation_pairs


@contextmanager
def open_join(*, issuer_database, executor_database, evidence_directory,
              account_ref_sha256, deployment_sha256, cancellation_database=None):
    """Verify originals in isolated role trees; leave every source byte intact."""
    _hash(account_ref_sha256);_hash(deployment_sha256)
    originals=_Originals();evidence=originals.directory(evidence_directory)
    with ExitStack() as stack:
        scratch=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='gold-issuer-join-')))
        issuer_area=scratch/'issuer';issuer_area.mkdir(mode=0o700)
        executor_area=scratch/'executor';executor_area.mkdir(mode=0o700)
        issuer=stack.enter_context(closing(IssuerStore(originals.database(issuer_database,issuer_area),read_only=True)))
        executor=stack.enter_context(closing(DemoDispatchStore(originals.database(executor_database,executor_area),read_only=True)))
        cancellation=None
        if cancellation_database is not None:
            cancel_area=scratch/'cancellation';cancel_area.mkdir(mode=0o700)
            cancellation=stack.enter_context(closing(DemoCancelStore(
                originals.database(cancellation_database,cancel_area),read_only=True)))
        report,pairs,cancellation_pairs=_join(issuer,executor,originals,evidence,
            account_ref_sha256,deployment_sha256,cancellation)
        handle=VerifiedSubmissionJoin(originals,report,pairs,cancellation_pairs)
        try:
            handle.recheck();yield handle;handle.recheck()
        finally:handle.close()
