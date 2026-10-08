"""Restartable scoped service inhibition/pause/restore, not archive authorization.

No operational CLI. Does not exclude manual/old controller processes, mutate
account data, or establish global quiescence. Real mutations require root.
"""

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_maintenance_inhibit as inhibit
from tools import demo_maintenance_services as services
from tools import demo_maintenance_processes as processes
from tools import demo_retention_worker_handoff as handoff
from tools import demo_maintenance_preparation as preparation
from tools.demo_retention_transaction import directory, read, require, sync, write_new

PHASES = {"PREPARED", "INHIBITING", "PAUSING", "PAUSED", "RELEASING", "RESTORING", "RESTORED"}
LEGACY_SCHEMA='DEMO_INHIBITED_SERVICE_WINDOW_V1'
GOLD_SCHEMA='GOLD_INHIBITED_SERVICE_WINDOW_V1'
GOLD_SCOPE_SCHEMA='GOLD_INHIBITED_SERVICE_SCOPE_WINDOW_V1'
GOLD_SCHEMAS=(GOLD_SCHEMA, GOLD_SCOPE_SCHEMA)


def backend(token=None, *, require_inhibition=True):
    return services.SystemdBackend(inhibitor_operation=token, require_inhibition=require_inhibition)


def gold_backend(token=None, *, require_inhibition=True):
    return services.GoldSystemdBackend(inhibitor_operation=token, require_inhibition=require_inhibition)


def _backend(document, token=None, *, require_inhibition=True):
    factory=gold_backend if document['schema'] in GOLD_SCHEMAS else backend
    return factory(token, require_inhibition=require_inhibition)


def _legacy(document):
    require(document['schema']==LEGACY_SCHEMA,'LEGACY_WINDOW_REQUIRED')


def boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
    require(re.fullmatch("[a-f0-9]{32}", value), "WINDOW_BOOT_ID")
    return value


