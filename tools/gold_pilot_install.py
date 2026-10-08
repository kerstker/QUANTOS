"""Review or independently install a disabled finite gold pilot handoff.

Exact original renderer bytes, accepted source and an existing gold budget
policy are required. This tool never grants permission, starts a service,
reads credentials, resets a pilot or modifies authority and execution stores.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import GOLD_BUDGET_POLICY_SCHEMAS, current_key, require
from live.execution.demo_authority import _require_protected
from scripts.render_gold_pilot_unit import render
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
from tools.demo_resident_recovery import _check_path
from tools.gold_strategy_policy_install import local_operator

UNIT = Path('/etc/systemd/system/quantos-gold-pilot.service')
DAY_MS = 86_400_000


class InstallIncomplete(ValueError):
    def __init__(self, result):
        super().__init__('GOLD_PILOT_INSPECT_PARTIAL_INSTALL')
        self.result = result


def _unit_absent():
    result = subprocess.run(['systemctl', 'show', '--no-pager',
        '--property=LoadState,ActiveState', UNIT.name],
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'SYSTEMD_COLORS': '0'},
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=15, check=False)
    require(result.returncode in (0, 4) and len(result.stdout) <= 4096,
        'PILOT_INSTALL_UNIT_STATUS_UNKNOWN')
    require(result.stdout.decode('ascii').splitlines() in
        (['LoadState=not-found', 'ActiveState=inactive'],
         ['ActiveState=inactive', 'LoadState=not-found']), 'PILOT_INSTALL_UNIT_ALREADY_LOADED')


def _protected_targets(registry):
    for parent in (UNIT.parent, *UNIT.parent.parents):
        _require_protected(parent.lstat(), directory=True)
    _check_path(installer.EXECUTOR, registry['executor_uid'], directory=True)


def _absent():
    paths = (UNIT, installer.EXECUTOR / 'gold-pilot-request.json', installer.EXECUTOR / 'gold-pilot')
    require(all(not path.exists() and not path.is_symlink() for path in paths),
        'PILOT_INSTALL_EXISTING_OR_PARTIAL_HANDOFF')


def _target_metadata(info, *, uid, maximum):
    require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_nlink == 1
        and not info.st_mode & 0o022 and 0 < info.st_size <= maximum,
        'PILOT_INSTALL_TARGET_CUSTODY')


def _read_target(path, *, uid, maximum=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno()); _target_metadata(before, uid=uid, maximum=maximum)
        body = stream.read(maximum + 1); after = os.fstat(stream.fileno())
    _target_metadata(after, uid=uid, maximum=maximum)
    current = path.lstat()
    _target_metadata(current, uid=uid, maximum=maximum)
    require(len(body) == before.st_size and all(getattr(before, k) == getattr(after, k)
        for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))
        and all(getattr(current, k) == getattr(after, k)
            for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')),
        'PILOT_INSTALL_TARGET_CHANGED')
    return body


def _materials(*, source_commit, bundle_dir, review_sha256, research_state_root):
    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}', source_commit) is not None
        and type(review_sha256) is str and re.fullmatch('[a-f0-9]{64}', review_sha256) is not None,
        'PILOT_INSTALL_ARGUMENTS')
    bundle_dir = Path(bundle_dir)
    raw = installer.read_private(bundle_dir / 'review.json', maximum=65536)
    require(hashlib.sha256(raw).hexdigest() == review_sha256, 'PILOT_INSTALL_REVIEW_DIGEST')
    manifest = decode(raw)
    from tools.gold_scope_pilot_materials import SCHEMA as SCOPE_REVIEW_SCHEMA
    scoped=type(manifest) is dict and manifest.get('schema')==SCOPE_REVIEW_SCHEMA
    fields = set(('schema status release_commit account_ref_sha256 files execution_requested '
        'installed started software_acceptance_verified independent_policy_verified remaining').split())
    if scoped:fields|={'budget_scope_sha256','scope_registry_sha256'}
    require(type(manifest) is dict and set(manifest) == fields
        and manifest['schema'] in ('GOLD_PILOT_UNIT_REVIEW_V1',SCOPE_REVIEW_SCHEMA)
        and manifest['status'] == 'PREPARED_NOT_APPLIED'
        and manifest['release_commit'] == source_commit
        and type(manifest['execution_requested']) is bool
        and all(manifest[k] is False for k in ('installed', 'started',
            'software_acceptance_verified', 'independent_policy_verified'))
        and type(manifest['remaining']) is list and len(manifest['remaining']) == 4
        and all(type(v) is str and 0 < len(v) <= 300 for v in manifest['remaining']),
        'PILOT_INSTALL_REVIEW_FIELDS')
    extra={}
    if scoped:
        from live.execution.demo_gold_pilot_enrollment import SCOPE_REQUEST_SCHEMA, _request_check
        retained=decode(installer.read_private(bundle_dir/'gold-pilot-request.json',maximum=65536))
        _request_check(retained,source_commit=source_commit,account=manifest['account_ref_sha256'])
        require(retained['schema']==SCOPE_REQUEST_SCHEMA
            and _digest(retained['budget_scope'])==manifest['budget_scope_sha256']
            and _digest(retained['scope_registry'])==manifest['scope_registry_sha256'],'PILOT_INSTALL_SCOPE_MATERIALS')
        extra=dict(budget_scope=retained['budget_scope'],scope_registry=retained['scope_registry'])
    rendered = render(release_commit=source_commit, account_ref_sha256=manifest['account_ref_sha256'],
        research_state_root=research_state_root, execute_approved=manifest['execution_requested'],**extra)
    expected = {'quantos-gold-pilot.service': rendered['service'].encode(),
        'gold-pilot-request.json': (json.dumps(rendered['enrollment_request'], sort_keys=True) + '\n').encode()}
    require(type(manifest['files']) is dict and set(manifest['files']) == set(expected),
        'PILOT_INSTALL_FILE_SET')
    files = {name: installer.read_private(bundle_dir / name, maximum=65536) for name in expected}
    require(files == expected and manifest['files'] ==
        {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
        'PILOT_INSTALL_RENDER_MISMATCH')
    return manifest, files


def _configuration(source_commit,scoped):
    if scoped:
        from tools.gold_strategy_controller import observation_configuration
        from live.execution.demo_gold_budget_scope import pilot_window
        registry,policy=observation_configuration(source_commit)
        pilot_window(policy,registry=registry,now_ms=supervisor.now_ms())
    else:
        registry,policy=supervisor.configuration(source_commit)
        current_key(policy,registry,supervisor.now_ms())
        require(policy['schema'] in GOLD_BUDGET_POLICY_SCHEMAS
            and policy['valid_until_ms']-supervisor.now_ms()>=DAY_MS,'PILOT_INSTALL_AUTHORIZATION_WINDOW')
    require(policy['scope']['symbol']=='XAUUSDT','PILOT_INSTALL_POLICY_SCOPE')
    return registry,policy


def _feedback_destination(root, *, uid, gid, bar):
    """Inspect the original publisher's destination without creating anything."""
    from tools.demo_feedback_publish import custody
    destination = root / 'data/cache/ai_factory' / ('bar-execution-feedback' if bar else 'execution-feedback')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    current = Path('/'); directories = []
    try:
        for name in destination.parts[1:]:
            current = current / name
            try:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=fd)
            except FileNotFoundError:
                # The publisher may create descendants only after dropping UID;
                # require the enrolled research root itself to exist already.
                require(root in current.parents, 'PILOT_INSTALL_FEEDBACK_ROOT_MISSING')
                break
            os.close(fd); fd = child
            info = os.fstat(fd)
            permissions = (info.st_mode >> (6 if info.st_uid == uid else 3 if info.st_gid == gid else 0)) & 0o7
            require(permissions & 0o1, 'PILOT_INSTALL_FEEDBACK_DIRECTORY_ACCESS')
            if current == root or root in current.parents:
                custody._owned(info, uid, directory=True)
                require(permissions & 0o3 == 0o3, 'PILOT_INSTALL_FEEDBACK_DIRECTORY_ACCESS')
                directories.append(dict(path=str(current.relative_to(root)), device=info.st_dev,
                    inode=info.st_ino, uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode)))
        return directories
    finally:
        os.close(fd)


