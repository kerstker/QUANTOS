"""Read-only collection handoff state from the exact original runner invocation."""
import sqlite3

from services.research.run_registry.contract import RUN_OWNER_RUNNER_INVOCATION, make_run_id
from services.research.run_registry.store import ResearchRunStore
from services.research_runner.run_kinds import RUN_KINDS
from services.research_runner.runner import validate_run_provenance


PUBLIC_FAILURES = frozenset(('GOLD_SIGNAL_INPUT_INCOMPLETE', 'GOLD_PAIRED_CAPTURE_FAILED',
    'GOLD_COLLECTION_STREAM_INCOMPLETE', 'GOLD_PREPARATION_SOURCE_CHANGED',
    'GOLD_COLLECTION_REQUEST_CHANGED', 'GOLD_COLLECTION_INPUT_ID_OCCUPIED'))


def observation(root, entry, *, research_created):
    result = {'status': 'UNKNOWN', 'run_id': None, 'reason': 'RUNNER_EVIDENCE_UNAVAILABLE',
              'research_created': research_created, 'authoritative': False}
    kind = 'gold_collect_campaign'
    params = {'attempt': entry['campaign_id']}
    invocation = f"factory-collection-{entry['campaign_id']}-{entry['request_hash']}"
    run_id = make_run_id(kind, params, 0, invocation_id=invocation)
    try:
        run = ResearchRunStore.read_snapshot(root/'data/cache/research_runs.db', run_id)
        if (run is None or run.owner != RUN_OWNER_RUNNER_INVOCATION or run.kind != kind
                or run.params != params or run.invocation_id != invocation):
            return result
        validate_run_provenance(run, RUN_KINDS[kind])
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return result
    result.update(run_id=run_id, reason='')
    if run.status in ('queued', 'running', 'cancelled'):
        return {**result, 'status': run.status.upper()}
    if run.status == 'error':
        return {**result, 'status': 'FAILED',
                'reason': run.error if run.error in PUBLIC_FAILURES else 'COLLECTION_FAILED'}
    if run.status == 'done':
        return {**result, 'status': 'RESEARCH_CREATED' if research_created else 'HANDOFF_INCOMPLETE',
                'reason': '' if research_created else 'RESEARCH_PLAN_NOT_OBSERVED'}
    return {**result, 'status': 'UNKNOWN', 'reason': 'RUNNER_EVIDENCE_UNAVAILABLE'}
