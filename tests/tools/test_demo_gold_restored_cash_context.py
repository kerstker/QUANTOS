"""Replay original execution and journals without following saved live paths."""
from copy import deepcopy
import os
from pathlib import Path
import shutil

import pytest

from tests.tools.test_gold_historical_position import (
    native_bar_checkpoint as native_bar_checkpoint,
    original_bar_checkpoint as original_bar_checkpoint,
    source_attachment as source_attachment,
    case as case,
    private_files as private_files,
    cold_scope_local as cold_scope_local,
    first_scope_archive as first_scope_archive,
    installed_first_scope as installed_first_scope,
    native_scope as native_scope,
    installed_partition_checkpoint as installed_partition_checkpoint,
)
from tests.tools.test_demo_gold_checkpoint_components import readonly_stores, snapshot
from tools import demo_gold_historical_position as history
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state


def test_original_flat_execution_and_cash_components_are_replayed_from_actual_journals(native_bar_checkpoint):
    from tests.tools.test_gold_historical_position import component_arguments
    from tools import demo_gold_checkpoint_components as components
    f = native_bar_checkpoint
    before = snapshot(f.root)
    with readonly_stores(f) as original:
        args = component_arguments(f, original)
        expected = components.verify(f.root, **args)
        with history.open_history(f.root, public=f.history_public, uid=f.executor_uid,
                retention=original['retention'], verified_public=f.verified_public) as book:
            with book.open_execution(as_of_ms=f.clock[0]) as replay:
                assert replay['execution']['state'] == _state(**f.stores)
                assert replay['execution']['context'] == f.context
                assert replay['market_partition'] is None
                assert all(store.read_only for store in replay['readonly_stores'].values())
                actual = components.verify(f.root, namespace='executor', public=f.history_public,
                    original=args['original'], uid=f.executor_uid, retention=original['retention'],
                    historical=book, **{key: replay[key] for key in
                        ('readonly_stores', 'execution', 'market_partition')})
                assert actual['episodes'] == expected['episodes']
                assert actual['episodes'][0]['reported_capture']['status'] == 'COMPLETE'
                assert actual['episodes'][0]['feedback']['status'] == 'COMPLETE'
                assert actual['partial'] == []
    assert snapshot(f.root) == before


def test_execution_cannot_infer_partition_absence_from_an_unverified_public_dictionary(native_bar_checkpoint):
    f = native_bar_checkpoint
    with readonly_stores(f) as original:
        with history.open_history(f.root, public=f.history_public, uid=f.executor_uid,
                retention=original['retention']) as book:
            with pytest.raises(history.HistoricalContextUnproven, match='PARTITION_RECORDS_REQUIRED'):
                with book.open_execution(as_of_ms=f.clock[0]):
                    pytest.fail('execution accepted an unverified public cut')


def test_execution_recheck_rejects_same_bytes_on_a_replaced_plan_inode(native_bar_checkpoint):
    f = native_bar_checkpoint
    with readonly_stores(f) as original:
        with pytest.raises(ValueError, match='HISTORICAL_SOURCE_CHANGED'):
            with history.open_history(f.root, public=f.history_public, uid=f.executor_uid,
                    retention=original['retention'], verified_public=f.verified_public) as book:
                with book.open_execution(as_of_ms=f.clock[0]) as replay:
                    key = next(iter(replay['execution']['records']))
                    plan = f.root / 'receipt-reconciliation' / 'plans' / (key + '.json')
                    replacement = plan.with_suffix('.replacement')
                    replacement.write_bytes(plan.read_bytes())
                    replacement.chmod(plan.stat().st_mode & 0o777)
                    replacement.replace(plan)


def test_root_cash_reader_consumes_actual_replayed_components_without_mutation(native_bar_checkpoint):
    from tests.tools.test_gold_historical_position import component_arguments
    from tools import demo_gold_restored_joins as joined
    f = native_bar_checkpoint
    before = snapshot(f.root)
    with readonly_stores(f) as original:
        args = component_arguments(f, original)
        with joined._cash(f.root, public=f.history_public, original=args['original'],
                retention=original['retention'], verified_public=f.verified_public,
                original_root=None, original_entries=None, as_of_ms=f.clock[0]) as cash:
            report = cash['report']
            assert report['status'] == 'PARTIAL'
            assert report['technical_cash_components_verified'] is False
            assert report['reported_cash_replay_verified'] is True
            assert len(report['episodes']) == 1
            assert report['episodes'][0]['reported_capture']['status'] == 'COMPLETE'
            assert report['episodes'][0]['feedback']['status'] == 'COMPLETE'
            assert report['episodes'][0]['cash']['status'] == 'OPENING_RETAINED'
            assert report['cash_reconciled'] is report['economic_authority'] is False
            cash['book'].recheck()
    assert snapshot(f.root) == before


