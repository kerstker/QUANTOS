"""A matching stop-image digest cannot replace native history verification."""
from copy import deepcopy
import hashlib
import sqlite3

import pytest

from live.execution.demo_market_partition import prepare
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.killswitch import KillSwitchState
from services.risk.pre_trade.kill_switch_runtime import arm_and_record
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore
from tests.live.test_demo_market_partition import book as book
from tests.tools.test_demo_market_partition import inputs, worker


def corrupt_stop(root, damage, at):
    path = root / 'kill-switch.sqlite3'
    if damage == 'corrupt':
        path.write_bytes(b'isolated invalid sqlite image')
    elif damage == 'truncated':
        path.write_bytes(path.read_bytes()[:256])
    elif damage in ('empty_history', 'future'):
        replacement = root / 'isolated-replacement.sqlite3'
        store = KillSwitchStateStore(str(replacement), create=True)
        try:
            if damage == 'future':
                # An imported fixture state has a coherent future timestamp.
                store.bootstrap(KillSwitchState(reset_at=at + 1), recorded_at=at + 1)
        finally:
            store.close()
        path.write_bytes(replacement.read_bytes())
    elif damage == 'tripped':
        store = KillSwitchStateStore(str(path))
        try:
            arm_and_record(store, Actor(actor_id='isolated-system-sentinel', kind=ActorKind.SYSTEM),
                'partition fixture trip', at=at, incident_id='partition-fixture')
        finally:
            store.close()
    else:
        # Explicit fault injection in a fixture copy; no protected store is used.
        connection = sqlite3.connect(path)
        try:
            connection.execute('DROP TRIGGER kill_switch_transitions_no_update')
            column = 'record_hash' if damage == 'record_hash' else 'state_hash'
            connection.execute(f"UPDATE kill_switch_transitions SET {column}=?", ('f' * 64,))
            connection.commit()
        finally:
            connection.close()
    return path.read_bytes()


@pytest.mark.parametrize('operation', ['initial', 'retained'])
@pytest.mark.parametrize('damage', ['corrupt', 'truncated', 'empty_history', 'record_hash',
    'state_hash', 'future', 'tripped'])
def test_partition_rejects_invalid_native_stop_history_even_when_the_sealed_digest_matches(
        book, monkeypatch, operation, damage):
    root, payload, _ = inputs(book, monkeypatch)
    state, _, values, stores, events = book
    image = corrupt_stop(root, damage, values['now_ms'])
    if operation == 'retained':
        # A legacy diagnostic cut may already contain this invalid image hash.
        payload['retained_market_partition'] = prepare(state=state,
            legacy_origin=values['legacy_origin'], gold_origin=values['gold_origin'],
            registry=values['registry'], source_commit='c' * 40,
            kill_switch_sha256=hashlib.sha256(image).hexdigest(), now_ms=values['now_ms'])
    before = deepcopy(state), events.row_count(), image
    with pytest.raises(ValueError, match='PARTITION_STOP_SOURCE_INVALID'):
        worker.prepare_market_partition(root, values['legacy_origin']['account_ref_sha256'],
            'c' * 40, payload)
    from live.execution.demo_pre_submit import _state
    assert before == (_state(stores['dispatch'], stores['recovery'], stores['oms']),
        events.row_count(), (root / 'kill-switch.sqlite3').read_bytes())
