"""Prepare a deterministic independent-review package and verify retained receipts."""
from pathlib import Path
import hashlib
import json
import time
from dataclasses import asdict
from services.research.ai_factory_store import FactoryStore
from services.research.trial_ledger import TrialLedger
from quantos.runtime_paths import runtime_state_root
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.governance.artifact_receipt_auth import SignedArtifactVerificationReceipt,ReceiptAuthorityKey
from services.validation.ai_factory_verification import verify_research_receipt
from tools.btc_factory_research import verified,seal
from tools.cache_io import atomic_write_json


def run_verification(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:raise ValueError('invalid review attempt')
    dest=runtime_state_root()/'data/cache/ai_factory'/str(params['attempt'])
    files={}
    state=FactoryStore.snapshot(dest.parent/'factory.db',params['attempt'])
    if state['selected']['state']!='COMPLETED':raise ValueError('completed research required')
    with TrialLedger(str(dest.parent/'trials.db')) as ledger:
        ledger.verify()
        history=[{'receipt':row,'events':[asdict(e) for e in ledger.load(row['trial_id']).events]} for row in state['trials']]
    history_path=dest/'independent-trial-history.json'
    history_body=json.dumps(history,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if history_path.exists() and history_path.read_bytes()!=history_body:raise ValueError('trial history changed')
    if not history_path.exists():history_path.write_bytes(history_body)
    files[history_path.name]=hashlib.sha256(history_body).hexdigest()
    for name in ('plan.json','model.json','result.json'):
        verified(dest/name)
        files[name]=hashlib.sha256((dest/name).read_bytes()).hexdigest()
    package={'schema':'QUANTOS_INDEPENDENT_RESEARCH_PACKAGE_V1','attempt':params['attempt'],'files':files,
             'requested_checks':['SOURCE_INPUT_HASHES','CAUSAL_LABEL_TIMES','COMPLETE_TRIAL_HISTORY','CANONICAL_LEDGER_RECONCILIATION',
                                 'FROZEN_MODEL_REPRODUCIBILITY','HISTORICAL_RULE_LIMITATIONS','OOS_SELECTION_LEAKAGE'],
             'economic_eligible':False,'authoritative':False}
    raw=json.dumps(package,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    artifact_hash=hashlib.sha256(raw).hexdigest()
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