def test_root_cash_reader_rejects_reported_inputs_available_after_the_fixed_cut(native_bar_checkpoint):
    from tests.tools.test_gold_historical_position import component_arguments
    from tools import demo_gold_restored_joins as joined
    f = native_bar_checkpoint
    with readonly_stores(f) as original:
        args = component_arguments(f, original)
        cutoff = max(f.context['available_at_ms'],
            *(row['recorded_at_ms'] for row in _state(**f.stores)['recovery']))
        assert cutoff < f.clock[0]
        with pytest.raises(ValueError, match='FUTURE_INPUTS'):
            with joined._cash(f.root, public=f.history_public, original=args['original'],
                    retention=original['retention'], verified_public=f.verified_public,
                    original_root=None, original_entries=None,
                    as_of_ms=cutoff):
                pytest.fail('cash reader accepted inputs from after its fixed cut')


@pytest.mark.parametrize('kind', ['complete', 'failed'])
@pytest.mark.parametrize('at_cut', [False, True])
def test_root_long_wallet_capture_respects_the_same_cut(native_bar_checkpoint, kind, at_cut):
    from tests.tools.test_gold_historical_position import component_arguments
    from tests.tools.test_binance_demo_cash_window import Clock, transport
    from services.research.demo_cash_window import load_capture, save_capture, assess
    from tools import demo_gold_restored_joins as joined, gold_strategy_cash as wallet
    from live.execution.demo_pre_submit import _retain
    f = native_bar_checkpoint
    cutoff = f.clock[0]
    area = f.root / 'strategy-cash-work' / _digest(f.base_runtime)
    opening = load_capture(area / 'opening')
    clock = Clock()
    clock.ms = cutoff + 1
    def unavailable(*args, **kwargs):
        raise TimeoutError('isolated unavailable wallet GET')
    report = wallet.collector.collect(credential_file=None, operator_read=True,
        credential_reader=lambda _: ('synthetic-key', 'synthetic-secret'),
        get=transport(clock, []) if kind == 'complete' else unavailable,
        observe_seconds=30, opening_prefix=opening, long_window=True,
        clock_ms=clock.now, sleep=clock.sleep)
    assert report['reads'][:2] == opening['reads'] and report['attempts'][:2] == opening['attempts']
    assert report['completed_at_ms'] > cutoff and report['account_ref_sha256'] == f.account
    with pytest.raises(ValueError, match='CASH_WINDOW_FUTURE'):
        assess(report, as_of_ms=cutoff)
    if kind == 'complete':
        assert report['status'] == 'CAPTURED'
        save_capture(f.root / 'strategy-cash' / _digest(f.base_runtime), report)
    else:
        assert report['status'] == 'PARTIAL'
        failures = area / 'failures'
        failures.mkdir(mode=0o700)
        _retain(failures, report)
    before = snapshot(f.root)
    with readonly_stores(f) as original:
        args = component_arguments(f, original)
        def read():
            return joined._cash(f.root, public=f.history_public, original=args['original'],
                retention=original['retention'], verified_public=f.verified_public,
                original_root=None, original_entries=None,
                as_of_ms=report['completed_at_ms'] if at_cut else cutoff)
        if not at_cut:
            with pytest.raises(ValueError, match='CASH_WINDOW_FUTURE'):
                with read():
                    pytest.fail('root accepted long cash inputs completed after its fixed cut')
        else:
            with read() as cash:
                view = cash['report']
                assert view['status'] == ('COMPLETE' if kind == 'complete' else 'PARTIAL')
                assert view['technical_cash_components_verified'] is (kind == 'complete')
                assert view['reported_cash_replay_verified'] is True
                assert view['as_of_ms'] == report['completed_at_ms']
                assert view['episodes'][0]['cash']['status'] == (
                    'COMPLETE' if kind == 'complete' else 'OPENING_RETAINED')
                assert all(view[key] is False for key in ('cash_reconciled', 'writer_barrier_verified',
                    'whole_set_verified', 'mutation_permitted', 'submission_enabled', 'economic_authority'))
    assert snapshot(f.root) == before


def test_original_opening_cash_cut_is_inclusive_and_legacy_read_is_unchanged(native_bar_checkpoint):
    from services.research.demo_cash_window import load_capture
    from tools import demo_gold_checkpoint_components as components
    f = native_bar_checkpoint
    area = f.root / 'strategy-cash-work' / _digest(f.base_runtime)
    opening = load_capture(area / 'opening')
    before = snapshot(f.root)
    args = (f.root, f.base_runtime, f.history_public, f.executor_uid)
    legacy = components._cash(*args)
    assert legacy['status'] == 'OPENING_RETAINED'
    assert components._cash(*args, cash_as_of_ms=opening['completed_at_ms']) == legacy
    with pytest.raises(ValueError, match='CASH_WINDOW_FUTURE'):
        components._cash(*args, cash_as_of_ms=opening['completed_at_ms'] - 1)
    assert snapshot(f.root) == before


