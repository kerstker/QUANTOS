"""One-process proposal/grant/send handoff over the existing protected executor.

No credential, transport, issuer, synthetic approval or startup replay lives
here. Pending inputs stay private to this object and cannot be imported after a
restart. Any possible send keeps the original durable reservation/claim/query
path; a failed or repeated command never creates a replacement order.
"""
from pathlib import Path
import threading

from live.execution import demo_pre_submit, demo_prepared_execution
from live.execution.demo_batch_risk import _digest
from live.execution.demo_session_inputs import decode


class SessionRejected(ValueError):
    pass


class ExecutionSession:
    def __init__(self, *, dispatch, recovery, oms, evidence_directory):
        self._stores = dict(dispatch=dispatch, recovery=recovery, oms=oms)
        self._directory = Path(evidence_directory)
        self._lock = threading.Lock()
        self._pending = None
        self._closed = False

    def propose(self, raw, *, now_ms):
        with self._lock:
            if self._closed or self._pending is not None:
                raise SessionRejected('SESSION_CLOSED_OR_PROPOSAL_PENDING')
            try:
                values = decode(raw, now_ms=now_ms)
                proposal = demo_pre_submit.proposal(**values, **self._stores)
                identity = _digest(proposal)
                path = demo_pre_submit._retain(self._directory, proposal)
                self._pending = (identity, values)
                return {'schema': 'DEMO_EXECUTION_SESSION_PROPOSAL_V1',
                    'proposal_sha256': identity, 'proposal_path': str(path), 'proposal': proposal,
                    'status': 'WAITING_INDEPENDENT_GRANT', 'submission_enabled': False}
            except Exception:
                raise SessionRejected('SESSION_PROPOSAL_INPUTS_REJECTED') from None

    def submit(self, proposal_sha256, envelope):
        with self._lock:
            if self._closed or self._pending is None:
                raise SessionRejected('SESSION_NO_PENDING_PROPOSAL')
            identity, values = self._pending
            # Consume this in-memory handoff before any verification or durable
            # side effect. Invalid signatures do not leave an automatic retry.
            self._pending = None
            self._closed = True
            if proposal_sha256 != identity or type(envelope) is not bytes or not 0 < len(envelope) <= 65536:
                raise SessionRejected('SESSION_GRANT_INPUT_REJECTED')
            try:
                # Same original now/input values reproduce the exact signed
                # proposal; execute checks current time and stop state anew.
                prepared = demo_pre_submit.prepare(envelope, evidence_directory=self._directory,
                    verification_now_ms=demo_prepared_execution.now_ms(), **values, **self._stores)
                execution = demo_prepared_execution.execute(prepared, **self._stores,
                    **{key: values[key] for key in ('risk_inputs', 'mandate', 'attestations')})
                return {'schema': 'DEMO_EXECUTION_SESSION_RESULT_V1', 'prepared': prepared,
                    'execution': execution, 'status': 'QUERY_RECOVERY_REQUIRED',
                    'cash_reconciled': False, 'economic_authority': False}
            except Exception:
                raise SessionRejected('SESSION_STOPPED_INSPECT_ORIGINAL_RESERVATIONS') from None

    def close(self):
        with self._lock:
            self._pending = None
            self._closed = True
