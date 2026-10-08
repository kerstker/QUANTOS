"""Read-only research evidence view; the composition root supplies a verifier.

No paths, file access, research jobs or trading operations are accepted from HTTP.
An unconfigured console reports missing evidence instead of a synthetic rating.
"""
import hashlib
import json
from fastapi import APIRouter, Request
from services.console.api.common import with_snapshot_metadata

router = APIRouter()
DIMENSIONS = {'data_trust', 'backtest_correctness', 'experiment_validity',
              'profitability_evidence', 'forward_and_testnet_evidence', 'research_efficiency'}


@router.get('/api/v1/console/research-assessment')
def research_assessment(request: Request):
    result = dict(source='BACKEND_COMPUTED_SNAPSHOT', read_only=True, api_version='0.1.0',
                  data_provenance='SAVED_RESEARCH_RECEIPTS_TECHNICAL_ONLY',
                  snapshot_status='NO_SNAPSHOT', authoritative=False, submission_enabled=False,
                  assessment=None)
    reader = getattr(request.app.state, 'research_assessment_reader', None)
    if reader is None:
        return with_snapshot_metadata(result)
    try:
        report = reader()
        if (report['assessment_kind'] != 'research_first_dimensions'
                or report['overall']['verdict'] != 'RESEARCH_EVIDENCE_INCOMPLETE'
                or report['overall']['aggregate_readiness_score_emitted'] is not False
                or len(report['dimensions']) != 6
                or {r['id'] for r in report['dimensions']} != DIMENSIONS
                or any(r['verdict'] not in {'UNKNOWN','PARTIAL','INSUFFICIENT'} for r in report['dimensions'])):
            raise ValueError('invalid research view')
        raw = json.dumps(report, sort_keys=True, allow_nan=False).encode()
        result.update(snapshot_status='REAL', snapshot_id=hashlib.sha256(raw).hexdigest(), assessment=report)
    except Exception:
        # Never return an old successful report or leak filesystem exception text.
        result.update(snapshot_status='DATA_BLOCKED', assessment=None)
    return with_snapshot_metadata(result)
