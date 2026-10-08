"""Use the original Gold producer and registry, retaining a compatibility mirror.

The managed project owns the sole native invocation and trial ledger. The old
Gold views receive exact retained bytes, never another registered producer call
or ledger writer. Their existing diagnostic replay remains. The mirror is
explicitly marked and cannot enter standalone recovery.
"""
import time

from quantos.contracts import registered_gold_collection as shape
from quantos.contracts.gold_forward_evaluation_design import FULL_REVIEW_SCHEMAS, LONG_REVIEW_SCHEMAS
from tools import registered_research_job as job
from tools import registered_research_run as native
from tools import gold_bar_research as gold
from tools.gold_bar_experiment import bound_archive

common = job.common
MARKER = 'registered-native.json'
STATISTICS_CUT = 'native-statistics-cut.json'
MAX_STATISTICS_CUT_BYTES = 64_000_000


def invocation_id(attempt):
    common.require(type(attempt) is int and 1 <= attempt <= 1000, 'GOLD_REGISTERED_ATTEMPT')
    # Content changes cannot quietly allocate a second invocation for the slot.
    return common.plan_digest(dict(schema='QUANTOS_COLLECTED_GOLD_INVOCATION_V1', attempt=attempt))


def _metadata(plan):
    value = dict(schema=shape.SCHEMA, native_invocation_id=plan['invocation_id'],
        plan_sha256=common.plan_digest(plan), input_sha256=plan['input_sha256'],
        registered_at_ms=plan['search']['recorded_at_ms'], attempt_count=len(plan['trials']),
        native_trial_ids=[row['native_trial_id'] for row in plan['trials']],
        evaluation_plan='NOT_PREREGISTERED', statistical_review='FORWARD_ADAPTER_UNAVAILABLE',
        risk_review='NOT_INDEPENDENTLY_REPLAYED', locked_oos='UNKNOWN', candidate_admission='NOT_APPROVED',
        economic_authority=False, promotion_approved=False, submission_enabled=False)
    if plan['schema'] == common.contract.FORWARD_DECLARED_SCHEMA:
        design = plan['forward_design']
        value.update(schema=shape.DECLARED_SCHEMA, evaluation_plan='WINDOW_DECLARED_UNVERIFIED',
            forward_declaration=dict(design_sha256=common.plan_digest(design),
                declared_at_ms=design['declared_at_ms'], observation_start_ms=design['window']['observation_start_ms'],
                closed_bars=design['window']['closed_bars'], time_evidence='LOCAL_CLOCK_NOT_INDEPENDENTLY_VERIFIED'))
        if design['schema'] in FULL_REVIEW_SCHEMAS:
            value.update(schema=shape.LONG_REVIEW_SCHEMA if design['schema'] in LONG_REVIEW_SCHEMAS else shape.REVIEW_SCHEMA,
                         statistical_review='STATISTICAL_POLICY_DECLARED')
    return shape.validate(value)


def _marker(plan, attempt):
    return dict(schema='QUANTOS_GOLD_NATIVE_COMPATIBILITY_MIRROR_V1', attempt=attempt,
        native_invocation_id=plan['invocation_id'], plan_sha256=common.plan_digest(plan),
        input_sha256=plan['input_sha256'], read_only_mirror=True, economic_authority=False, submission_enabled=False)


def _mirror_start(directory, plan, attempt):
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    common.require(directory.parent.resolve() == directory.parent.absolute(), 'GOLD_MIRROR_PATH')
    with common._locked(directory):
        if directory.exists():
            common._directory(directory)
            common.require((directory/MARKER).is_file(), 'GOLD_STANDALONE_HISTORY_PRESENT')
        else:
            common._mkdir(directory)
        common._put(directory/MARKER, _marker(plan, attempt))


def _mirror_terminal(directory, original, plan, attempt):
    # Publish only terminal originals. SQLite is already closed here; no WAL
    # is omitted or merged and the compatibility copy has no writable API.
    if not (original.parent/'outcome.json').exists():
        return
    with common._locked(directory):
        common.require(common._load(directory/MARKER) == _marker(plan, attempt), 'GOLD_MIRROR_BINDING')
        for name in ('input.json', 'report.json', 'manifest.json', 'trials.db'):
            if (original/name).exists():
                common._put(directory/name, None, raw=common._read(original/name))
        common._sync(directory)


