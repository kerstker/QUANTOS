"""Finite observation storage derived from the original completed pilot.

An immutable marker binds each custody root to the same request, baseline and
runtime. This is storage capacity, never an extension of time or order budget.
Legacy active observations are not silently migrated or restarted.
"""
from copy import deepcopy
from contextlib import contextmanager
import fcntl
from itertools import islice
import json
import os
from pathlib import Path
import re

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.research.gold_bar_candidates import INTERVAL_MS
from tools.demo_resident_recovery import _check_path
from tools.gold_bar_worker_inputs import protected_tree

SCHEMA='GOLD_BAR_PILOT_STORAGE_CAPACITY_V1'
MARKER='gold-bar-pilot-capacity.json'
PARENT='strategy-bar-pilot-storage'
AUDIT='gold-bar-pilot-storage-audit'
KINDS=('history','observations','rotations')
DIRECTORIES=KINDS+tuple(name+'-allocations' for name in KINDS)
HISTORY_EPISODE_BYTES=16*1024*1024
OBSERVATION_EPISODE_BYTES=64*1024*1024
ROTATION_BYTES=2_000_000


def _read(path,uid,maximum=32768):
    # This reader checks the supplied installed UID without invoking the
    # executor-only identity API. Parent/root custody is checked by context.
    from tools.gold_pilot_install import _read_target
    return _read_target(path,uid=uid,maximum=maximum)


def _present(path):
    return path.exists() or path.is_symlink()


def context(root,bundle,*,uid,account=None,allow_pending=False):
    """Read original public pilot material; do not create or enroll anything."""
    if bundle.get('schema')!='GOLD_BAR_AUTO_EPISODE_INPUT_V1':return None
    return original_context(root,bundle,uid=uid,account=account,allow_pending=allow_pending)


def original_context(root,bundle,*,uid,account=None,allow_pending=False):
    """Original enrollment validation shared by all native bar consumers."""
    from tools.gold_strategy_budget_enrollment import runtime_check
    runtime_check(bundle,bundle['source_commit'])
    root=Path(root).absolute();require(root.resolve()==root,'BAR_PILOT_ROOT')
    request_path=root/'gold-pilot-request.json';area=root/'gold-pilot'
    if not _present(request_path):
        require(not any(_present(p) for p in (area,root/MARKER,root/PARENT,root/AUDIT)),
            'BAR_PILOT_REQUEST_MISSING')
        return None
    from live.execution import demo_gold_pilot_enrollment as enrollment
    _check_path(root,uid,directory=True)
    original={request_path:_read(request_path,uid)};request=decode(original[request_path])
    expected=request.get('account_ref_sha256') if account is None else account
    enrollment._request_check(request,source_commit=bundle['source_commit'],account=expected)
    config=None
    if _present(area):
        _check_path(area,uid,directory=True)
        for name,maximum in (('configuration.json',32768),('enrollment.json',32768)):
            path=area/name;original[path]=_read(path,uid,maximum)
        path=area/'enrollment-intent.json';intent=None
        if _present(path):
            original[path]=_read(path,uid,enrollment.INTENT_MAXIMUM);intent=decode(original[path])
            require(type(intent) is dict,'BAR_PILOT_ENROLLMENT_INTENT')
        config=enrollment.completed_configuration_records(
            configuration=decode(original[area/'configuration.json']),
            receipt=decode(original[area/'enrollment.json']),intent=intent,request=request)
    else:
        require(allow_pending and not state_present(root),
            'BAR_PILOT_ENROLLMENT_REQUIRED')
    require(all(_read(path,uid,max(32768,len(raw)))==raw for path,raw in original.items()),
        'BAR_PILOT_ORIGINAL_CHANGED')
    return dict(request=request,configuration=config)


def controller_context(base,account):
    """Fixed executor root and public controller registry; no UID switching."""
    from tools import demo_operating_bundle as installer
    root=installer.EXECUTOR
    if not any(_present(root/name) for name in ('gold-pilot-request.json','gold-pilot',MARKER,PARENT,AUDIT)):
        return None
    from tools.gold_strategy_controller import observation_configuration
    registry,policy=observation_configuration(base['source_commit'])
    require(policy['account_ref_sha256']==account,'BAR_PILOT_ACCOUNT')
    return context(root,base,uid=registry['executor_uid'],account=account,allow_pending=True)


