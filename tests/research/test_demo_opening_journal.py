"""Opening inventory and receipts must recover as a single versioned projection."""
from dataclasses import replace
import hashlib
import json

import pytest

from services.research.demo_execution_journal import DemoExecutionJournal, replay_report
from tests.research.test_demo_execution_journal import manifest
from tests.research.test_demo_account_baseline import captured
from tests.research.test_binance_order_receipts import native_spec
from tests.execution.test_perpetual_ledger_v2 import _spec


def opening_manifest(*, basis=True):
    m = manifest()
    # Move the synthetic capture before the frozen receipt window and use USDC.
    cap = captured().model_dump()
    origin = cap['observed_at_ms']
    cap['observed_at_ms'], cap['completed_at_ms'] = 1, 90
    for r in cap['reads']:
        r['started_at_ms'] = r['started_at_ms'] - origin + 1
        r['received_at_ms'] = r['received_at_ms'] - origin + 1
        r['data'] = json.loads(json.dumps(r['data']).replace('USDT', 'USDC'))
        if r['kind'] == 'positions': r['data'][0]['updateTime'] = 1
    from quantos.contracts.binance_demo_account import DemoAccountCapture
    cap = DemoAccountCapture.model_validate(cap)
    m.update(schema='DEMO_EXECUTION_REPLAY_V2', initial_position='2', expected_end_position='3')
    m['opening'] = {'schema': 'DEMO_OPENING_BINDING_V1', 'account_ref_sha256': m['native']['account_ref_sha256'],
        'capture': cap.model_dump(), 'capture_hash': hashlib.sha256(cap.model_dump_json().encode()).hexdigest(),
        'basis': [{'symbol': 'BTCUSDC', 'entry_notional': '200', 'source_sha256': 'e'*64}] if basis else [], 'contracts': []}
    return m


def test_nonflat_receipt_restart_does_not_recount_opening_pnl_or_commission(tmp_path):
    path = tmp_path/'journal.db'; m = opening_manifest()
    messages = native_spec()['raw_observed']['messages']
    with DemoExecutionJournal(path, manifest=m) as journal:
        journal.append(messages[:2])
        first = replay_report(journal)
        assert first['position_quantity'] == '2.4'
        assert first['opening']['status'] == 'DECLARED_INPUTS_READY'
        assert first['opening']['initial_position'] == '2'
        assert first['reconciliation']['initial_equity'] == '1020'
    with DemoExecutionJournal(path) as recovered:
        recovered.append(messages)
        result = replay_report(recovered)
        assert result['position_quantity'] == '3'
        assert result['reconciliation']['fee'] == '0.0198'
        assert result['reconciliation']['residual'] == '0'
        assert result['venue_cash_reconciliation'] == 'UNKNOWN'
        assert recovered.append(messages) == 0 and replay_report(recovered) == result
    before = path.read_bytes()
    with DemoExecutionJournal(path, read_only=True) as recovered: assert replay_report(recovered) == result
    assert path.read_bytes() == before


def test_missing_exact_basis_keeps_receipts_without_estimated_cash_projection(tmp_path):
    with DemoExecutionJournal(tmp_path/'journal.db', manifest=opening_manifest(basis=False)) as journal:
        journal.append(native_spec()['raw_observed']['messages'])
        result = replay_report(journal)
        assert result['projection_reason'] == 'OPENING_INPUTS_INCOMPLETE'
        assert result['opening']['missing_basis_symbols'] == ['BTCUSDC']
        assert result['journal_records'] == 3 and result['trades_count'] == 2
        assert result['reconciliation'] is None and result['position_quantity'] is None
        assert result['opening']['source_verified'] is False


@pytest.mark.parametrize('change', ['account', 'capture_hash', 'quantity', 'collateral', 'cutoff', 'basis_duplicate', 'basis_unknown'])
def test_changed_binding_is_rejected_before_creating_a_journal(tmp_path, change):
    m = opening_manifest()
    if change == 'account': m['opening']['account_ref_sha256'] = 'f'*64
    elif change == 'capture_hash': m['opening']['capture_hash'] = 'f'*64
    elif change == 'quantity': m['initial_position'] = '0'
    elif change == 'collateral': m['initial_collateral'] = '2000'
    elif change == 'cutoff': m['native']['coverage']['start_ms'] = 50
    elif change == 'basis_duplicate': m['opening']['basis'] *= 2
    else: m['opening']['basis'][0]['symbol'] = 'ETHUSDC'
    with pytest.raises(ValueError): DemoExecutionJournal(tmp_path/'journal.db', manifest=m)
    assert not (tmp_path/'journal.db').exists()


