"""One-process cancellation grant handoff using only original protected IDs."""
from pathlib import Path
import re
import threading

from live.execution import demo_cancellation
from live.execution.demo_execution_session import SessionRejected
from live.execution.demo_pre_submit import _digest, _retain
from live.execution.demo_resident_recovery import discover
from services.governance.json_integrity import load_json_without_duplicate_keys


class CancellationSession:
    def __init__(self,*,dispatch,recovery,oms,cancel_store,evidence_directory):
        self._stores=dict(dispatch=dispatch,recovery=recovery,oms=oms,cancel_store=cancel_store)
        self._directory=Path(evidence_directory)
        self._lock=threading.Lock();self._pending=None;self._closed=False

    def propose(self,raw,*,now_ms):
        with self._lock:
            if self._closed or self._pending is not None:raise SessionRejected('SESSION_CLOSED_OR_PROPOSAL_PENDING')
            try:
                if type(raw) is not bytes or not 0<len(raw)<=16384 or type(now_ms) is not int or now_ms<=0:
                    raise ValueError('CANCEL_SESSION_INPUT_BUDGET')
                data=load_json_without_duplicate_keys(raw)
                if (type(data) is not dict or set(data)!=set('schema reservation_sha256 client_order_ids'.split()) or
                    data['schema']!='DEMO_CANCELLATION_SESSION_INPUT_V1' or
                    type(data['reservation_sha256']) is not str or re.fullmatch('[a-f0-9]{64}',data['reservation_sha256']) is None):
                    raise ValueError('CANCEL_SESSION_INPUT_SHAPE')
                ids=data['client_order_ids']
                if (type(ids) is not list or not 1<=len(ids)<=20 or any(type(v) is not str or
                    re.fullmatch('QD[A-Za-z0-9_-]{8,34}',v) is None for v in ids) or len(set(ids))!=len(ids)):
                    raise ValueError('CANCEL_SESSION_EXPLICIT_IDS')
                originals=discover(**{k:self._stores[k] for k in ('dispatch','recovery','oms')},evidence_directory=self._directory)
                own=[r for r in originals['pending'] if r['reservation_sha256']==data['reservation_sha256']]
                if len(own)!=1 or own[0]['prepared'] is None:raise ValueError('CANCEL_SESSION_ORIGINAL_UNAVAILABLE')
                prepared=own[0]['prepared']
                proposal=demo_cancellation.proposal(prepared,**self._stores,client_order_ids=ids)
                identity=_digest(proposal)
                path=_retain(self._directory/'cancellation-proposals',proposal)
                self._pending=(identity,prepared,ids)
                return {'schema':'DEMO_CANCELLATION_SESSION_PROPOSAL_V1','proposal_sha256':identity,
                    'proposal_path':str(path),'proposal':proposal,'status':'WAITING_INDEPENDENT_GRANT','submission_enabled':False}
            except Exception:raise SessionRejected('CANCEL_SESSION_PROPOSAL_INPUTS_REJECTED') from None

    def submit(self,proposal_sha256,envelope):
        with self._lock:
            if self._closed or self._pending is None:raise SessionRejected('SESSION_NO_PENDING_PROPOSAL')
            identity,prepared,ids=self._pending
            self._pending=None;self._closed=True
            if proposal_sha256!=identity or type(envelope) is not bytes or not 0<len(envelope)<=65536:
                raise SessionRejected('SESSION_GRANT_INPUT_REJECTED')
            try:
                result=demo_cancellation.execute(prepared,envelope,**self._stores,client_order_ids=ids)
                return {'schema':'DEMO_CANCELLATION_SESSION_RESULT_V1','cancellation':result,
                    'status':'ORIGINAL_ID_QUERY_RECOVERY_REQUIRED','cash_reconciled':False,'economic_authority':False}
            except Exception:raise SessionRejected('CANCEL_SESSION_STOPPED_INSPECT_ORIGINAL_IDS') from None

    def close(self):
        with self._lock:self._pending=None;self._closed=True
