"""A waiting proposal and its grant must use one protected executor boot."""
from dataclasses import asdict
import json

import pytest

from live.execution import demo_execution_session as session
from live.execution import demo_prepared_execution as executor
from live.execution.demo_batch_risk import _json_value
from live.exchange import binance_demo_exact as transport
from tests.live.test_demo_pre_submit import setup, signed, stores, NOW
from services.validation.demo_submission import signing_payload
from tests.live.test_demo_submission_verification import encoded


def request(values):
    risk = values['risk_inputs']
    return json.dumps({'schema': 'DEMO_EXECUTION_SESSION_INPUT_V1',
        'orders': values['orders'], 'origin': values['origin'],
        'risk': {**{k: risk[k] for k in ('account', 'market')},
            'native_limits': _json_value(asdict(risk['native_limits'])),
            'strategy': risk['strategy'].model_dump(mode='json'),
            'kill_switch': risk['kill_switch'].state.model_dump(mode='json'), 'plan_turnover': 0.0},
        'mandate': _json_value(asdict(values['mandate'])),
        'attestations': {k: asdict(v) for k, v in values['attestations'].items()}}).encode()


def fixture(tmp_path, monkeypatch):
    private, envelope, values = setup(tmp_path, monkeypatch)
    ds, rs, oms = stores(tmp_path)
    current = session.ExecutionSession(dispatch=ds, recovery=rs, oms=oms,
        evidence_directory=tmp_path/'evidence')
    monkeypatch.setattr(executor, 'now_ms', lambda: NOW+1)
    monkeypatch.setattr(executor, '_require_state_custody', lambda *a, **k: None)
    monkeypatch.setattr(transport, '_credentials', lambda ref: ('test-key', 'test-secret'))
    return current, (ds, rs, oms), private, envelope, values


def test_one_process_proposal_signed_grant_and_exact_once_send(tmp_path, monkeypatch):
    current, opened, private, envelope, values = fixture(tmp_path, monkeypatch)
    calls = []
    def http(*args):
        calls.append(1)
        return 200, json.dumps({'symbol': 'BTCUSDC', 'clientOrderId': values['orders'][0]['newClientOrderId'],
                               'orderId': 123}).encode()
    monkeypatch.setattr(transport, '_http_once', http)
    try:
        proposal = current.propose(request(values), now_ms=NOW)
        result = current.submit(proposal['proposal_sha256'], signed(private, envelope, proposal['proposal']))
        assert len(calls) == 1
        assert result['execution']['status'] == 'WIRE_ATTEMPTS_RECORDED'
        assert result['prepared']['context'] == proposal['proposal']['context']
        with pytest.raises(session.SessionRejected):
            current.submit(proposal['proposal_sha256'], signed(private, envelope, proposal['proposal']))
        assert len(calls) == 1
        assert opened[2].fills() == []
    finally:
        for store in opened: store.close()


def test_restarted_session_cannot_import_pending_proposal(tmp_path, monkeypatch):
    current, opened, private, envelope, values = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, '_credentials', lambda *_: pytest.fail('credentials reached'))
    try:
        proposal = current.propose(request(values), now_ms=NOW)
        restored = session.ExecutionSession(dispatch=opened[0], recovery=opened[1], oms=opened[2],
            evidence_directory=tmp_path/'evidence')
        with pytest.raises(session.SessionRejected):
            restored.submit(proposal['proposal_sha256'], signed(private, envelope, proposal['proposal']))
        assert opened[0].snapshot() == []
    finally:
        for store in opened: store.close()


def test_grant_issued_after_proposal_retains_decision_identity_and_uses_actual_reservation_time(tmp_path, monkeypatch):
    current, opened, private, envelope, values = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(executor, 'now_ms', lambda: NOW+10)
    monkeypatch.setattr(transport, '_http_once', lambda *_: (200, json.dumps({
        'symbol': 'BTCUSDC', 'clientOrderId': values['orders'][0]['newClientOrderId'], 'orderId': 123}).encode()))
    try:
        proposal = current.propose(request(values), now_ms=NOW)
        signed(private, envelope, proposal['proposal'])
        envelope['payload']['issued_at_ms'] = NOW+5
        envelope['signature_base64url'] = encoded(private.sign(signing_payload(envelope)))
        result = current.submit(proposal['proposal_sha256'], json.dumps(envelope).encode())
        assert result['prepared']['context'] == proposal['proposal']['context']
        assert opened[0].snapshot()[0]['payload']['reserved_at_ms'] == NOW+10
        assert opened[2].orders()[0]['submitted_at'] == NOW+10
    finally:
        for store in opened: store.close()