def test_v1_manifest_still_requires_flat_inventory(tmp_path):
    m = manifest(); m['initial_position'] = '2'
    with pytest.raises(ValueError, match='FLAT'): DemoExecutionJournal(tmp_path/'journal.db', manifest=m)


def with_extra_position(m, *, quote='USDC', isolated=False):
    cap = m['opening']['capture']; symbol = 'ETH' + quote
    for r in cap['reads']:
        if r['kind'].startswith('account'):
            r['data']['positions'].append({'symbol': symbol, 'positionSide': 'BOTH', 'positionAmt': '1'})
            if quote != 'USDC':
                r['data']['assets'].append({'asset':quote,'walletBalance':'50','crossWalletBalance':'50',
                    'availableBalance':'40','unrealizedProfit':'10','marginBalance':'60'})
        elif r['kind'].startswith('symbols'):
            r['data'].append({'symbol':symbol,'marginType':'ISOLATED' if isolated else 'CROSSED','leverage':10})
        elif r['kind'] == 'positions':
            r['data'].append({'symbol':symbol,'positionSide':'BOTH','positionAmt':'1','entryPrice':'50',
                'markPrice':'60','unRealizedProfit':'10','isolatedWallet':'50' if isolated else '0',
                'marginAsset':quote,'updateTime':1})
    recapture(m)
    return m


def recapture(m):
    from quantos.contracts.binance_demo_account import DemoAccountCapture, normalize_account_read
    for r in m['opening']['capture']['reads']: r['data'] = normalize_account_read(r['kind'], r['data'])
    cap = DemoAccountCapture.model_validate(m['opening']['capture'])
    m['opening']['capture'] = cap.model_dump()
    m['opening']['capture_hash'] = hashlib.sha256(cap.model_dump_json().encode()).hexdigest()


def test_other_positions_in_same_quote_cannot_be_silently_omitted(tmp_path):
    m = with_extra_position(opening_manifest())
    with DemoExecutionJournal(tmp_path/'missing.db', manifest=m) as journal:
        report = replay_report(journal)
        assert report['opening']['missing_basis_symbols'] == ['ETHUSDC']
        assert report['opening']['missing_contract_symbols'] == ['ETHUSDC']
        assert report['reconciliation'] is None
    m['opening']['basis'].append({'symbol':'ETHUSDC','entry_notional':'50','source_sha256':'e'*64})
    m['opening']['contracts'].append(replace(_spec(), instrument_id='ETHUSDC', margin_asset='USDC', contract_type='LINEAR_USDC_PERPETUAL').as_canonical_dict())
    with DemoExecutionJournal(tmp_path/'complete.db', manifest=m) as journal:
        journal.append(native_spec()['raw_observed']['messages'])
        report = replay_report(journal)
        assert report['opening']['positions_count'] == 2
        assert report['reconciliation']['initial_equity'] == '1030'
        assert report['reconciliation']['residual'] == '0'
        # No later marks were imported: the opening marks remain valuation inputs.
        assert report['reconciliation']['ending_equity'] == '1040.9802'


def test_other_quote_wallet_and_positions_are_not_mixed_into_usdc_book(tmp_path):
    m = with_extra_position(opening_manifest(), quote='USDT')
    with DemoExecutionJournal(tmp_path/'journal.db', manifest=m) as journal:
        journal.append(native_spec()['raw_observed']['messages'])
        report = replay_report(journal)
        assert report['opening']['positions_count'] == 1 and report['opening']['other_quote_positions_count'] == 1
        assert report['reconciliation']['initial_equity'] == '1020'