def test_restored_execution_uses_indexed_journals_when_original_tree_is_unavailable(native_bar_checkpoint, tmp_path):
    f = native_bar_checkpoint
    with readonly_stores(f) as original:
        expected = deepcopy(f.context)
        restored = tmp_path / 'cash-restore'
        shutil.copytree(f.root, restored)
        entries = [dict(relative=p.relative_to(f.root).as_posix(), kind='FILE')
            for p in f.root.rglob('*') if p.is_file()]
        before = snapshot(restored)
        held = f.root.with_name('unavailable-original')
        f.root.rename(held)
        try:
            with history.open_history(restored, public=f.history_public, uid=os.getuid(),
                    retention=original['retention'], original_root=f.root, original_entries=entries,
                    verified_public=f.verified_public) as book:
                with book.open_execution(as_of_ms=f.clock[0]) as replay:
                    assert replay['execution']['context'] == expected
                    for row in replay['execution']['records'].values():
                        assert Path(row['plan']['journal_path']).is_relative_to(f.root)
                        assert row['journal']['manifest_sha256'] == row['plan']['manifest_hash']
        finally:
            held.rename(f.root)
    assert snapshot(restored) == before


@pytest.mark.parametrize('damage', [None, 'missing_attachment', 'changed_bundle'])
def test_restored_cash_replay_uses_verified_bundle_without_repairing_archived_source(
        native_bar_checkpoint, tmp_path, damage):
    from tests.tools.test_gold_historical_position import component_arguments
    from tests.tools.test_demo_gold_namespace_checkpoint import materialize
    from tools import demo_gold_namespace_checkpoint as native
    from tools import demo_gold_restored_joins as joined
    f = native_bar_checkpoint
    restored = tmp_path / 'cash-components-restored'
    with native.capture_inventory(**f.history_arguments) as captured:
        entries = list(captured.iter_entries())
        materialize(captured, restored)
    source = restored / 'strategy-bar-sources' / f.base_runtime['archive_source_commit']
    assert source.is_dir() and not (source / '.git').exists()
    before = snapshot(restored)
    with readonly_stores(f) as stores:
        args = component_arguments(f, stores)
        attachment = deepcopy(f.source_attachment)
        if damage == 'missing_attachment':
            attachment = None
        elif damage == 'changed_bundle':
            # Keep the immutable declaration and change actual owned bytes.
            target = native.source_attachments(attachment)[0]
            path = Path(target['bundle_path'])
            path.write_bytes(path.read_bytes() + b'changed-original-bundle')
        def read():
            return joined._cash(restored, public=f.history_public, original=args['original'],
                retention=stores['retention'], verified_public=f.verified_public,
                original_root=f.root, original_entries=entries, as_of_ms=f.clock[0],
                source_attachment=attachment)
        if damage:
            reason = 'SOURCE_ATTACHMENT_REQUIRED' if damage == 'missing_attachment' else 'SOURCE_BUNDLE_CHANGED'
            with pytest.raises(ValueError, match=reason):
                with read():
                    pytest.fail('restored cash accepted missing or changed source evidence')
        else:
            with read() as cash:
                assert cash['report']['reported_cash_replay_verified'] is True
                assert cash['report']['status'] == 'PARTIAL'
                assert cash['root'] != restored and (cash['root'] / source.relative_to(restored) / '.git').is_dir()
                cash['trees'].recheck(cash['source_attachment'])
            assert not cash['root'].exists()
    assert snapshot(restored) == before and not (source / '.git').exists()


@pytest.mark.parametrize('cold_scope_local', [True], indirect=True)
def test_installed_btc_and_gold_replay_uses_original_partition_without_merging_cash(
        installed_partition_checkpoint, tmp_path, monkeypatch):
    from tests.live.gold_partition_checkpoint_fixture import bind_checkpoint_public
    from live.execution.demo_market_partition_runtime import _descriptor
    f = bind_checkpoint_public(installed_partition_checkpoint, tmp_path, monkeypatch)
    before = snapshot(f.root)
    with readonly_stores(f) as original:
        with history.open_history(f.root, public=f.history_public, uid=f.executor_uid,
                retention=original['retention'], verified_public=f.verified_public) as book:
            with book.open_execution(as_of_ms=f.clock[0]) as replay:
                assert replay['execution']['context'] == f.context
                assert replay['market_partition'] == _descriptor(f.local['value']['record'])
                assert len(replay['execution']['state']['reservations']) == 3
                assert len(replay['execution']['reservation_sources']) == 2
                assert _digest(replay['legacy_context']) == f.local['value']['record']['candidate']['binding']['legacy_context_sha256']
    assert snapshot(f.root) == before