def _feedback_preflight(*, source_commit, registry, research_state_root, runtime):
    """Bind the fixed publication profile; account binding stays in the policy."""
    from tools import demo_feedback_publish as feedback
    config = feedback.configuration(source_commit)
    require(config['executor_uid'] == registry['executor_uid']
        and config['research_uid'] in registry['research_uids']
        and config['research_gid'] not in (registry['executor_uid'], registry['issuer_uid']),
        'PILOT_INSTALL_FEEDBACK_IDENTITIES')
    require(config['research_state_root'] == research_state_root, 'PILOT_INSTALL_FEEDBACK_ROOT')
    bar = runtime['schema'].startswith('GOLD_BAR_')
    directories = _feedback_destination(Path(research_state_root),
        uid=config['research_uid'], gid=config['research_gid'], bar=bar)
    require(feedback.configuration(source_commit) == config, 'PILOT_INSTALL_FEEDBACK_CHANGED')
    return dict(configuration=config, configuration_sha256=_digest(config),
        destination_directories=directories, bar=bar, delivery_verified=False,
        submission_enabled=False, economic_authority=False)


def review(*, source_commit, bundle_dir, review_sha256, research_state_root):
    manifest, files = _materials(source_commit=source_commit, bundle_dir=bundle_dir,
        review_sha256=review_sha256, research_state_root=research_state_root)
    from tools.gold_scope_pilot_materials import SCHEMA as SCOPE_REVIEW_SCHEMA
    scoped=manifest['schema']==SCOPE_REVIEW_SCHEMA
    registry, policy = _configuration(source_commit,scoped)
    require(policy['account_ref_sha256']==manifest['account_ref_sha256'],'PILOT_INSTALL_POLICY_SCOPE')
    if scoped:
        require(_digest(policy)==manifest['budget_scope_sha256']
            and _digest(registry)==manifest['scope_registry_sha256'],'PILOT_INSTALL_SCOPE_CHANGED')
    runtime = decode(supervisor._read_root_owned_json(supervisor.STATE / 'strategy-runtime.json'))
    require(type(runtime) is dict and runtime.get('source_commit') == source_commit
        and runtime.get('schema') in ('GOLD_STRATEGY_WORKER_INPUT_V1',
            'GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1', 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1',
            'GOLD_BAR_FRESH_EPISODE_INPUT_V1', 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'),
        'PILOT_INSTALL_RUNTIME_SCOPE')
    if scoped:
        require(_digest(runtime)==policy['scope_enrollment']['runtime_sha256'],'PILOT_INSTALL_SIGNED_RUNTIME_CHANGED')
    _protected_targets(registry)
    _absent()
    _unit_absent()
    feedback = _feedback_preflight(source_commit=source_commit, registry=registry,
        research_state_root=research_state_root, runtime=runtime)
    result=dict(schema='GOLD_PILOT_INSTALL_REVIEW_V1', status='PILOT_INSTALL_REQUIRES_LOCAL_OPERATOR',
        source_commit=source_commit, review_sha256=review_sha256,
        account_ref_sha256=manifest['account_ref_sha256'], research_state_root=research_state_root,
        registry_sha256=_digest(registry), policy_sha256=_digest(policy), runtime_sha256=_digest(runtime),
        authorization_valid_until_ms=policy['valid_until_ms'], files=manifest['files'],
        duration_seconds=86400, max_loss_fraction='0.05', execution_requested=manifest['execution_requested'],
        installed=False, started=False, economic_authority=False, remaining_budget_verified=False,
        strategy_inputs_verified=False, feedback_preflight=feedback)
    if scoped:result.update(schema='GOLD_PILOT_INSTALL_REVIEW_V2',scope_only=True,execution_approval_granted=False)
    return result


def confirmation(candidate, operator):
    require(local_operator() == operator, 'PILOT_INSTALL_OPERATOR_CHANGED')
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    print('安装24小时/5%黄金 Demo 控制与禁用服务。启动另行操作；原批准、预算、风控与账户基线保持。')
    expected = 'INSTALL GOLD PILOT ' + _digest(candidate)
    require(input('请输入 ' + expected + '\n').strip() == expected, 'PILOT_INSTALL_NOT_CONFIRMED')
    return dict(schema='GOLD_PILOT_INSTALL_OPERATOR_RECORD_V1', candidate_sha256=_digest(candidate),
        operator_id=operator, approved_at_ms=supervisor.now_ms(), economic_authority=False)


def install_approved(**args):
    operator = local_operator()
    with supervisor.controller_lease(maintenance=True):
        candidate = review(**args)
        approval = confirmation(candidate, operator)
        require(review(**args) == candidate, 'PILOT_INSTALL_INPUTS_CHANGED')
        area = supervisor.STATE / 'gold-pilot-installations'; stage = area / _digest(candidate)
        mutating = False
        try:
            if not area.exists() and not area.is_symlink():
                mutating = True; installer.directory(area, 0, 0)
            supervisor._root_private_directory(area)
            history = list(area.iterdir())
            require(len(history) < 4096, 'PILOT_INSTALL_HISTORY_BUDGET')
            for path in history:
                require(re.fullmatch('[a-f0-9]{64}', path.name) is not None, 'PILOT_INSTALL_HISTORY_IDENTITY')
                supervisor._root_private_directory(path)
            require(not stage.exists() and not stage.is_symlink(), 'PILOT_INSTALL_PRIOR_ATTEMPT')
            mutating = True; installer.directory(stage, 0, 0)
            installer.write_new(stage / 'candidate.json', installer.canonical(candidate))
            installer.write_new(stage / 'operator.json', installer.canonical(approval))
            scoped=candidate.get('scope_only') is True
            registry, policy = _configuration(args['source_commit'],scoped)
            installer.write_new(stage / 'registry.json', installer.canonical(registry))
            installer.write_new(stage / 'policy.json', installer.canonical(policy))
            installer.write_new(stage / 'feedback-configuration.json',
                installer.canonical(candidate['feedback_preflight']['configuration']))
            manifest, files = _materials(**args)
            for name, body in files.items(): installer.write_new(stage / name, body)
            require(local_operator() == operator and review(**args) == candidate, 'PILOT_INSTALL_INPUTS_CHANGED')
            registry, _ = _configuration(args['source_commit'],scoped)
            installer.write_new(installer.EXECUTOR / 'gold-pilot-request.json', files['gold-pilot-request.json'],
                uid=registry['executor_uid'], gid=registry['executor_uid'])
            installer.write_new(UNIT, files[UNIT.name], mode=0o644)
            _check_path(installer.EXECUTOR / 'gold-pilot-request.json', registry['executor_uid'], directory=False)
            require(_read_target(installer.EXECUTOR / 'gold-pilot-request.json', uid=registry['executor_uid'])
                == files['gold-pilot-request.json'] and _read_target(UNIT, uid=0) == files[UNIT.name],
                'PILOT_INSTALL_READBACK_CHANGED')
            current_registry, current_policy = _configuration(args['source_commit'],scoped)
            current_runtime = decode(supervisor._read_root_owned_json(supervisor.STATE / 'strategy-runtime.json'))
            require(_digest(current_registry) == candidate['registry_sha256']
                and _digest(current_policy) == candidate['policy_sha256']
                and _digest(current_runtime) == candidate['runtime_sha256']
                and _feedback_preflight(source_commit=args['source_commit'], registry=current_registry,
                    research_state_root=args['research_state_root'], runtime=current_runtime) == candidate['feedback_preflight'],
                'PILOT_INSTALL_POST_WRITE_INPUTS_CHANGED')
            result = dict(schema='GOLD_PILOT_INSTALL_RESULT_V1', status='PILOT_INSTALLED_NOT_STARTED',
                source_commit=args['source_commit'], candidate_sha256=_digest(candidate),
                review_sha256=args['review_sha256'], installed=True, started=False,
                orders_submitted=False, economic_authority=False)
            installer.write_new(stage / 'result.json', installer.canonical(result))
            return result
        except Exception as exc:
            if not mutating: raise
            result = dict(schema='GOLD_PILOT_INSTALL_RESULT_V1', status='INSPECT_PARTIAL_PILOT_INSTALL',
                source_commit=args['source_commit'], candidate_sha256=_digest(candidate),
                installed=None, started=False, orders_submitted=False, economic_authority=False,
                error_type=type(exc).__name__, failure_record_retained=True)
            try: installer.write_new(stage / 'failure.json', installer.canonical(result))
            except Exception: result['failure_record_retained'] = False
            raise InstallIncomplete(result) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--bundle-dir', type=Path, required=True)
    parser.add_argument('--review-sha256', required=True)
    parser.add_argument('--research-state-root', required=True)
    parser.add_argument('--install-approved', action='store_true')
    args = vars(parser.parse_args(argv)); apply = args.pop('install_approved')
    previous = os.umask(0o077)
    try:
        result = install_approved(**args) if apply else review(**args)
        print(json.dumps(result, sort_keys=True)); return 0
    except InstallIncomplete as exc:
        print(json.dumps(exc.result, sort_keys=True)); return 3
    except Exception as exc:
        print(json.dumps(dict(schema='GOLD_PILOT_INSTALL_RESULT_V1', status='REJECTED_BEFORE_INSTALL',
            changed_by_this_attempt=False, started=False, orders_submitted=False,
            economic_authority=False, error_type=type(exc).__name__), sort_keys=True)); return 3
    finally: os.umask(previous)


if __name__ == '__main__': raise SystemExit(main())
