"""Review handoff for retained bar experiments; no signatures or admission."""
from dataclasses import asdict
import hashlib
from pathlib import Path

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.research.trial_ledger import TrialLedger
from tools.gold_bar_experiment import verify, bound_archive, _bytes
from tools.gold_cost_review import _read


def review_candidates(report):
    rows = [dict(candidate=row['candidate'], outcome=row['outcome']) for row in report['trials']]
    if 'portfolio' in report:
        sim = report['portfolio']['simulation']
        pending = any(o['status'] in ('OPEN', 'PARTIALLY_FILLED') for o in sim['orders'])
        outcome = ('OPEN_AT_END' if sim['ending_state']['positions'] or pending else
                   'FLAT_TERMINAL' if any(o['filled_at'] is not None for o in sim['orders']) else
                   'NO_FILL' if sim['orders'] else 'NO_NET_ORDER')
        rows.append(dict(candidate='net_portfolio', outcome=outcome))
    return rows


def retained_request(directory):
    """Validate retained producer claims; this is not fresh recomputation."""
    from services.execution.backtest_authority import exact_identity_hash
    root = Path(directory).absolute()
    original = _read(root, 'independent-request.json')
    request = decode(original)
    manifest, bodies, _ = bound_archive(root)
    manifest_raw = _read(root, 'manifest.json')
    if decode(manifest_raw) != manifest:
        raise ValueError('GOLD_BAR_REVIEW_SOURCE_CHANGED')
    report = decode(bodies['report'])
    files = {'manifest.json': manifest_raw, **{name + '.json': raw for name, raw in bodies.items()}}
    report_hash = exact_identity_hash({k: v for k, v in report.items() if k != 'result_hash'},
                                      name='gold_bar_public_comparison')
    if (report.get('schema') != 'GOLD_BAR_PUBLIC_COMPARISON_V1'
            or report.get('result_hash') != report_hash
            or any(report.get(k) is not False for k in ('authoritative', 'economic_eligible', 'submission_enabled'))
            or request.get('schema') != 'GOLD_BAR_INDEPENDENT_REVIEW_REQUEST_V1'
            or request.get('status') != 'INDEPENDENT_REVIEW_REQUIRED'
            or request.get('local_reproduction_status') != 'RECOMPUTED_NOT_INDEPENDENTLY_APPROVED'
            or any(request.get(k) is not False for k in ('authoritative', 'economic_eligible', 'submission_enabled'))
            or request.get('result_hash') != report_hash
            or request.get('release_identity') != manifest['source']
            or request.get('trial_ids') != manifest['trial_ids']
            or request.get('files') != {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
            or request.get('candidates') != review_candidates(report)):
        raise ValueError('GOLD_BAR_REVIEW_ARCHIVE_BINDING')
    with TrialLedger(str(root / 'trials.db'), create=False, read_only=True) as ledger:
        ledger.verify()
        histories = {trial: [asdict(e) for e in ledger.load(trial).events] for trial in manifest['trial_ids']}
    if request.get('trial_history') != histories:
        raise ValueError('GOLD_BAR_REVIEW_TRIAL_HISTORY')
    if (_read(root, 'independent-request.json') != original
            or any(_read(root, name) != raw for name, raw in files.items())):
        raise ValueError('GOLD_BAR_REVIEW_SOURCE_CHANGED')
    return request


def review_request(directory):
    root = Path(directory).absolute()
    names = ('input.json', 'report.json', 'manifest.json')
    raw = {name: _read(root, name) for name in names}
    review = verify(root)
    manifest = decode(raw['manifest.json'])
    report = decode(raw['report.json'])
    histories = {}
    with TrialLedger(str(root / 'trials.db'), create=False, read_only=True) as ledger:
        ledger.verify()
        for trial_id in manifest['trial_ids']:
            histories[trial_id] = [asdict(event) for event in ledger.load(trial_id).events]
    if any(_read(root, name) != body for name, body in raw.items()):
        raise ValueError('GOLD_BAR_REVIEW_SOURCE_CHANGED')
    request = dict(schema='GOLD_BAR_INDEPENDENT_REVIEW_REQUEST_V1',
        status='INDEPENDENT_REVIEW_REQUIRED', local_reproduction_status=review['status'],
        result_hash=review['result_hash'], release_identity=manifest['source'],
        trial_ids=manifest['trial_ids'], trial_history=histories,
        files={name: hashlib.sha256(body).hexdigest() for name, body in raw.items()},
        candidates=review_candidates(report),
        requested_checks=['SOURCE_AUTHENTICATION', 'ARCHIVED_SOURCE_REPRODUCTION',
            'CAUSAL_TIMES', 'ACCOUNT_COST_AND_VENUE_RULES', 'LOCKED_OOS', 'GLOBAL_TRIAL_HISTORY'],
        limitations=['SAME_IMPLEMENTATION_RECOMPUTATION_NOT_INDEPENDENT_VALIDATION',
            'FOUR_LOCAL_TRIALS_NOT_GLOBAL_HISTORY' if 'portfolio' in report else 'THREE_LOCAL_TRIALS_NOT_GLOBAL_HISTORY', 'SOURCE_BYTES_NOT_IN_THIS_ARCHIVE',
            *report['limitations']],
        authoritative=False, economic_eligible=False, submission_enabled=False)
    body = _bytes(request)
    target = root / 'independent-request.json'
    if target.exists() or target.is_symlink():
        if _read(root, target.name) != body:
            raise ValueError('GOLD_BAR_REVIEW_REQUEST_CHANGED')
    else:
        with target.open('xb') as handle:
            handle.write(body)
    return request
