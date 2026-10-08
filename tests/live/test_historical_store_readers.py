"""Native full-chain readers preserve source bytes and cannot append."""
import hashlib
import sqlite3

import pytest

from live.execution.order_event_store import SqliteOrderEventStore, OrderEventError
from live.execution.order_recovery import SqliteOrderRecoveryStore, OrderRecoveryError
from tests.live.test_order_event_store import _event
from tests.live.test_order_recovery import _register


def files(root):
    return {p.name:(p.stat().st_ino,p.stat().st_uid,p.stat().st_gid,p.stat().st_mode,
        hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.iterdir() if p.is_file()}


def test_event_reader_is_genuinely_readonly_and_verifies_global_chain(tmp_path):
    path = tmp_path/'events.db'
    writer = SqliteOrderEventStore(str(path), create=True)
    expected = writer.append(_event())
    writer.close()
    before = files(tmp_path)
    reader = SqliteOrderEventStore(str(path), read_only=True)
    try:
        assert reader.events() == (expected,)
        with pytest.raises(OrderEventError, match='read-only'):
            reader.append(_event())
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            reader._conn.execute('CREATE TABLE forbidden (value INTEGER)')
    finally:
        reader.close()
    assert files(tmp_path) == before


def test_recovery_reader_returns_complete_original_history_not_only_latest(tmp_path):
    path = tmp_path/'recovery.db'
    writer = SqliteOrderRecoveryStore(str(path), create=True)
    first = _register(writer)
    last = writer.mark_indeterminate('cid-1', now_ms=200)
    writer.close()
    before = files(tmp_path)
    reader = SqliteOrderRecoveryStore(str(path), read_only=True)
    try:
        assert reader.verified_history() == (first,last)
        assert reader.latest_records() == (last,)
    finally:
        reader.close()
    assert files(tmp_path) == before


@pytest.mark.parametrize('kind', ['event','recovery'])
def test_full_chain_reader_rejects_damage_before_requested_tail(tmp_path, kind):
    path=tmp_path/'store.db'
    if kind=='event':
        cls=SqliteOrderEventStore; error=OrderEventError; table='live_order_events'; column='event_hash'
        writer=cls(str(path), create=True); writer.append(_event())
    else:
        cls=SqliteOrderRecoveryStore; error=OrderRecoveryError; table='order_recovery_events'; column='record_hash'
        writer=cls(str(path), create=True); _register(writer); writer.mark_indeterminate('cid-1',now_ms=200)
    writer.close()
    conn=sqlite3.connect(path)
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        conn.execute('DROP TRIGGER '+name)
    conn.execute(f'UPDATE {table} SET {column}=? WHERE seq=1', ('f'*64,)); conn.commit(); conn.close()
    with pytest.raises(error):
        cls(str(path), read_only=True)


@pytest.mark.parametrize('kwargs', [{'create':True,'read_only':True},{'read_only':1}])
def test_event_readonly_mode_is_strict(tmp_path, kwargs):
    with pytest.raises(OrderEventError, match='read-only'):
        SqliteOrderEventStore(str(tmp_path/'absent.db'), **kwargs)
    assert not list(tmp_path.iterdir())


def test_nonempty_wal_requires_complete_native_snapshot_and_preserves_original_members(tmp_path):
    from tools.demo_namespace_checkpoint import snapshot_readonly_sqlite
    path=tmp_path/'events.db'; scratch=tmp_path/'scratch'; scratch.mkdir(mode=0o700)
    writer=SqliteOrderEventStore(str(path),create=True)
    try:
        expected=writer.append(_event())
        assert path.with_name(path.name+'-wal').stat().st_size>0
        before=files(tmp_path)
        with pytest.raises(OrderEventError,match='stable SQLite snapshot'):
            SqliteOrderEventStore(str(path),read_only=True)
        frozen,_,_=snapshot_readonly_sqlite(path,scratch)
        reader=SqliteOrderEventStore(str(frozen),read_only=True)
        try:assert reader.events()==(expected,)
        finally:reader.close()
        assert files(tmp_path)==before
    finally:writer.close()