def _publish(directory, plan, attempt):
    native_root = job.workspace_paths()[0]/'runs'/plan['invocation_id']/'archive'
    for name in ('input.json', 'report.json', 'manifest.json', 'trials.db'):
        common.require(common._read(directory/name) == common._read(native_root/name), 'GOLD_MIRROR_CHANGED')
    # Original read-only replay is diagnostic; it does not allocate an attempt.
    publication = gold._publication(directory, attempt)
    publication['report'].update(retained_evidence(directory, attempt, publish_statistics=True))
    return publication


def _extends_original_cut(previous, current):
    """Later attempts append history; they never rewrite a published old cut."""
    from services.validation.project_research_population import validate_project_cut
    validate_project_cut(previous)
    for key, rows in previous['registry_history'].items():
        if key == 'evaluations':
            # This immutable table is sorted by run ID, rather than append seq.
            by_id = {row['run_id']:row for row in current['registry_history'][key]}
            unchanged = all(by_id.get(row['run_id']) == row for row in rows)
        else:
            unchanged = current['registry_history'][key][:len(rows)] == rows
        common.require(unchanged, 'GOLD_STATISTICS_REGISTRY_HISTORY_CHANGED')
    for identity, run in previous['runs'].items():
        next_run = current['runs'].get(identity)
        common.require(next_run is not None and common.canonical(next_run['plan']) == common.canonical(run['plan']),
            'GOLD_STATISTICS_ORIGINAL_RUN_CHANGED')
        for name, record in run['originals'].items():
            # An interrupted archive can finish its original manifest. Complete
            # reports/outcomes and all registration/input witnesses stay exact.
            if name == 'archive/manifest.json' and 'outcome.json' not in run['originals']:
                continue
            common.require(next_run['originals'].get(name) == record, 'GOLD_STATISTICS_ORIGINAL_CHANGED')
        for trial, events in run['ledger']['history'].items():
            common.require(next_run['ledger']['history'].get(trial, [])[:len(events)] == events,
                'GOLD_STATISTICS_NATIVE_HISTORY_CHANGED')


def retained_evidence(directory, attempt, *, publish_statistics=False):
    """Read original plan, population and both archives in one physical read set."""
    from pathlib import Path
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader, _ProjectReads, _decode
    directory = Path(directory).absolute()
    common.require(directory.name == str(attempt) and directory.parent.name == 'results'
        and directory.parent.parent.name == 'gold_bar_research', 'GOLD_MIRROR_PATH')
    base = directory.parent.parent.parent/'registered_research'
    reads = _ProjectReads()
    reads.directory(directory)
    marker = _decode(reads.read(directory/MARKER, 65536))
    project = base/'project'
    cut = ProjectResearchEvidenceReader(project, registry_database=base/'registry.db')._capture(reads)
    identity = invocation_id(attempt)
    run = cut['runs'].get(identity)
    common.require(run is not None and not run['partial'], 'GOLD_ORIGINAL_NOT_COMPLETE')
    plan = run['plan']
    common.require(plan['adapter'] == common.contract.GOLD
        and marker == _marker(plan, attempt), 'GOLD_MIRROR_BINDING')
    rows = [row for row in cut['attempts'] if row['invocation_id'] == identity]
    common.require(len(rows) == len(plan['trials']) and all(row['status'] == 'PASSED' for row in rows),
        'GOLD_ORIGINAL_NOT_PASSED')
    original = project/'runs'/identity/'archive'
    def raw(path):
        limit = reads.files[path][2] if path in reads.files else common.contract.MAX_OUTPUT_BYTES
        return reads.read(path, limit)
    for name in ('input.json', 'report.json', 'manifest.json', 'trials.db'):
        common.require(raw(directory/name) == raw(original/name), 'GOLD_MIRROR_CHANGED')
    value = dict(native_registration=_metadata(plan))
    pending = None
    if plan['schema'] == common.contract.FORWARD_DECLARED_SCHEMA:
        from services.validation.gold_native_statistics import project_gold_statistics
        path = directory/STATISTICS_CUT
        if path.exists() or path.is_symlink():
            statistics_cut = _decode(reads.read(path, MAX_STATISTICS_CUT_BYTES))
            _extends_original_cut(statistics_cut, cut)
        else:
            common.require(publish_statistics, 'GOLD_STATISTICS_PUBLICATION_CUT_MISSING')
            statistics_cut = cut
            pending = common.contract.canonical(cut, maximum=MAX_STATISTICS_CUT_BYTES, max_nodes=1_000_000)
        value['native_statistics'] = project_gold_statistics(project_cut=statistics_cut, invocation_id=identity)
    reads.recheck()
    if pending is not None:
        # Publish after the complete original physical read set is rechecked.
        # This derived cut never writes original registry/ledger/archive state.
        with common._locked(directory):
            common._put(directory/STATISTICS_CUT, None, raw=pending)
    return value


