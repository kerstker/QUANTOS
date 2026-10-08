"""Schedule delivered bar diagnostics once on the existing research queue."""
import re
import stat

from services.research_runner.run_kinds import RUN_KINDS
from services.research_runner.runner import validate_run_provenance

KIND = 'gold_bar_feedback_research'


def feedback_tick(root, queue, registry, *, now, experiments=False):
    if type(experiments) is not bool:
        raise ValueError('BAR_FEEDBACK_EXPERIMENT_MODE')
    if any(row.status in ('queued', 'running') for row in registry.list(limit=200)):
        return {'status': 'WAITING_RUNNER'}
    directory = root/'data/cache/ai_factory/bar-execution-feedback'
    if not directory.exists() and not directory.is_symlink():
        return {'status': 'NO_BAR_FEEDBACK_READY'}
    if directory.resolve() != directory.absolute() or not directory.is_dir():
        raise ValueError('BAR_FEEDBACK_SCAN_PATH')
    paths = []
    total = 0
    for path in directory.iterdir():
        if path.name.startswith('.feedback-') and path.suffix == '.tmp':
            continue
        meta = path.lstat()
        if (re.fullmatch('[a-f0-9]{64}\\.json', path.name) is None
                or not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                or not 0 < meta.st_size <= 2000000):
            raise ValueError('BAR_FEEDBACK_SCAN_FILE')
        paths.append(path)
        total += meta.st_size
        if len(paths) > 1000 or total > 16000000:
            raise ValueError('BAR_FEEDBACK_SCAN_BUDGET')
    for path in sorted(paths):
        params = {'feedback_sha256': path.stem}
        identity = 'gold-bar-feedback-'+path.stem
        previous = registry.get_by_invocation(identity)
        if previous is not None:
            if previous.kind != KIND or previous.params != params:
                raise ValueError('BAR_FEEDBACK_RUN_BINDING')
            validate_run_provenance(previous, RUN_KINDS[KIND])
            # Only a completed registered hypothesis releases its one new window.
            if experiments and previous.status == 'done':
                if previous.metrics.get('feedback_sha256') != path.stem:
                    raise ValueError('BAR_FEEDBACK_HYPOTHESIS_BINDING')
                experiment_kind = 'gold_bar_feedback_experiment'
                experiment_key = identity+'-experiment'
                experiment = registry.get_by_invocation(experiment_key)
                if experiment is not None:
                    if experiment.kind != experiment_kind or experiment.params != params:
                        raise ValueError('BAR_FEEDBACK_EXPERIMENT_RUN_BINDING')
                    validate_run_provenance(experiment, RUN_KINDS[experiment_kind])
                else:
                    run_id = queue.submit(experiment_kind, params, now=now//1000,
                        operator='bar-feedback-monitor', invocation_id=experiment_key)
                    return {'status': 'SUBMITTED_BAR_FEEDBACK_EXPERIMENT', 'run_id': run_id,
                            'feedback_sha256': path.stem}
            # Terminal errors remain consumed; do not silently retry model calls.
            continue
        run_id = queue.submit(KIND, params, now=now//1000,
            operator='bar-feedback-monitor', invocation_id=identity)
        return {'status': 'SUBMITTED_BAR_FEEDBACK_RESEARCH', 'run_id': run_id,
                'feedback_sha256': path.stem}
    return {'status': 'NO_BAR_FEEDBACK_READY'}
