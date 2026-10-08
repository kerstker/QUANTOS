"""Read-only gold round results bound to the original retained cost report."""
from services.console.api.gold_factory_feedback import read_feedback, project_feedback
from decimal import Decimal
import time

from quantos.local_artifacts import with_result_hash
from services.research import gold_review_watch as watch

from services.console.api.state_artifact import VALID, read_json_artifact
from services.execution.backtest_authority import exact_identity_hash
from services.research.gold_factory_candidates import candidates


def _latest_review(root,trial,trial_id):
    state,artifact=read_json_artifact(trial/'latest-review.json',trusted_root=root)
    if state=='MISSING':return None
    try:
        now=time.time_ns()//1000000
        if (state!=VALID or artifact.get('schema')!='GOLD_FACTORY_REVIEW_VIEW_V1'
                or with_result_hash({k:v for k,v in artifact.items() if k!='result_hash'})!=artifact
                or artifact['authoritative'] is not False or artifact['economic_eligible'] is not False):
            raise ValueError('invalid review view')
        review=artifact['review'];verification=review['verification']
        if (review['trial_id']!=trial_id or review['authoritative'] is not False
                or review['economic_eligible'] is not False or verification['economic_eligible'] is not False
                or verification['authoritative'] is not False or type(review['checked_at_ms']) is not int
                or not 0<=review['checked_at_ms']<=now):raise ValueError('invalid review binding')
        current=artifact['observation_hash']==watch.observation(trial,watch.REGISTRY,now=now)
        return {'status':verification['status'] if current else 'PENDING_RECHECK',
                'checked_at_ms':review['checked_at_ms'],'current':current}
    except (ValueError,KeyError,TypeError,OSError):
        return {'status':'UNAVAILABLE','checked_at_ms':None,'current':False}


def results(root, sessions):
    projected = []
    feedback,feedback_unavailable=read_feedback(root)
    for session in sessions:
        if session['config'].get('market') != 'XAUUSDT':
            continue
        attempt = session['attempt']
        if type(attempt) is not int or not 1 <= attempt <= 1000:
            continue
        summary = session['result']
        candidate_id = summary.get('candidate_id')
        candidate = next((c for c in candidates() if type(candidate_id) is int and c['id'] == candidate_id), None)
        item = {'attempt': attempt, 'state': session['state'], 'candidate': candidate,
                'trial_id': summary.get('trial_id'), 'report_status': 'NO_REPORT',
                'outcome': None, 'costs': None, 'recorded_verification_status': None, 'latest_review':None,
                'economic_eligible': False, 'authoritative': False,
                'execution_review':{'status':'NO_REPORT','records':[]}}
        if session['state'] == 'COMPLETED':
            item['report_status'] = 'UNAVAILABLE'
            path = root/f'data/cache/ai_factory/gold/results/{attempt}/trial/report.json'
            status, artifact = read_json_artifact(path, trusted_root=root, with_digest=True)
            try:
                if status != VALID or artifact['input_sha256'] != summary['report_sha256']:
                    raise ValueError('report unavailable or changed')
                report = artifact['document']
                if (summary['attempt'] != attempt or report['schema'] != 'GOLD_COST_REPLAY_V1' or report['authoritative'] is not False
                        or report['evidence_status'] != 'TECHNICAL_VALIDATION_ONLY'
                        or report['result_hash'] != summary['result_hash']
                        or report['result_hash'] != exact_identity_hash(
                            {k: v for k, v in report.items() if k != 'result_hash'}, name='gold_cost_replay')
                        or report['outcome'] != summary['outcome']
                        or report['simulation']['reconciliation'] != summary['reconciliation']):
                    raise ValueError('report binding mismatch')
                costs = report['simulation']['reconciliation']
                if not isinstance(costs, dict) or any(type(v) is not str or len(v) > 100 or not Decimal(v).is_finite() for v in costs.values()):
                    raise ValueError('invalid costs')
                item.update(report_status='AVAILABLE', outcome=report['outcome'], costs=costs,
                            recorded_verification_status=summary['verification_status'],
                            latest_review=_latest_review(root,path.parent,summary['trial_id']),
                            execution_review=project_feedback(session,feedback,feedback_unavailable,sessions))
            except (KeyError, ValueError, TypeError, ArithmeticError):
                pass
        projected.append(item)
    return projected