def retained_registration(directory, attempt):
    return retained_evidence(directory, attempt)['native_registration']


def publication_recoverable(params):
    """Only original durable computation can enter automatic publication recovery."""
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader, _ProjectReads, _decode
    arguments, output = gold._arguments(params)
    attempt = params['attempt']
    project, database = job.workspace_paths()
    reads = _ProjectReads()
    mirror = output/str(attempt)
    reads.directory(mirror)
    marker = _decode(reads.read(mirror/MARKER, 65536))
    cut = ProjectResearchEvidenceReader(project, registry_database=database)._capture(reads)
    run = cut['runs'].get(invocation_id(attempt))
    common.require(run is not None and marker == _marker(run['plan'], attempt)
        and run['plan']['source_identity'] == arguments['release_identity']
        and run['plan']['input_sha256'] == common.plan_digest(common._gold_input(arguments)),
        'GOLD_RECOVERY_ORIGINAL_BINDING')
    request_root = output.parent/'requests'/str(attempt)
    if 'forward_design' in run['plan']:
        reads.directory(request_root)
        request_plan = _decode(reads.read(request_root/'plan.json', 65536))
    else:
        # Existing V1 public requests never carried a private declaration.
        # Preserve their original read boundary; they cannot acquire a design.
        from tools.gold_cost_review import _read
        request_plan = _decode(_read(request_root, 'plan.json'))
    common.require(request_plan.get('register_original_trials') is True
        and common.canonical(request_plan.get('forward_design')) == common.canonical(run['plan'].get('forward_design')),
        'GOLD_RECOVERY_DECLARATION_BINDING')
    recoverable = 'archive/report.json' in run['originals']
    if 'outcome.json' in run['originals']:
        path = project/'runs'/invocation_id(attempt)/'outcome.json'
        outcome = _decode(reads.read(path, reads.files[path][2]))
        recoverable = recoverable and outcome['status'] == 'PASSED'
    reads.recheck()
    return recoverable


def run(params, *, recovering=False):
    arguments, output = gold._arguments(params)
    attempt = params['attempt']
    common.require(gold._registered_request(params), 'GOLD_REGISTERED_REQUEST_CHANGED')
    from tools.gold_cost_review import _read
    from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
    request_root = output.parent/'requests'/str(attempt)
    request_raw = _read(request_root, 'plan.json')
    request_plan = decode(request_raw)
    forward_design = request_plan.get('forward_design')
    common.require(forward_design is None or forward_design['attempt'] == attempt, 'GOLD_FORWARD_ATTEMPT')
    project, database = job.workspace_paths()
    destination = project/'runs'/invocation_id(attempt)
    mirror = output/str(attempt)
    if recovering:
        common.require((destination/'invocation.json').exists(), 'RECOVERY_NOT_STARTED')
    # Refuse retrospective import of a standalone attempt before making a plan.
    if mirror.exists():
        common._directory(mirror)
        common.require((mirror/MARKER).is_file(), 'GOLD_STANDALONE_HISTORY_PRESENT')
    database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with job._registry(database, initialize=not recovering) as registry:
        prior = common._load(destination/'plan.json') if (destination/'plan.json').exists() else None
        now = time.time_ns() // 1_000_000
        registered = prior['search']['recorded_at_ms'] if prior is not None else now
        common._time(lambda:time.time_ns()//1_000_000, registered)
        common.require(common._gold_input(gold._arguments(params)[0]) == common._gold_input(arguments), 'GOLD_REQUEST_CHANGED')
        common.require(_read(request_root, 'plan.json') == request_raw, 'GOLD_REQUEST_CHANGED')
        plan = native.prepare_gold(arguments, project_root=project,
            search_id='collected-gold-'+str(attempt), producer_id='REGISTERED_GOLD_COLLECTOR_V1',
            actor_id='AUTOMATED_RESEARCH', dataset_version=common.plan_digest(native._gold_input(arguments)),
            registered_at_ms=registered, invocation_id=invocation_id(attempt), forward_design=forward_design)
        _mirror_start(mirror, plan, attempt)
        try:
            native.run_gold(arguments, destination=destination, registry=registry)
        finally:
            _mirror_terminal(mirror, destination/'archive', plan, attempt)
    # Source and original bindings are rechecked by the original publisher.
    bound_archive(destination/'archive')
    return _publish(mirror, plan, attempt)
