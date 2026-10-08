"""First Scope exact decisions must retain the original BTC issuer prefix.

The cold fixture performs the original setup, issue/recovery, partition and
Scope installation. Public account/market transport, accepted source and host
identities are isolated. This test runs native bar observation, exact candidate,
task/build/session/risk and issuer signatures; it does not claim the interactive
exact-policy installer or a field order has run.
"""
from contextlib import closing
from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tools.gold_bar_fresh_inputs import FreshEpisode
from tools.gold_bar_episode_tail import EpisodeTail
from tests.live.test_gold_first_scope_delivery import (
    first_scope_archive as first_scope_archive,
    cold_scope_local as cold_scope_local,
    installed_first_scope as installed_first_scope,
    native_scope as native_scope,
)


def native_exact_case(installed, native, tmp_path, monkeypatch):
    """Create one real future close without initializing a future-budget group."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from services.validation.demo_submission import _decode
    from tests.research.test_gold_bar_dataset import STEP
    from tests.live.test_demo_market_partition_runtime import shift
    from tools import gold_bar_observe as observer, gold_strategy_approval_request as approval
    from tools import binance_demo_market_inputs as capture, demo_operator_setup as setup
    from tools.gold_bar_demo_cycle import prepare
    from tools.gold_strategy_worker import original_pilot_window
    from tools.gold_strategy_request import task_from_draft
    from live.exchange import demo_network
    from live.execution import demo_request_inputs as builder
    from live.execution.demo_execution_session import ExecutionSession
    from quantos.contracts.killswitch import KillSwitchState

    local = installed['local']; root = local['root']; scope = installed['scope']
    registry = installed['registry']; runtime = deepcopy(local['parts']['runtime'])
    source = scope['executor_release_commit']; stores = local['value']['stores']
    warmup_end = local['value']['scope_warmup_end_ms']
    at = warmup_end + STEP + 20
    clock = local['clock']
    assert 0 < at-clock[0] <= runtime['seconds']*1000
    message = json.dumps(dict(stream='xauusdt@kline_15m', data=dict(e='kline', E=at, s='XAUUSDT',
        k=dict(t=warmup_end, T=warmup_end+STEP-1, s='XAUUSDT', i='15m', x=True,
            o='100', h='110', l='99', c='109'))))

    class Socket:
        consumed = False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def recv(self):
            assert not self.consumed
            self.consumed = True
            clock[0] = at
            return message

    monkeypatch.setattr(observer, 'connect', lambda *a, **kw: Socket())
    monkeypatch.setattr(observer.time, 'time_ns', lambda: clock[0]*1000000)
    monkeypatch.setattr(demo_network, 'proxy_url', lambda: None)
    monkeypatch.setattr(demo_network, 'tls_context', lambda: object())
    old_umask = os.umask(0o077)
    try:
        episode = FreshEpisode(root, runtime, source_commit=source, uid=os.getuid())
    finally:
        os.umask(old_umask)
    clock[0] = at + 1
    tail = EpisodeTail(root, bundle_sha256=_digest(runtime), engine=episode.engine,
        entry_decision_ms=episode.entry_decision_ms)
    engine, continuation, _ = tail.snapshot()
    original_origin = local['parts']['origin']
    origin = shift(original_origin, clock[0]-original_origin['completed_at_ms']-1)
    decision = prepare(engine=engine, entry_decision_ms=episode.entry_decision_ms,
        origin=origin, **stores, now_ms=clock[0])
    assert decision['status'] == 'DECISION_REQUIRES_APPROVAL', decision
    decision.update(source_commit=source, bundle_sha256=_digest(runtime),
        account_ref_sha256=scope['account_ref_sha256'], origin_sha256=_digest(origin),
        bar_continuation_sha256=continuation)
    result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
        source_commit=source, bundle_sha256=_digest(runtime), decision_sha256=_digest(decision),
        decision=decision, submission_enabled=False, economic_authority=False,
        original_pilot_window=original_pilot_window(root, decision=decision,
            source_commit=source, account=scope['account_ref_sha256']))
    request = approval.prepare(result, runtime=runtime, policy=scope, registry=registry, now_ms=clock[0])
    candidate = approval.policy_candidate(request, result=result, runtime=runtime,
        policy=scope, registry=registry, now_ms=clock[0])
    policy = candidate['policy_candidate']
    assert policy['bindings']['execution_constraints_sha256'] == _digest(decision['execution_constraints'])
    assert policy['budget_group'] == scope['budget_group']
    profile = json.loads((local['cfg']/'profile.json').read_bytes())
    dispatch = approval.scope_dispatch_configuration(scope, now_ms=clock[0])
    kill = KillSwitchState.model_validate_json(json.dumps(dispatch['kill_switch']), strict=True)
    task = task_from_draft(decision['draft'], profile=profile, issuer_policy=policy,
        kill_state=kill, source_commit=source, now_ms=clock[0])

    end = clock[0]//60000*60000-1
    bars = []
    for index in range(61):
        start = end+1-(61-index)*60000; price = str(4000+index)
        bars.append([start, price, price, price, price, '2', start+59999, '8000', 3, '1', '4000', '0'])
    def get(kind, **kwargs):
        if kind.startswith('clock'):
            value = dict(serverTime=clock[0]+(6 if kind == 'clock_after' else 0))
        elif kind == 'ticker':
            value = dict(symbol='XAUUSDT', openTime=clock[0]-86400000,
                closeTime=clock[0], volume='100000', quoteVolume='400000000')
        else: value = bars
        return json.dumps(value).encode()
    ticks = iter(range(clock[0], clock[0]+100))
    market = capture.collect(symbol='XAUUSDT', get=get, clock_ms=lambda: next(ticks))
    assert market['status'] == 'CAPTURED'
    clock[0] += 10
    built = builder.build(orders=task['orders'], origin=origin, market_inputs=market,
        profile=profile, issuer_policy=policy, kill_state=kill, **stores, now_ms=clock[0],
        execution_constraints=task['execution_constraints'])
    session = ExecutionSession(**stores, evidence_directory=tmp_path/'exact-proposal')
    try:
        handoff = session.propose(setup.canonical(built['request']), now_ms=clock[0])
    finally: session.close()
    keydoc = json.loads((local['cfg']/'signing-key.json').read_bytes())
    key = Ed25519PrivateKey.from_private_bytes(_decode(keydoc['private_key_base64url'], 32))
    prefix = installed['history']['issuer_prefix']
    expected = ['RESERVED', 'SIGNED'] + (['RESERVED'] if installed['history']['unsigned_handoff'] is not None else [])
    assert [r['kind'] for r in prefix] == expected
    return SimpleNamespace(installed=installed, scope=scope, registry=registry, policy=policy,
        candidate=candidate, result=result, task=task, handoff=handoff, key=key, now_ms=clock[0])


@pytest.fixture
def exact_case(installed_first_scope, native_scope, tmp_path, monkeypatch):
    return native_exact_case(installed_first_scope, native_scope, tmp_path, monkeypatch)


def issue(f, store):
    from live.execution import demo_issuer
    from services.validation import demo_submission
    grant = demo_issuer.issue_submission(f.handoff, policy=f.policy, registry=f.registry,
        key=f.key, store=store, now_ms=f.now_ms, clock_ms=lambda: f.now_ms)
    checked = demo_submission.verify(grant['envelope'], registry=f.registry,
        expected_context=f.handoff['proposal']['context'], orders=f.task['orders'], now_ms=f.now_ms)
    assert checked['signature_verified'] is True
    return grant


@pytest.mark.parametrize('cold_scope_local', [False, True], indirect=True,
    ids=['signed-history', 'signed-and-unsigned-history'])
def test_native_first_scope_exact_preserves_full_original_issuer(exact_case):
    from live.execution.demo_issuer_store import IssuerStore
    from live.execution.demo_gold_first_scope_history import verify_prefix
    f = exact_case; history = f.installed['history']; prefix = history['issuer_prefix']
    with closing(IssuerStore(history['issuer_path'])) as store:
        assert store.snapshot() == prefix
        issue(f, store)
        assert store.snapshot()[:-2] == prefix
        verify_prefix(f.scope['scope_enrollment']['original_issuer_history'], store.snapshot())
        assert store.snapshot()[-2]['payload']['policy'] == f.policy


def test_native_first_scope_exact_rejects_empty_replacement_issuer(exact_case):
    from live.execution.demo_issuer_store import IssuerStore
    from live.execution.demo_gold_first_scope_history import verify_prefix
    f = exact_case; path = f.installed['history']['issuer_path']
    # Preserve the entire original for diagnosis. Replace only this isolated
    # fixture path with a genuinely initialized, structurally valid empty DB.
    replacement = path.with_name('isolated-empty-issuer.sqlite3')
    with closing(IssuerStore(replacement, create=True)) as empty:
        assert empty.snapshot() == []
    backup = path.with_name('retained-original-issuer.sqlite3')
    os.replace(path, backup)
    os.replace(replacement, path)
    with closing(IssuerStore(path)) as store:
        with pytest.raises(ValueError, match='FIRST_SCOPE_ISSUER_PREFIX_MISSING'):
            verify_prefix(f.scope['scope_enrollment']['original_issuer_history'], store.snapshot())
        # Native signature success here is the regression: unchanged signed
        # Scope requires a prefix that the ordinary exact issuer never checks.
        with pytest.raises(ValueError, match='FIRST_SCOPE_ISSUER_PREFIX'):
            issue(f, store)


@pytest.mark.parametrize('cold_scope_local', [True], indirect=True)
def test_native_first_scope_exact_rejects_lost_original_unsigned_reservation(exact_case):
    from live.execution.demo_issuer_store import IssuerStore
    from live.execution.demo_gold_first_scope_history import verify_prefix
    f = exact_case; history = f.installed['history']; prefix = history['issuer_prefix']
    assert len(prefix) == 3 and prefix[-1]['kind'] == 'RESERVED'
    replacement = history['issuer_path'].with_name('isolated-before-unsigned.sqlite3')
    # Rebuild the actual earlier signed prefix through native reserve/signed.
    # No SQL DELETE, trigger removal, signature substitute or zero consumption.
    with closing(IssuerStore(replacement, create=True)) as earlier:
        reserved, signed = prefix[:2]; payload = reserved['payload']
        earlier.reserve(payload['plan'], policy=payload['policy'], registry=payload['registry'])
        earlier.signed(signed['proposal_sha256'], signed['payload']['envelope'], now_ms=signed['at_ms'])
        assert earlier.snapshot() == prefix[:2]
    backup = history['issuer_path'].with_name('retained-signed-and-unsigned.sqlite3')
    os.replace(history['issuer_path'], backup)
    os.replace(replacement, history['issuer_path'])
    with closing(IssuerStore(history['issuer_path'])) as earlier:
        with pytest.raises(ValueError, match='FIRST_SCOPE_ISSUER_PREFIX_MISSING'):
            verify_prefix(f.scope['scope_enrollment']['original_issuer_history'], earlier.snapshot())
        with pytest.raises(ValueError, match='FIRST_SCOPE_ISSUER_PREFIX'):
            issue(f, earlier)


def test_first_scope_prefix_is_rechecked_after_reservation_before_crypto(exact_case, monkeypatch):
    from live.execution import demo_issuer
    from live.execution.demo_issuer_store import IssuerStore
    f = exact_case; history = f.installed['history']; prefix = history['issuer_prefix']
    with closing(IssuerStore(history['issuer_path'])) as original:
        with closing(IssuerStore(history['issuer_path'].with_name('isolated-post-reserve.sqlite3'), create=True)) as empty:
            original_connection = original.db
            native_reserve = original.reserve
            def reserve_then_reopen(*args, **kwargs):
                native_reserve(*args, **kwargs)
                assert original.snapshot()[:-1] == prefix
                # Fault injection only: replace the read connection after the
                # original durable reservation. Both real DBs stay untouched.
                original.db = empty.db
            monkeypatch.setattr(original, 'reserve', reserve_then_reopen)
            signatures = []
            native_sign = demo_issuer._sign
            def signing(key, body):
                signatures.append(True)
                return native_sign(key, body)
            monkeypatch.setattr(demo_issuer, '_sign', signing)
            try:
                with pytest.raises(ValueError):
                    issue(f, original)
            finally:
                original.db = original_connection
            rows = original.snapshot()
            assert rows[:-1] == prefix and rows[-1]['kind'] == 'RESERVED'
            assert signatures == [], 'cryptographic grant escaped before checking original prefix'


def test_native_first_scope_signing_failure_keeps_budget_and_claim_on_restart(exact_case, monkeypatch):
    from live.execution import demo_issuer
    from live.execution.demo_issuer_store import IssuerStore
    from live.execution.demo_issuer_group_continuity import reduce_groups
    from tests.live.test_gold_first_scope_original_history import issuer_claims
    f = exact_case; history = f.installed['history']; prefix = history['issuer_prefix']
    def interrupted(*args):
        raise OSError('isolated-signing-interruption')
    monkeypatch.setattr(demo_issuer, '_sign', interrupted)
    with closing(IssuerStore(history['issuer_path'])) as original:
        with pytest.raises(OSError, match='isolated-signing-interruption'):
            issue(f, original)
        rows = original.snapshot(); claims = issuer_claims(original)
        assert rows[:-1] == prefix and rows[-1]['kind'] == 'RESERVED'
    with closing(IssuerStore(history['issuer_path'])) as reopened:
        assert reopened.snapshot() == rows and issuer_claims(reopened) == claims
        used = reduce_groups(rows)[f.policy['budget_group']['group_id']]['submission']
        assert used[0] == 1 and used[1] == len(f.task['orders']) and used[2] > 0
        monkeypatch.setattr(demo_issuer, '_sign', lambda *args: pytest.fail('unsigned proposal was signed twice'))
        with pytest.raises(ValueError, match='ALREADY_RESERVED'):
            issue(f, reopened)
        assert reopened.snapshot() == rows and issuer_claims(reopened) == claims


def test_native_exact_signature_cannot_reopen_without_its_original_prefix(exact_case):
    from live.execution.demo_issuer_store import IssuerStore
    from tests.live.test_gold_first_scope_original_history import issuer_claims
    f = exact_case; history = f.installed['history']
    with closing(IssuerStore(history['issuer_path'])) as original:
        issue(f, original)
        complete = original.snapshot()
        tail = complete[-2:]
        claims = [row for row in issuer_claims(original) if row[-1] == f.handoff['proposal_sha256']]
        assert [row['kind'] for row in tail] == ['RESERVED', 'SIGNED'] and len(claims) == 1
    damaged = history['issuer_path'].with_name('isolated-exact-only-history.sqlite3')
    # An explicitly corrupted recovery image is assembled in a NEW database.
    # Only raw append/storage is used here, to reach the independent replay
    # boundary. The original payloads, Ed25519 signature and claims are kept;
    # no production history, DELETE trigger or native validator is changed.
    with closing(IssuerStore(damaged, create=True)) as copy:
        inserted = []
        for row in tail:
            copy._append(row['kind'], row['proposal_sha256'], row['payload'], row['at_ms'], inserted)
            record = dict(seq=len(inserted)+1, kind=row['kind'], proposal_sha256=row['proposal_sha256'],
                at_ms=row['at_ms'], payload=row['payload'], prev_hash=inserted[-1]['record_hash'] if inserted else '')
            inserted.append(record | {'record_hash': _digest(record)})
        copy.db.executemany('INSERT INTO issuer_order_claims VALUES(?,?,?,?)', claims)
        copy.db.commit()
    with pytest.raises(ValueError, match='FIRST_SCOPE_ISSUER_PREFIX'):
        IssuerStore(damaged, read_only=True)
    with closing(IssuerStore(history['issuer_path'], read_only=True)) as unchanged:
        assert unchanged.snapshot() == complete