def test_expired_grant_rejected_before_reservation_at_actual_time(tmp_path, monkeypatch):
    current, opened, private, envelope, values = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, '_credentials', lambda *_: pytest.fail('credentials reached'))
    try:
        proposal = current.propose(request(values), now_ms=NOW)
        monkeypatch.setattr(executor, 'now_ms', lambda: NOW+30000)
        with pytest.raises(session.SessionRejected):
            current.submit(proposal['proposal_sha256'], signed(private, envelope, proposal['proposal']))
        assert opened[0].snapshot() == []
    finally:
        for store in opened: store.close()


def test_crash_after_send_claim_leaves_original_id_pending_without_session_replay(tmp_path, monkeypatch):
    current, opened, private, envelope, values = fixture(tmp_path, monkeypatch)
    calls = []
    def crash(*_):
        calls.append(1)
        raise SystemExit(99)
    monkeypatch.setattr(transport, '_http_once', crash)
    try:
        proposal = current.propose(request(values), now_ms=NOW)
        grant = signed(private, envelope, proposal['proposal'])
        with pytest.raises(SystemExit): current.submit(proposal['proposal_sha256'], grant)
        assert len(calls) == len(opened[0].wire_snapshot()) == 1
        assert len(opened[1].pending()) == 1
        with pytest.raises(session.SessionRejected): current.submit(proposal['proposal_sha256'], grant)
        restored = session.ExecutionSession(dispatch=opened[0], recovery=opened[1], oms=opened[2],
            evidence_directory=tmp_path/'evidence')
        with pytest.raises(session.SessionRejected): restored.propose(request(values), now_ms=NOW)
        assert len(calls) == 1
    finally:
        for store in opened: store.close()


@pytest.mark.parametrize('mode', ['closed', 'wrong_proposal', 'wrong_signature', 'second_proposal'])
def test_invalid_handoffs_do_not_send_or_replace_a_pending_proposal(tmp_path, monkeypatch, mode):
    current, opened, private, envelope, values = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, '_credentials', lambda *_: pytest.fail('credentials reached'))
    try:
        proposal = current.propose(request(values), now_ms=NOW)
        grant = signed(private, envelope, proposal['proposal'])
        if mode == 'closed': current.close()
        elif mode == 'wrong_proposal': proposal['proposal_sha256'] = '0'*64
        elif mode == 'wrong_signature': grant = grant.replace(envelope['signature_base64url'].encode(), encoded(b'\0'*64).encode())
        if mode == 'second_proposal':
            with pytest.raises(session.SessionRejected): current.propose(request(values), now_ms=NOW)
        else:
            with pytest.raises(session.SessionRejected): current.submit(proposal['proposal_sha256'], grant)
        assert opened[0].snapshot() == []
    finally:
        for store in opened: store.close()


@pytest.mark.parametrize('case', ['extra', 'missing_cap', 'boolean_cap', 'integer_rate', 'strategy_bool',
                                 'duplicate_symbol', 'duplicate_key', 'nan', 'oversize'])
def test_invalid_external_inputs_never_reserve_or_reach_credentials(tmp_path, monkeypatch, case):
    current, opened, _, _, values = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, '_credentials', lambda *_: pytest.fail('credentials reached'))
    raw = request(values); data = json.loads(raw)
    if case == 'extra': data['credential_path'] = '/not-allowed'
    elif case == 'missing_cap': del data['risk']['native_limits']['max_order_notional']
    elif case == 'boolean_cap': data['risk']['native_limits']['max_order_notional'] = True
    elif case == 'integer_rate': data['risk']['native_limits']['rate_window_ms'] = 60000.5
    elif case == 'strategy_bool': data['risk']['strategy']['approved'] = 'true'
    elif case == 'duplicate_symbol': data['risk']['native_limits']['allowed_instruments'] *= 2
    elif case == 'duplicate_key': raw = raw.replace(b'"plan_turnover": 0.0', b'"plan_turnover": 0.0, "plan_turnover": 0.1')
    elif case == 'nan': raw = raw.replace(b'"plan_turnover": 0.0', b'"plan_turnover": NaN')
    elif case == 'oversize': raw = b' ' * (16_000_000 + 1)
    if case not in ('duplicate_key', 'nan', 'oversize'): raw = json.dumps(data).encode()
    try:
        with pytest.raises(session.SessionRejected): current.propose(raw, now_ms=NOW)
        assert opened[0].snapshot() == []
    finally:
        for store in opened: store.close()