def test_bad_quantity_grid_is_a_visible_model_gap_and_preserves_receipts(tmp_path):
    m = opening_manifest()
    # Still exact inputs; current contract step cannot represent this inherited quantity.
    m['contract']['quantity_step'] = '3'
    m['contract']['min_quantity'] = '3'
    m['actions'][0]['quantity'] = '3'
    from services.research.ai_factory_store import digest
    m['native']['action_hash'] = digest(m['actions'])
    with DemoExecutionJournal(tmp_path/'journal.db', manifest=m) as journal:
        source = native_spec()
        from tests.research.test_binance_order_receipts import edit_message
        for i in range(3): edit_message(source, i, lambda p: p['o'].update(q='3'))
        edit_message(source, 2, lambda p: p['o'].update(l='2.6', z='3'))
        journal.append(source['raw_observed']['messages'])
        report = replay_report(journal)
        assert report['journal_records'] == 3
        assert report['opening']['status'] == 'INPUTS_INCOMPLETE'
        assert report['opening']['issues'] == ['OPENING_MODEL_QUANTITYRULEVIOLATION']


def test_basis_change_cannot_relabel_existing_receipts(tmp_path):
    m = opening_manifest(); path = tmp_path/'journal.db'
    with DemoExecutionJournal(path, manifest=m) as journal:
        journal.append(native_spec()['raw_observed']['messages'])
    m['opening']['basis'][0]['entry_notional'] = '201'
    before = path.read_bytes()
    with pytest.raises(ValueError, match='MANIFEST_CONFLICT'): DemoExecutionJournal(path, manifest=m)
    assert path.read_bytes() == before


def test_stale_or_same_cutoff_opening_cannot_seed_new_window(tmp_path):
    for start in (90, 120091):
        m = opening_manifest()
        m['native']['coverage'].update(start_ms=start, end_ms=start+1000)
        m['actions'][0]['timestamp'] = start + 1
        from services.research.ai_factory_store import digest
        m['native']['action_hash'] = digest(m['actions'])
        with pytest.raises(ValueError, match='CUTOFF'):
            DemoExecutionJournal(tmp_path/f'{start}.db', manifest=m)
        assert not (tmp_path/f'{start}.db').exists()


def test_unsupported_hedge_mode_is_visible_without_flattening_it_into_authority(tmp_path):
    m = opening_manifest(basis=False)
    for r in m['opening']['capture']['reads']:
        if r['kind'].startswith('config'): r['data']['dualSidePosition'] = True
    recapture(m)
    with DemoExecutionJournal(tmp_path/'journal.db', manifest=m) as journal:
        r = replay_report(journal)
        assert 'HEDGE_MODE_UNSUPPORTED' in r['opening']['issues']
        assert r['reconciliation'] is None and r['opening']['initial_position'] == '2'


def test_isolated_other_position_allocation_is_counted_once(tmp_path):
    m = with_extra_position(opening_manifest(), isolated=True)
    m['opening']['basis'].append({'symbol':'ETHUSDC','entry_notional':'50','source_sha256':'e'*64})
    m['opening']['contracts'].append(replace(_spec(), instrument_id='ETHUSDC', margin_asset='USDC', contract_type='LINEAR_USDC_PERPETUAL').as_canonical_dict())
    with DemoExecutionJournal(tmp_path/'journal.db', manifest=m) as journal:
        journal.append(native_spec()['raw_observed']['messages'])
        r = replay_report(journal)
        assert r['reconciliation']['initial_equity'] == '1030'
        assert r['reconciliation']['ending_equity'] == '1040.9802'
        assert r['reconciliation']['residual'] == '0'


def test_demo_base_asset_receipts_cannot_be_rescaled_by_contract_multiplier(tmp_path):
    m = opening_manifest(); m['contract']['contract_multiplier'] = '2'
    with pytest.raises(ValueError, match='MULTIPLIER'):
        DemoExecutionJournal(tmp_path/'journal.db', manifest=m)
    assert not (tmp_path/'journal.db').exists()


def test_opening_reference_changes_invalidate_same_order_query_capture(tmp_path):
    from services.research.demo_order_recovery import build_query_plan, compare_orders
    from tests.research.test_demo_order_recovery import capture
    m = opening_manifest()
    with DemoExecutionJournal(tmp_path/'first.db', manifest=m) as journal:
        journal.append(native_spec()['raw_observed']['messages'])
        query = capture(build_query_plan(journal))
    m['opening']['basis'][0]['source_sha256'] = 'f'*64
    with DemoExecutionJournal(tmp_path/'second.db', manifest=m) as journal:
        journal.append(native_spec()['raw_observed']['messages'])
        assert compare_orders(replay_report(journal), query, as_of_ms=200)['status'] == 'IDENTITY_MISMATCH'