def _capacity(original,base):
    from tools.gold_strategy_budget_enrollment import runtime_check
    runtime_check(base,base['source_commit'])
    require(base['schema']=='GOLD_BAR_AUTO_EPISODE_INPUT_V1','BAR_PILOT_RUNTIME')
    request,config=original['request'],original['configuration']
    require(config is not None,'BAR_PILOT_ENROLLMENT_REQUIRED')
    if request['schema']=='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V2':
        require(_digest(base)==request['budget_scope']['scope_enrollment']['runtime_sha256'],
            'BAR_PILOT_SIGNED_RUNTIME_CHANGED')
    duration=config['ends_at_ms']-config['started_at_ms']
    # At most ceil(window/bar) original closes, one alignment slot, and one
    # terminal observation slot. Recovery rereads its original episode and
    # consumes no fresh slot. These slots never extend the original deadline.
    episodes=(duration+INTERVAL_MS-1)//INTERVAL_MS+2
    return dict(schema=SCHEMA,request_sha256=_digest(request),configuration_sha256=_digest(config),
        source_commit=request['source_commit'],account_ref_sha256=request['account_ref_sha256'],
        base_runtime=deepcopy(base),base_runtime_sha256=_digest(base),
        started_at_ms=config['started_at_ms'],ends_at_ms=config['ends_at_ms'],bar_interval_ms=INTERVAL_MS,
        max_episodes=episodes,max_rotations=episodes-1,
        history_episode_bytes=HISTORY_EPISODE_BYTES,observation_episode_bytes=OBSERVATION_EPISODE_BYTES,
        history_total_bytes=episodes*HISTORY_EPISODE_BYTES,
        observation_total_bytes=episodes*OBSERVATION_EPISODE_BYTES,
        rotation_record_bytes=ROTATION_BYTES,rotation_total_bytes=(episodes-1)*ROTATION_BYTES,
        allocation_record_bytes=4096,allocation_max_records=3*episodes-1,
        allocation_copies=2,
        submission_enabled=False,economic_authority=False)


def _without_episode(bundle):
    return {key:value for key,value in bundle.items() if key!='episode_id'}


def _fsync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def state_present(root):
    return any(_present(Path(root)/name) for name in (MARKER,PARENT,AUDIT))


def legacy_empty(root,uid):
    for name in ('strategy-bar-rotation','strategy-bar-history-work','strategy-bar-observation-work'):
        legacy=root/name
        if _present(legacy):
            _check_path(legacy,uid,directory=True)
            require(next(legacy.iterdir(),None) is None,'BAR_PILOT_LEGACY_STORAGE_REQUIRES_REVIEW')


