"""Read-only operational completion, separate from economic eligibility."""
import sqlite3
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.contract import RUN_OWNER_RUNNER_INVOCATION
from services.research_runner.run_kinds import RUN_KINDS
from services.research_runner.runner import validate_run_provenance


def workflow_payload(root, session, events, pipeline):
    result = {'status': 'NOT_STARTED', 'stage': 'RESEARCH', 'run_id': None,
              'runner_status': 'UNKNOWN', 'reason': '', 'can_resume': False}
    if not session:
        return result
    result.update(status='UNKNOWN', reason='RUNNER_EVIDENCE_UNAVAILABLE')
    event = next((e for e in events if e['kind'] == 'OPERATOR_ACTION'
                  and e['payload'].get('action') in ('create', 'resume')), None)
    if not event or not isinstance(event['payload'].get('run_id'), str):
        return result
    try:
        run = ResearchRunStore.read_snapshot(root/'data/cache/research_runs.db', event['payload']['run_id'])
        if (run is None or run.owner != RUN_OWNER_RUNNER_INVOCATION
                or run.kind != 'ai_factory_pipeline' or run.params.get('attempt') != session['attempt']):
            return result
        validate_run_provenance(run, RUN_KINDS[run.kind])
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return result
    result.update(run_id=run.run_id, runner_status=run.status)
    state = session['state']
    if state == 'COMPLETED':
        result['stage'] = (pipeline or {}).get('stage', 'POSTPROCESSING')
    if run.status in ('queued', 'running'):
        return {**result, 'status': run.status.upper(), 'reason': ''}
    if state == 'CANCELLED' or session['command'] == 'cancel':
        return {**result, 'status': 'CANCELLED', 'reason': ''}
    if run.status in ('error', 'cancelled'):
        return {**result, 'status': 'FAILED', 'stage': 'RESULT_REGISTRATION' if (pipeline or {}).get('status') == 'COMPLETED' else result['stage'], 'reason': 'RUNNER_FAILED', 'can_resume': True}
    if run.status != 'done':
        return result
    if state == 'PAUSED' or session['command'] == 'pause':
        return {**result, 'status': 'PAUSED', 'reason': '', 'can_resume': True}
    if state == 'COMPLETED' and (pipeline or {}).get('status') == 'COMPLETED':
        return {**result, 'status': 'COMPLETED', 'reason': ''}
    return {**result, 'status': 'FAILED', 'reason': 'WORKFLOW_INCOMPLETE', 'can_resume': True}