def reload_manager():
    require(os.geteuid() == 0, "OPERATOR_ROOT_REQUIRED")
    subprocess.run(
        ["/usr/bin/systemctl", "daemon-reload"],
        check=True,
        timeout=90,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def prepare(root, operation_sha256):
    inhibit._payload(operation_sha256)
    return _prepare(root,dict(schema=LEGACY_SCHEMA,phase='PREPARED',
        operation_sha256=operation_sha256,boot_id=boot_id()),backend())


def prepare_gold(root, *, verified_layout, verified_public):
    from tools import demo_gold_research_layout as roster
    from tools import demo_gold_namespace_checkpoint as native

    require(os.getresuid()==os.getresgid()==(0,0,0),'OPERATOR_ROOT_REQUIRED')
    captured=roster.require_captured(verified_layout,verified_public=verified_public)
    operation=captured._cut['operation_sha256']
    inhibit._payload(operation)
    document=dict(schema=GOLD_SCHEMA,phase='PREPARED',operation_sha256=operation,boot_id=boot_id(),
        source_commit=captured._public['source_commit'],fixed_layout_sha256=native._digest(captured.snapshot()))
    return _prepare(root,document,gold_backend())


def prepare_gold_scope(root, *, verified_scope, verified_public):
    from tools import demo_gold_research_layout as roster
    from tools import demo_gold_namespace_checkpoint as native
    scope = roster.require_scope(verified_scope, verified_public=verified_public)
    document = dict(schema=GOLD_SCOPE_SCHEMA, phase='PREPARED',
        operation_sha256=scope._cut['operation_sha256'], boot_id=boot_id(),
        source_commit=scope._public['source_commit'],
        fixed_scope_sha256=native._digest(scope.snapshot()), fixed_layout_sha256=None)
    return _prepare(root, document, gold_backend())


def _prepare(root, document, scoped):
    root = Path(root)
    directory(root.parent)
    root.mkdir(mode=0o700)
    sync(root.parent)
    services.prepare(root / "services.json", scoped)
    document["inhibitor_sha256"] = _inhibitor_identity(root, document)
    write_new(root / "window.lock", b"")
    write_new(root / "window.json", json.dumps(document, sort_keys=True).encode())
    return _result(document)


def _inhibitor_identity(root, document):
    info = directory(root)
    body = [info.st_dev, info.st_ino, document["operation_sha256"], document["boot_id"]]
    if document['schema']==GOLD_SCHEMA:
        body.extend([GOLD_SCHEMA,document['source_commit'],document['fixed_layout_sha256']])
    elif document['schema']==GOLD_SCOPE_SCHEMA:
        body.extend([GOLD_SCOPE_SCHEMA, document['source_commit'], document['fixed_scope_sha256']])
    return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()


def _result(document):
    return {"status": document["phase"], "quiescence_proven": False, "production_data_archived": False}


@contextmanager
def _locked(root):
    root = Path(root)
    directory(root)
    require(read(root / "window.lock", 1) == b"", "WINDOW_LOCK")
    with os.fdopen(os.open(root / "window.lock", os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        document = decode(read(root / "window.json", 4096))
        fields={'schema','phase','operation_sha256','boot_id','inhibitor_sha256'}
        if type(document) is dict and document.get('schema') in GOLD_SCHEMAS:
            fields.update({'source_commit','fixed_layout_sha256'})
            if document['schema'] == GOLD_SCOPE_SCHEMA:
                fields.add('fixed_scope_sha256')
        require(
            type(document) is dict
            and set(document) == fields
            and document["schema"] in (LEGACY_SCHEMA, *GOLD_SCHEMAS)
            and document["phase"] in PHASES,
            "WINDOW_JOURNAL",
        )
        inhibit._payload(document["operation_sha256"])
        if document['schema'] in GOLD_SCHEMAS:
            layout = document['fixed_layout_sha256']
            require(type(document['source_commit']) is str and re.fullmatch('[a-f0-9]{40}',document['source_commit'])
                and ((document['schema'] == GOLD_SCOPE_SCHEMA and layout is None)
                    or type(layout) is str and re.fullmatch('[a-f0-9]{64}', layout)),
                'WINDOW_GOLD_BINDING')
            if document['schema'] == GOLD_SCOPE_SCHEMA:
                require(type(document['fixed_scope_sha256']) is str
                    and re.fullmatch('[a-f0-9]{64}', document['fixed_scope_sha256']), 'WINDOW_GOLD_BINDING')
        require(document["boot_id"] == boot_id(), "WINDOW_BOOT_CHANGED")
        require(document["inhibitor_sha256"] == _inhibitor_identity(root, document), "WINDOW_IDENTITY_CHANGED")
        yield root, document


def _phase(root, document, phase):
    document["phase"] = phase
    services._save(root / "window.json", document)


def pause(root):
    with _locked(root) as (root, document):
        require(document["phase"] in {"PREPARED", "INHIBITING", "PAUSING", "PAUSED"}, "WINDOW_PAUSE_PHASE")
        token = document["inhibitor_sha256"]
        if document["phase"] in {"PREPARED", "INHIBITING"}:
            # Refuse external configuration changes before installing/reloading.
            original = services.inspect_journal(root / "services.json")
            services._observations(original, _backend(document,token, require_inhibition=False))
            _phase(root, document, "INHIBITING")
            if set(original['units'])==set(services.UNITS):
                inhibit.install(token)
            else:
                inhibit.install(token,units=tuple(original['units']))
            reload_manager()
            _phase(root, document, "PAUSING")
        services.pause(root / "services.json", _backend(document,token))
        _phase(root, document, "PAUSED")
        return _result(document)


def restore(root):
    with _locked(root) as (root, document):
        _gold_capture_restore_guard(root,document)
        from tools.demo_feedback_upgrade_transaction import restore_guard
        restore_guard(root)
        if document["phase"] == "RESTORED":
            return _result(document)
        preparation.restore_guard(root, document)
        previous = _worker_record(root, document)
        if previous is not None:
            result = previous["result"]
            require(
                handoff.drained(previous["unit"])
                and (
                    (
                        type(result) is dict
                        and result.get("status") == "WORKER_COMPLETED"
                        and result.get("worker_drained") is True
                    )
                    or _prepare_aborted(root, document, previous)
                ),
                "WINDOW_WORKER_RECOVERY_REQUIRED",
            )
        if document["phase"] == "PREPARED":
            services.restore(root / "services.json", _backend(document))
            _phase(root, document, "RESTORED")
            return _result(document)
        if document["phase"] != "RESTORING":
            original = services.inspect_journal(root / "services.json")
            services._observations(original, _backend(document,document["inhibitor_sha256"], require_inhibition=False))
            _phase(root, document, "RELEASING")
            if set(original['units'])==set(services.UNITS):
                inhibit.remove(document['inhibitor_sha256'])
            else:
                inhibit.remove(document['inhibitor_sha256'],units=tuple(original['units']))
            reload_manager()
            # Check the exact original loaded configuration before any starts.
            services._observations(original, _backend(document))
            _phase(root, document, "RESTORING")
        services.restore(root / "services.json", _backend(document))
        _phase(root, document, "RESTORED")
        return _result(document)


def check_processes(root, *, executor_uid, issuer_uid):
    """Fresh scoped observation while the service window remains locked/paused.

    Not an authorization token: the report cannot rule out later manual work,
    namespace aliases or programs temporarily without a visible state handle.
    """
    with _locked(root) as (root, document):
        _legacy(document)
        require(document["phase"] == "PAUSED", "WINDOW_NOT_PAUSED")
        scoped = backend(document["inhibitor_sha256"])
        services.pause(root / "services.json", scoped)
        result = processes.observe(executor_uid=executor_uid, issuer_uid=issuer_uid)
        services.pause(root / "services.json", scoped)
        result["window_sha256"] = document["inhibitor_sha256"]
        services._save(root / "process-check.json", result)
        return result


def check_gold_processes(root, *, verified_layout, verified_public):
    """Fresh complete-roster observation, bound to this paused Gold window."""
    from tools import demo_gold_research_layout as roster
    from tools import demo_gold_namespace_checkpoint as native

    with _locked(root) as (root,document):
        require(document['schema'] in GOLD_SCHEMAS and document['phase']=='PAUSED','WINDOW_GOLD_PAUSED_REQUIRED')
        captured=roster.require_captured(verified_layout,verified_public=verified_public)
        require(captured._cut['operation_sha256']==document['operation_sha256']
            and captured._public['source_commit']==document['source_commit']
            and native._digest(captured.snapshot())==document['fixed_layout_sha256'],'WINDOW_GOLD_CONTEXT')
        scoped=_backend(document,document['inhibitor_sha256'])
        services.pause(root/'services.json',scoped)
        result=processes.observe_gold(verified_layout=captured,verified_public=verified_public)
        services.pause(root/'services.json',scoped)
        result['window_sha256']=document['inhibitor_sha256']
        services._save(root/'gold-process-check.json',result)
        return result


def check_gold_scope_processes(root, *, verified_scope, verified_public):
    from tools import demo_gold_research_layout as roster
    from tools import demo_gold_namespace_checkpoint as native
    with _locked(root) as (root, document):
        scope = roster.require_scope(verified_scope, verified_public=verified_public)
        require(document['schema'] == GOLD_SCOPE_SCHEMA and document['phase'] == 'PAUSED'
            and document['fixed_scope_sha256'] == native._digest(scope.snapshot())
            and document['source_commit'] == scope._public['source_commit']
            and document['operation_sha256'] == scope._cut['operation_sha256'], 'WINDOW_GOLD_SCOPE_CONTEXT')
        scoped = _backend(document, document['inhibitor_sha256'])
        services.pause(root / 'services.json', scoped)
        result = processes.observe_gold_scope(verified_scope=scope, verified_public=verified_public)
        services.pause(root / 'services.json', scoped)
        result['window_sha256'] = document['inhibitor_sha256']
        services._save(root / 'gold-scope-process-check.json', result)
        return result


def seal_gold_scope_layout(root, *, verified_scope, verified_public):
    """Capture stopped data only after the full original writer roster drains."""
    from tools import demo_gold_research_layout as roster
    from tools import demo_gold_namespace_checkpoint as native
    from tools import demo_gold_checkpoint_public as fixed
    observed = check_gold_scope_processes(root, verified_scope=verified_scope, verified_public=verified_public)
    require(observed['status'] == 'NO_SCOPED_PROCESS_OBSERVED', 'WINDOW_GOLD_SCOPE_PROCESSES_UNKNOWN')
    scope = roster.require_scope(verified_scope, verified_public=verified_public)
    with _locked(root) as (_, document):
        require(document['schema'] == GOLD_SCOPE_SCHEMA and document['phase'] == 'PAUSED'
            and document['fixed_layout_sha256'] is None, 'WINDOW_GOLD_SCOPE_ALREADY_SEALED')
        window_sha = document['inhibitor_sha256']
    snapshot = fixed.capture(source_commit=scope._public['source_commit'],
        operation_sha256=scope._cut['operation_sha256'],
        account_ref_sha256=scope._public['account_ref_sha256'], guard=scope.recheck)
    public, cut = deepcopy(scope._public), deepcopy(scope._cut)
    public['records']['fixed_public'] = snapshot
    cut.update(public_bundle_sha256=native._digest(public),
        opening_at_ms=snapshot['captured_at_ms'], writer_barrier_sha256=window_sha)
    stopped_public = fixed.bind_captured(snapshot, public_bundle=public, cut_request=cut, guard=scope.recheck)
    captured = roster.capture(verified_public=stopped_public, public_bundle=public,
        cut_request=cut, guard=scope.recheck)
    observed = check_gold_scope_processes(root, verified_scope=scope, verified_public=verified_public)
    require(observed['status'] == 'NO_SCOPED_PROCESS_OBSERVED', 'WINDOW_GOLD_SCOPE_PROCESSES_UNKNOWN')
    captured.recheck()
    with _locked(root) as (locked, document):
        require(document['schema'] == GOLD_SCOPE_SCHEMA and document['phase'] == 'PAUSED'
            and document['fixed_scope_sha256'] == native._digest(scope.snapshot())
            and document['fixed_layout_sha256'] is None, 'WINDOW_GOLD_SCOPE_ALREADY_SEALED')
        document['fixed_layout_sha256'] = native._digest(captured.snapshot())
        services._save(locked / 'window.json', document)
    return captured, stopped_public


def capture_gold_research(root, *, verified_layout, verified_public, source_attachments):
    """Pause the fixed writers, check all UIDs, retain leases through native capture.

    Actual service operations are a separate root operator action. Development
    tests use isolated backends; this does not prove global/manual exclusion.
    """
    from tools.demo_maintenance_operator import exclusive
    from tools import demo_gold_file_leases as file_leases
    from tools import demo_gold_research_launcher as launcher

    with exclusive():
        prepare_gold(root,verified_layout=verified_layout,verified_public=verified_public)
        dispatched=False
        try:
            pause(root)
            before=check_gold_processes(root,verified_layout=verified_layout,verified_public=verified_public)
            require(before['status']=='NO_SCOPED_PROCESS_OBSERVED','WINDOW_GOLD_PROCESSES_UNKNOWN')
            with file_leases.hold(verified_layout=verified_layout,verified_public=verified_public) as held:
                _mark_gold_capture(root,before,'RUNNING',False)
                dispatched=True
                result=launcher.capture_all(verified_layout=verified_layout,verified_public=verified_public,
                    source_attachments=source_attachments,file_leases=held)
                after=check_gold_processes(root,verified_layout=verified_layout,verified_public=verified_public)
                require(after['status']=='NO_SCOPED_PROCESS_OBSERVED','WINDOW_GOLD_PROCESSES_UNKNOWN')
                held.recheck()
                require(result['worker_exit_verified'] is result['original_file_leases_verified'] is True,
                    'WINDOW_GOLD_CAPTURE_UNVERIFIED')
            # Final lease cleanup is part of capture completion. A new scoped
            # process after release must still block restoration and success.
            after=check_gold_processes(root,verified_layout=verified_layout,verified_public=verified_public)
            require(after['status']=='NO_SCOPED_PROCESS_OBSERVED','WINDOW_GOLD_CAPTURE_RECOVERY_REQUIRED')
            _mark_gold_capture(root,after,'COMPLETE',True)
        except BaseException:
            if dispatched:
                with _locked(root) as (locked,document):
                    pending=_gold_capture_record(locked,document)
                if pending['phase']=='RUNNING':
                    observed=check_gold_processes(root,verified_layout=verified_layout,verified_public=verified_public)
                    require(observed['status']=='NO_SCOPED_PROCESS_OBSERVED','WINDOW_GOLD_CAPTURE_RECOVERY_REQUIRED')
                    _mark_gold_capture(root,observed,'FAILED_CLEANED',False)
            raise
        finally:
            restore(root)
        return dict(result,maintenance_window_restored=True,maintenance_window_sha256=after['window_sha256'],
            process_scope_sha256=after['scope_sha256'])


def _gold_capture_record(root, document):
    path=root/'gold-research-capture.json'
    if not path.exists() and not path.is_symlink():
        return None
    record=decode(read(path,4096))
    require(type(record) is dict and set(record)=={'schema','window_sha256','source_commit',
        'fixed_layout_sha256','process_scope_sha256','phase','worker_exit_verified'}
        and record['schema']=='GOLD_MAINTENANCE_RESEARCH_CAPTURE_V1'
        and record['window_sha256']==document['inhibitor_sha256']
        and record['source_commit']==document['source_commit']
        and record['fixed_layout_sha256']==document['fixed_layout_sha256']
        and type(record['process_scope_sha256']) is str and re.fullmatch('[a-f0-9]{64}',record['process_scope_sha256'])
        and record['phase'] in {'RUNNING','COMPLETE','FAILED_CLEANED'}
        and record['worker_exit_verified'] is (record['phase']=='COMPLETE'),'WINDOW_GOLD_CAPTURE_RECORD')
    return record


def _gold_capture_restore_guard(root, document):
    if document['schema'] in GOLD_SCHEMAS:
        record=_gold_capture_record(root,document)
        require(record is None or record['phase']!='RUNNING','WINDOW_GOLD_CAPTURE_RECOVERY_REQUIRED')
        from tools.demo_gold_all_role_maintenance import restore_guard
        restore_guard(root,document)


def _mark_gold_capture(root, observation, phase, worker_exit):
    with _locked(root) as (root,document):
        require(document['schema']==GOLD_SCHEMA and document['phase']=='PAUSED','WINDOW_GOLD_PAUSED_REQUIRED')
        require(observation['schema']=='GOLD_PROCESS_METADATA_OBSERVATION_V1'
            and observation['status']=='NO_SCOPED_PROCESS_OBSERVED'
            and observation['window_sha256']==document['inhibitor_sha256']
            and observation['source_commit']==document['source_commit']
            and observation['fixed_layout_sha256']==document['fixed_layout_sha256'],'WINDOW_GOLD_CAPTURE_CONTEXT')
        record=dict(schema='GOLD_MAINTENANCE_RESEARCH_CAPTURE_V1',window_sha256=document['inhibitor_sha256'],
            source_commit=document['source_commit'],fixed_layout_sha256=document['fixed_layout_sha256'],
            process_scope_sha256=observation['scope_sha256'],phase=phase,worker_exit_verified=worker_exit)
        previous=_gold_capture_record(root,document)
        require(phase in {'RUNNING','COMPLETE','FAILED_CLEANED'} and worker_exit is (phase=='COMPLETE'),
            'WINDOW_GOLD_CAPTURE_PHASE')
        if previous is None:
            require(phase=='RUNNING','WINDOW_GOLD_CAPTURE_PHASE')
            write_new(root/'gold-research-capture.json',json.dumps(record,sort_keys=True).encode())
        else:
            require({key:value for key,value in previous.items() if key not in ('phase','worker_exit_verified')}
                =={key:value for key,value in record.items() if key not in ('phase','worker_exit_verified')}
                and previous['phase']=='RUNNING' and phase in {'COMPLETE','FAILED_CLEANED'},'WINDOW_GOLD_CAPTURE_PHASE')
            services._save(root/'gold-research-capture.json',record)
        _gold_capture_record(root,document)


def recover_gold_research(root, *, verified_layout, verified_public):
    """Clear only a proven drained interrupted capture; never report archive success."""
    from tools.demo_maintenance_operator import exclusive

    with exclusive():
        with _locked(root) as (locked,document):
            require(document['schema']==GOLD_SCHEMA,'WINDOW_GOLD_PAUSED_REQUIRED')
            record=_gold_capture_record(locked,document)
            require(record is not None and record['phase']=='RUNNING','WINDOW_GOLD_CAPTURE_PHASE')
        pause(root)
        observed=check_gold_processes(root,verified_layout=verified_layout,verified_public=verified_public)
        require(observed['status']=='NO_SCOPED_PROCESS_OBSERVED','WINDOW_GOLD_CAPTURE_RECOVERY_REQUIRED')
        _mark_gold_capture(root,observed,'FAILED_CLEANED',False)
        return dict(status='GOLD_CAPTURE_ABORTED_CLEANED',worker_exit_verified=False,
            production_data_archived=False,writer_barrier_verified=False,whole_set_verified=False,
            submission_enabled=False,economic_authority=False)


def prepare_evidence(root, *, source_commit):
    """Run fresh read-only evidence preparation while retaining the window lock."""
    with _locked(root) as (root, document):
        _legacy(document)
        require(document["phase"] == "PAUSED", "WINDOW_NOT_PAUSED")
        scoped = backend(document["inhibitor_sha256"])
        services.pause(root / "services.json", scoped)
        result = preparation.run(root, document, source_commit=source_commit)
        services.pause(root / "services.json", scoped)
        return result


def stage_evidence(root):
    from tools import demo_maintenance_staging as staging

    with _locked(root) as (root, document):
        _legacy(document)
        require(document["phase"] == "PAUSED", "WINDOW_NOT_PAUSED")
        scoped = backend(document["inhibitor_sha256"])
        services.pause(root / "services.json", scoped)
        result = staging.stage(root, document)
        services.pause(root / "services.json", scoped)
        return result


def _action(root, document, action, approved_sha256):
    from tools import demo_maintenance_grants as issuer

    _legacy(document)
    services.pause(root / "services.json", backend(document["inhibitor_sha256"]))
    with issuer.issue(root, document, action=action, approved_sha256=approved_sha256) as fd:
        return _run_worker(
            root, document, fd, action=action, unit="quantos-demo-retention-" + secrets.token_hex(16) + ".service"
        )


def _recover_archive(root, document, approved_sha256):
    from tools import demo_maintenance_staging as staging

    bound = staging.bound_receipt(root, document)
    require(approved_sha256 == bound["approval_sha256"], "MAINTENANCE_APPROVAL_BINDING")
    previous = _worker_record(root, document)
    require(previous is not None and previous["action"] in {"APPLY", "ROLLBACK"}, "MAINTENANCE_ROLLBACK_REQUIRED")
    if not handoff.drained(previous["unit"]):
        handoff.stop_owned(previous["unit"], previous["grant_sha256"])
        require(handoff.drained(previous["unit"]), "WINDOW_WORKER_STILL_RUNNING")
    result = _action(root, document, "ROLLBACK", approved_sha256)
    require(result["status"] == "WORKER_COMPLETED", "MAINTENANCE_RECOVERY_REQUIRED")
    return {
        "status": "ARCHIVE_FAILED_ROLLED_BACK",
        "production_data_archived": False,
        "services_restored": False,
        "submission_enabled": False,
    }


def recover_archive(root, *, approved_sha256):
    """Resume rollback of this window's known transaction; never reapply."""
    with _locked(root) as (root, document):
        _legacy(document)
        require(document["phase"] == "PAUSED", "WINDOW_NOT_PAUSED")
        return _recover_archive(root, document, approved_sha256)


def _prepare_aborted(root, document, previous):
    from tools import demo_maintenance_staging as staging

    path = root / "prepare-abort.json"
    if previous["action"] != "PREPARE" or (not path.exists() and not path.is_symlink()):
        return False
    marker = decode(read(path, 4096))
    require(
        type(marker) is dict
        and set(marker) == {"window_sha256", "grant_sha256", "approval_sha256"}
        and marker["window_sha256"] == document["inhibitor_sha256"]
        and marker["grant_sha256"] == previous["grant_sha256"],
        "PREPARE_ABORT_BINDING",
    )
    staging.verify_unmoved(root, document, approved_sha256=marker["approval_sha256"])
    return True


def abandon_prepare(root, *, approved_sha256):
    from tools import demo_maintenance_staging as staging

    with _locked(root) as (root, document):
        _legacy(document)
        require(document["phase"] == "PAUSED", "WINDOW_NOT_PAUSED")
        bound = staging.bound_receipt(root, document)
        require(bound["approval_sha256"] == approved_sha256, "MAINTENANCE_APPROVAL_BINDING")
        previous = _worker_record(root, document)
        require(previous is not None and previous["action"] == "PREPARE", "PREPARE_ABORT_SCOPE")
        if not handoff.drained(previous["unit"]):
            handoff.stop_owned(previous["unit"], previous["grant_sha256"])
        require(handoff.drained(previous["unit"]), "WINDOW_WORKER_STILL_RUNNING")
        result = staging.verify_unmoved(root, document, approved_sha256=approved_sha256)
        services._save(
            root / "prepare-abort.json",
            {
                "window_sha256": document["inhibitor_sha256"],
                "grant_sha256": previous["grant_sha256"],
                "approval_sha256": approved_sha256,
            },
        )
        return result


def execute_archive(root, *, approved_sha256):
    """Explicit batch approval, prepare/apply, and one automatic failure rollback.

    Services remain paused until the outer operator workflow calls restore.
    A restarted coordinator must use recovery for an already dispatched apply.
    """
    with _locked(root) as (root, document):
        _legacy(document)
        require(document["phase"] == "PAUSED", "WINDOW_NOT_PAUSED")
        previous = _worker_record(root, document)
        if previous is None:
            result = _action(root, document, "PREPARE", approved_sha256)
            require(result["status"] == "WORKER_COMPLETED", "MAINTENANCE_PREPARE_INCOMPLETE")
            if result["result"]["summary"]["status"] == "NO_ELIGIBLE_RECORDS":
                return result["result"]["summary"]
        else:
            require(
                previous["action"] == "PREPARE"
                and previous["result"] is not None
                and previous["result"]["status"] == "WORKER_COMPLETED",
                "MAINTENANCE_RECOVERY_REQUIRED",
            )
        try:
            result = _action(root, document, "APPLY", approved_sha256)
            require(result["status"] == "WORKER_COMPLETED", "MAINTENANCE_APPLY_INCOMPLETE")
            return result["result"]["summary"]
        except Exception:
            previous = _worker_record(root, document)
            if previous is None or previous["action"] != "APPLY":
                raise
            return _recover_archive(root, document, approved_sha256)


def _worker_record(root, document):
    path = root / "worker.json"
    if not path.exists() and not path.is_symlink():
        return None
    return _checked_worker(decode(read(path, 32768)), document)


def _checked_worker(record, document):
    require(
        type(record) is dict
        and set(record) == {"schema", "window_sha256", "unit", "action", "grant_sha256", "result"}
        and record["schema"] == "DEMO_MAINTENANCE_WORKER_DISPATCH_V1"
        and record["window_sha256"] == document["inhibitor_sha256"]
        and record["action"] in {"PREPARE", "APPLY", "ROLLBACK"}
        and handoff.grants.digest(record["grant_sha256"]),
        "WINDOW_WORKER_RECORD",
    )
    handoff._unit(record["unit"])
    result = record["result"]
    if result is not None:
        require(
            type(result) is dict
            and set(result) == {"status", "worker_drained", "result", "safe_to_restore_services"}
            and result["status"] in {"WORKER_COMPLETED", "WORKER_INSPECTION_REQUIRED"}
            and type(result["worker_drained"]) is bool
            and result["safe_to_restore_services"] is False,
            "WINDOW_WORKER_RESULT",
        )
        if result["status"] == "WORKER_COMPLETED":
            require(result["worker_drained"] is True, "WINDOW_WORKER_RESULT")
            packet = handoff._packet(json.dumps(result["result"]).encode(), record["action"])
            require(packet["ok"] is True, "WINDOW_WORKER_RESULT")
        else:
            require(result["result"] is None, "WINDOW_WORKER_RESULT")
    return record


def run_worker(root, fd, *, action, unit):
    """Dispatch within the locked paused window; incomplete work blocks restore."""
    with _locked(root) as (root, document):
        _legacy(document)
        return _run_worker(root, document, fd, action=action, unit=unit)


def _run_worker(root, document, fd, *, action, unit):
    require(document["phase"] == "PAUSED", "WINDOW_NOT_PAUSED")
    doc, raw = handoff.grants.read_descriptor(fd, action)
    require(
        doc["window_sha256"] == document["inhibitor_sha256"] and doc["archive_id"] == document["operation_sha256"],
        "WINDOW_WORKER_BINDING",
    )
    handoff._unit(unit)
    previous = _worker_record(root, document)
    if previous is not None:
        require(handoff.drained(previous["unit"]), "WINDOW_WORKER_STILL_RUNNING")
        if previous["result"] is None or previous["result"].get("status") != "WORKER_COMPLETED":
            require(action == "ROLLBACK", "WINDOW_WORKER_RECOVERY_REQUIRED")
    services.pause(root / "services.json", backend(document["inhibitor_sha256"]))
    record = {
        "schema": "DEMO_MAINTENANCE_WORKER_DISPATCH_V1",
        "window_sha256": document["inhibitor_sha256"],
        "unit": unit,
        "action": action,
        "grant_sha256": hashlib.sha256(raw).hexdigest(),
        "result": None,
    }
    services._save(root / "worker.json", record)  # Persist intent before starting any child.
    result = handoff.run_once(fd, action=action, unit=unit)
    services.pause(root / "services.json", backend(document["inhibitor_sha256"]))
    record["result"] = result
    _checked_worker(record, document)
    services._save(root / "worker.json", record)
    return result
