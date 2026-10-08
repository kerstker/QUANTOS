"""Proof readers must neither create nor migrate the original execution stores."""
from contextlib import closing
import sqlite3

import pytest

from live.execution.order_recovery import SqliteOrderRecoveryStore, OrderRecoveryError
from live.execution.position_ledger import LiveLedger, LiveLedgerError
from quantos.contracts.trading import OrderSide
from tests.live.test_order_recovery import _register


def image(directory):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in directory.iterdir() if p.is_file()}


def test_native_recovery_read_only_verifies_chain_and_blocks_every_write(tmp_path):
    path = str(tmp_path/'recovery ?#.db')
    with closing(SqliteOrderRecoveryStore(path, create=True)) as store:
        original = _register(store)
    before = image(tmp_path)
    with closing(SqliteOrderRecoveryStore(path, read_only=True)) as reader:
        assert reader.latest_records() == (original,)
        assert reader.chain_valid() and reader.deployment_blocked('alpha-a')
        for operation in (lambda: _register(reader), lambda: _register(reader, cid='new'),
                lambda: reader.mark_indeterminate('cid-1', now_ms=101)):
            with pytest.raises(OrderRecoveryError, match='read.only'):
                operation()
        with pytest.raises(sqlite3.OperationalError):
            reader._conn.execute('CREATE TABLE forbidden(value TEXT)')
    assert image(tmp_path) == before


def test_native_oms_read_only_returns_original_rows_without_projection_creation(tmp_path):
    path = str(tmp_path/'oms ?#.db')
    with closing(LiveLedger(path, create=True)) as store:
        store.record_submission('c1', 'BTC', OrderSide.BUY, .01,
            strategy_id='fixture', submitted_at=100)
        store.record_fill('f1', 'c1', 'BTC', OrderSide.BUY, .01, 60000,
            strategy_id='fixture', filled_at=101)
        orders, fills = store.orders(), store.fills()
        # Older modern ledgers may predate projection markers. Reads may report
        # the events as unprojected, but must not create the optional table.
        store._conn.execute('DROP TABLE live_oms_projections')
        store._conn.commit()
    before = image(tmp_path)
    with closing(LiveLedger(path, read_only=True)) as reader:
        assert reader.orders() == orders and reader.fills() == fills
        assert reader.all_positions() == {'BTC': .01}
        assert reader.unprojected_oms_event_hashes(('original-event',)) == ('original-event',)
        assert reader.tca_records() == []
        for operation in (
                lambda: reader.record_submission('c1', 'BTC', OrderSide.BUY, .01,
                    strategy_id='fixture', submitted_at=100),
                lambda: reader.update_status('c1', 'FILLED'),
                lambda: reader.record_fill('f2', 'c1', 'BTC', OrderSide.BUY, .01, 60000,
                    strategy_id='fixture', filled_at=102),
                lambda: reader.record_tca('f1', client_order_id='c1', instrument_id='BTC',
                    side=OrderSide.BUY, decision_price=60000, fill_price=60000,
                    quantity=.01, strategy_id='fixture', recorded_at=103)):
            with pytest.raises(LiveLedgerError, match='read.only'):
                operation()
        with pytest.raises(sqlite3.OperationalError):
            reader._conn.execute('CREATE TABLE forbidden(value TEXT)')
    assert image(tmp_path) == before


@pytest.mark.parametrize('store,error', [
    (LiveLedger, LiveLedgerError), (SqliteOrderRecoveryStore, OrderRecoveryError)])
def test_read_only_rejects_missing_create_and_symlink_without_writing(tmp_path, store, error):
    missing = tmp_path/'missing.db'
    with pytest.raises(error): store(str(missing), read_only=True)
    assert not missing.exists()
    with pytest.raises(error): store(str(missing), create=True, read_only=True)
    assert not missing.exists()
    with pytest.raises(error): store(':memory:', read_only=True)
    real = tmp_path/'real.db'
    with closing(store(str(real), create=True)): pass
    link = tmp_path/'alias.db'
    link.symlink_to(real)
    before = image(tmp_path)
    with pytest.raises(error): store(str(link), read_only=True)
    assert image(tmp_path) == before


def test_read_only_refuses_old_schema_without_auto_migrating(tmp_path):
    path = tmp_path/'old.db'
    with closing(sqlite3.connect(path)) as db:
        db.executescript('CREATE TABLE live_orders(client_order_id TEXT PRIMARY KEY);'
            'CREATE TABLE live_fills(fill_id TEXT PRIMARY KEY);')
    before = image(tmp_path)
    with pytest.raises(LiveLedgerError, match='schema'):
        LiveLedger(str(path), read_only=True)
    assert image(tmp_path) == before


def test_read_only_still_rejects_corrupt_recovery_chain(tmp_path):
    path = str(tmp_path/'corrupt.db')
    with closing(SqliteOrderRecoveryStore(path, create=True)) as store:
        _register(store)
        triggers = store._conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        for row in triggers: store._conn.execute('DROP TRIGGER "'+row[0]+'"')
        store._conn.execute("UPDATE order_recovery_events SET record_hash='invalid'")
        store._conn.commit()
    before = image(tmp_path)
    with pytest.raises(OrderRecoveryError, match='hash mismatch'):
        SqliteOrderRecoveryStore(path, read_only=True)
    assert image(tmp_path) == before