def _write_once(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    _fsync(path.parent)


def storage(root,kind,original,bundle,*,uid,read_only=False):
    """Return one immutable pilot namespace, or reject a partial/new identity."""
    require(kind in KINDS and type(read_only) is bool,'BAR_PILOT_STORAGE_KIND')
    root=Path(root).absolute();require(root.resolve()==root,'BAR_PILOT_ROOT')
    _check_path(root,uid,directory=True)
    marker=root/MARKER;parent=root/PARENT;audit=root/AUDIT
    if _present(marker):
        raw=_read(marker,uid);saved=decode(raw)
        require(type(saved) is dict and type(saved.get('base_runtime')) is dict,'BAR_PILOT_CAPACITY_RECORD')
        expected=_capacity(original,saved['base_runtime'])
        require(saved==expected and _without_episode(bundle)==_without_episode(saved['base_runtime']),
            'BAR_PILOT_CAPACITY_CHANGED')
        require(kind!='rotations' or bundle==saved['base_runtime'],'BAR_PILOT_BASE_RUNTIME_CHANGED')
        capacity=saved
    else:
        require(not _present(parent) and not _present(audit),'BAR_PILOT_CAPACITY_MISSING')
        legacy_empty(root,uid)
        capacity=_capacity(original,bundle)
        if read_only:return parent/capacity['request_sha256']/kind,capacity
        audit.mkdir(mode=0o700)
        for name in KINDS:(audit/(name+'-allocations')).mkdir(mode=0o700)
        parent.mkdir(mode=0o700)
        namespace=parent/capacity['request_sha256'];namespace.mkdir(mode=0o700)
        for name in DIRECTORIES:(namespace/name).mkdir(mode=0o700)
        lock=os.open(namespace/'allocation.lock',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        os.close(lock)
        raw=(json.dumps(capacity,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
        _write_once(audit/'capacity.json',raw)
        _write_once(marker,raw)
        for directory in (namespace,parent,audit,root):_fsync(directory)
    namespace=parent/capacity['request_sha256']
    for path in (parent,namespace,*(namespace/name for name in DIRECTORIES)):
        _check_path(path,uid,directory=True)
    _check_path(namespace/'allocation.lock',uid,directory=False)
    for path in (audit,*(audit/(name+'-allocations') for name in KINDS)):
        _check_path(path,uid,directory=True)
    require(_read(marker,uid)==raw,'BAR_PILOT_CAPACITY_CHANGED')
    require(_read(audit/'capacity.json',uid)==raw,'BAR_PILOT_CAPACITY_AUDIT_CHANGED')
    return namespace/kind,capacity


def usage(area,capacity,kind,uid,*,new=False):
    """Bound count and bytes before and after every capture, preserving failures."""
    require(kind in ('history','observations'),'BAR_PILOT_USAGE_KIND')
    entries=list(islice(area.iterdir(),capacity['max_episodes']+1))
    require(len(entries)+(1 if new else 0)<=capacity['max_episodes'],'BAR_PILOT_EPISODE_BUDGET')
    records=_allocations(area,capacity,kind,uid,capacity['max_episodes'])
    require({entry.name for entry in entries}=={path.stem for path in records},
        'BAR_PILOT_ALLOCATION_HISTORY_MISSING')
    per=capacity['history_episode_bytes' if kind=='history' else 'observation_episode_bytes']
    maximum=capacity['history_total_bytes' if kind=='history' else 'observation_total_bytes']
    size=0
    for entry in entries:
        _check_path(entry,uid,directory=True)
        facts=protected_tree(entry,uid);current=sum(row[2] for row in facts)
        require(current<=per,'BAR_PILOT_EPISODE_BYTES');size+=current
    require(size+(per if new else 0)<=maximum,'BAR_PILOT_STORAGE_BYTES')
    return entries


def _allocation(kind,identity,capacity):
    return dict(schema='GOLD_BAR_PILOT_STORAGE_ALLOCATION_V1',kind=kind,
        record_sha256=identity,capacity_sha256=_digest(capacity),submission_enabled=False)


def _allocations(area,capacity,kind,uid,maximum):
    directory=area.parent/(kind+'-allocations')
    _check_path(directory,uid,directory=True)
    paths=list(islice(directory.iterdir(),maximum+1))
    require(len(paths)<=maximum,'BAR_PILOT_ALLOCATION_BUDGET')
    audit=area.parents[2]/AUDIT/(kind+'-allocations')
    _check_path(audit,uid,directory=True)
    witnesses=list(islice(audit.iterdir(),maximum+1))
    require(len(witnesses)<=maximum and {p.name for p in witnesses}=={p.name for p in paths},
        'BAR_PILOT_ALLOCATION_AUDIT_MISSING')
    for path in paths:
        require(re.fullmatch(r'[a-f0-9]{64}\.json',path.name) is not None,
            'BAR_PILOT_ALLOCATION_IDENTITY')
        require(decode(_read(path,uid,4096))==_allocation(kind,path.stem,capacity),
            'BAR_PILOT_ALLOCATION_CHANGED')
        require(_read(audit/path.name,uid,4096)==_read(path,uid,4096),'BAR_PILOT_ALLOCATION_AUDIT_CHANGED')
    return paths


@contextmanager
def _allocation_lease(area,uid):
    path=area.parent/'allocation.lock';_check_path(path,uid,directory=False)
    fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        opened,current=os.fstat(fd),path.lstat()
        require((opened.st_dev,opened.st_ino)==(current.st_dev,current.st_ino),
            'BAR_PILOT_ALLOCATION_LOCK_CHANGED')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('BAR_PILOT_ALLOCATION_BUSY') from None
        _check_path(path,uid,directory=False)
        yield
    finally:os.close(fd)


def _write_allocation(area,capacity,kind,uid,identity):
    path=area.parent/(kind+'-allocations')/(identity+'.json')
    value=_allocation(kind,identity,capacity)
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
    witness=area.parents[2]/AUDIT/(kind+'-allocations')/path.name
    _write_once(witness,raw)
    _write_once(path,raw)
    require(_read(path,uid,4096)==raw,'BAR_PILOT_ALLOCATION_CHANGED')
    require(_read(witness,uid,4096)==raw,'BAR_PILOT_ALLOCATION_AUDIT_CHANGED')


def allocate_capture(area,capacity,kind,uid,bundle):
    """Reserve once before capture; absent/partial attempts cannot be skipped."""
    with _allocation_lease(area,uid):
        usage(area,capacity,kind,uid,new=True)
        _write_allocation(area,capacity,kind,uid,_digest(bundle))


def rotation_history(area,capacity,uid):
    paths=list(islice(area.iterdir(),capacity['max_rotations']+1))
    require(len(paths)<=capacity['max_rotations'],'BAR_ROTATION_STORAGE_BUDGET')
    records=_allocations(area,capacity,'rotations',uid,capacity['max_rotations'])
    require({path.name for path in paths}=={path.name for path in records},
        'BAR_PILOT_ALLOCATION_HISTORY_MISSING')


def allocate_rotation(area,capacity,uid,record):
    with _allocation_lease(area,uid):
        rotation_history(area,capacity,uid)
        _write_allocation(area,capacity,'rotations',uid,_digest(record))


def result_binding(result,capacity):
    decision=result['decision'];pilot=decision.get('pilot_control')
    require(type(pilot) is dict and pilot.get('configuration_sha256')==capacity['configuration_sha256']
        and pilot.get('submission_enabled') is False,'BAR_PILOT_RESULT_CONFIGURATION')
    observed=decision.get('observed_at_ms')
    require(type(observed) is int and capacity['started_at_ms']<=observed<capacity['ends_at_ms'],
        'BAR_PILOT_RESULT_WINDOW')
