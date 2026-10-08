"""Prepare a deterministic independent-review package and verify retained receipts."""
from pathlib import Path
import hashlib
import json
import time
from dataclasses import asdict
from services.research.ai_factory_store import FactoryStore,digest
from services.research.trial_ledger import TrialLedger
from quantos.runtime_paths import runtime_state_root
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.governance.artifact_receipt_auth import SignedArtifactVerificationReceipt,ReceiptAuthorityKey
from services.governance.ai_factory_verification import verify_research_receipt
from tools.btc_factory_research import seal,verified
from tools.cache_io import atomic_write_json


def _bound_material(dest, state):
    """Bind the exact packaged bytes to the completed session before publishing."""
    documents, files = {}, {}
    for name in ('plan.json', 'model.json', 'result.json'):
        path = dest / name
        if path.is_symlink():
            raise ValueError('research package binding: unsafe artifact')
        raw = path.read_bytes()
        document = load_json_without_duplicate_keys(raw)
        if (not isinstance(document, dict) or document.get('result_hash') !=
                seal({k: v for k, v in document.items() if k != 'result_hash'})['result_hash']):
            raise ValueError('research package binding: artifact hash')
        documents[name] = document
        files[name] = hashlib.sha256(raw).hexdigest()
    try:
        session = state['selected']
        bound = documents['plan.json']['identity']
        model = documents['model.json']
        result = documents['result.json']
        trials = state['trials']
        config = session['config']
        valid = (
            bound['config'] == config
            and model['identity_hash'] == digest(bound)
            and result['model_hash'] == model['result_hash']
            and result['selected'] == model['spec']
            and session['result'] == {k: v for k, v in result.items() if k != 'result_hash'}
            and len(trials) == config['budget']
            and all(row['ordinal'] == index and row['status'] in ('PASSED', 'FAILED')
                    and row['trial_id'] == digest({'bound': bound, 'ordinal': index})
                    for index, row in enumerate(trials))
        )
        passed = [row for row in trials if row['status'] == 'PASSED']
        if not valid or not passed:
            raise ValueError('research package binding: session or trial history')
        best = max(passed, key=lambda row: (row['metrics']['score'], -row['ordinal']))
        if best['spec'] != model['spec'] or best['metrics'] != result['inner']:
            raise ValueError('research package binding: selected trial')
    except (KeyError, TypeError) as exc:
        raise ValueError('research package binding: missing identity') from exc
    return files


def _review_material(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:raise ValueError('invalid review attempt')
    dest=runtime_state_root()/'data/cache/ai_factory'/str(params['attempt'])
    state=FactoryStore.snapshot(dest.parent/'factory.db',params['attempt'])
    if not state['selected'] or state['selected']['state']!='COMPLETED':raise ValueError('completed research required')
    files = _bound_material(dest, state)
    with TrialLedger(str(dest.parent/'trials.db'), create=False, read_only=True) as ledger:
        ledger.verify()
        history = []
        for row in state['trials']:
            trial = ledger.load(row['trial_id'])
            if trial.status != row['status']:
                raise ValueError('research package binding: trial ledger status')
            history.append({'receipt': row, 'events': [asdict(e) for e in trial.events]})
    history_path=dest/'independent-trial-history.json'
    history_body=json.dumps(history,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    files[history_path.name]=hashlib.sha256(history_body).hexdigest()
    package={'schema':'QUANTOS_INDEPENDENT_RESEARCH_PACKAGE_V1','attempt':params['attempt'],'files':files,
             'requested_checks':['SOURCE_INPUT_HASHES','CAUSAL_LABEL_TIMES','COMPLETE_TRIAL_HISTORY','CANONICAL_LEDGER_RECONCILIATION',
                                 'FROZEN_MODEL_REPRODUCIBILITY','HISTORICAL_RULE_LIMITATIONS','OOS_SELECTION_LEAKAGE'],
             'economic_eligible':False,'authoritative':False}
    raw=json.dumps(package,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    artifact_hash=hashlib.sha256(raw).hexdigest()
    return dest, history_body, package, raw, artifact_hash


def verify_completed_review(params):
    """Recheck retained material only; this is not renewed signature admission."""
    dest, history_body, package, raw, artifact_hash = _review_material(params)
    for name, expected in (('independent-trial-history.json', history_body),
                           ('independent-request.json', raw)):
        path = dest / name
        if path.is_symlink() or path.read_bytes() != expected:
            raise ValueError('completed review material changed')
    value = verified(dest / 'verification.json')
    if (value.get('schema') != 'QUANTOS_AI_FACTORY_VERIFICATION_V1'
            or value.get('attempt') != params['attempt']
            or value.get('request') != package
            or value.get('artifact_sha256') != artifact_hash
            or value.get('authoritative') is not False
            or value.get('economic_eligible') is not False):
        raise ValueError('completed review binding changed')
    return value['result_hash']


def run_verification(params):
    dest, history_body, package, raw, artifact_hash = _review_material(params)
    history_path = dest / 'independent-trial-history.json'
    if history_path.exists() and history_path.read_bytes()!=history_body:raise ValueError('trial history changed')
    if not history_path.exists():history_path.write_bytes(history_body)
    path=dest/'independent-request.json'
    if path.exists() and path.read_bytes()!=raw:raise ValueError('independent request identity changed')
    if not path.exists():path.write_bytes(raw)
    registry_path=Path(__file__).resolve().parents[1]/'config/artifact_receipt_authority_registry.json'
    registry=load_json_without_duplicate_keys(registry_path.read_bytes())
    envelope_path=dest/'independent-envelope.json';envelope=None;key=None
    if envelope_path.exists():
        if envelope_path.is_symlink() or envelope_path.stat().st_size>65536:raise ValueError('unsafe independent envelope')
        envelope=SignedArtifactVerificationReceipt.model_validate(load_json_without_duplicate_keys(envelope_path.read_bytes()))
        for candidate in registry['keys'].values():
            parsed=ReceiptAuthorityKey.model_validate(candidate)
            if parsed.authority_id==envelope.authority_id and parsed.key_id==envelope.key_id:key=parsed
    try:verification=verify_research_receipt(artifact_hash,envelope,key,now=time.time_ns()//1000000)
    except ValueError as exc:verification={'status':'REJECTED','reason':str(exc),'economic_eligible':False,'authoritative':False}
    result=seal({'schema':'QUANTOS_AI_FACTORY_VERIFICATION_V1','attempt':params['attempt'],'artifact_sha256':artifact_hash,
                 'request':package,'verification':verification,'authoritative':False,'economic_eligible':False})
    atomic_write_json(str(dest/'verification.json'),result);return result
