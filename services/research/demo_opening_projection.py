"""Bind declared opening inputs to one observed quote-asset account book.

This does not authenticate the account, turn display averages into exact basis,
attest stream coverage, or grant submission authority. Only the canonical V2
ledger performs cash accounting. Missing inputs remain explicit diagnostics.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re

from quantos.contracts.binance_demo_account import DemoAccountCapture
from services.execution.perpetual_ledger_v2 import (
    ContractSpec, MarginBracket, MarginMode, OpeningPosition, OpeningLedgerState,
    LinearPerpetualLedger, PerpetualLedgerError, ledger_decimal_context,
)
from services.research.demo_account_baseline import build_baseline
from quantos.contracts.binance_demo_clock import bracket_bounds

OPENING_MANIFEST_SCHEMA = 'DEMO_EXECUTION_REPLAY_V2'


def fields(body, keys):
    if not isinstance(body, dict) or set(body) != set(keys.split()):
        raise ValueError('DEMO_REPLAY_FIELDS_INVALID')


def decimal(value):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r'-?\d+(\.\d+)?', value):
        raise ValueError('EXACT_DECIMAL_REQUIRED')
    result = Decimal(value)
    if abs(result) > Decimal('1e15') or result.as_tuple().exponent < -24:
        raise ValueError('DECIMAL_OUT_OF_RANGE')
    return result


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('OPENING_SOURCE_HASH_REQUIRED')
    return value


@ledger_decimal_context()
def contract_spec(raw):
    finite = isinstance(raw, dict) and 'new_risk_notional_cap' in raw
    fields(raw, 'decimal_context margin_admission_policy instrument_id contract_type margin_asset contract_multiplier quantity_step min_quantity min_notional liquidation_fee_rate settlement_quantum margin_brackets'+
           (' new_risk_notional_cap margin_overflow_policy' if finite else ''))
    if not isinstance(raw['margin_brackets'], list) or not 1 <= len(raw['margin_brackets']) <= 20:
        raise ValueError('BOUNDED_MARGIN_BRACKETS_REQUIRED')
    brackets = []
    for row in raw['margin_brackets']:
        fields(row, 'notional_floor notional_cap initial_margin_rate maintenance_margin_rate maintenance_amount')
        brackets.append(MarginBracket(**{k: None if v is None else decimal(v) for k, v in row.items()}))
    spec = ContractSpec(**{k: raw[k] for k in ('instrument_id', 'contract_type', 'margin_asset')},
        **{k: decimal(raw[k]) for k in ('contract_multiplier', 'quantity_step', 'min_quantity', 'min_notional', 'liquidation_fee_rate', 'settlement_quantum')},
        margin_brackets=tuple(brackets),
        **({'new_risk_notional_cap':decimal(raw['new_risk_notional_cap']),
            'margin_overflow_policy':raw['margin_overflow_policy']} if finite else {}))
    if spec.as_canonical_dict() != raw:
        raise ValueError('CONTRACT_VERSION_OR_QUOTE_MISMATCH')
    if spec.contract_multiplier != Decimal(1):
        raise ValueError('BINANCE_DEMO_BASE_QUANTITY_MULTIPLIER_MUST_BE_ONE')
    return spec


@ledger_decimal_context()
def prepare_opening(manifest, primary):
    """Return specs, immutable seed and a fresh, non-authoritative assessment."""
    if manifest['schema'] != OPENING_MANIFEST_SCHEMA:
        return (primary,), None, None
    raw = manifest['opening']
    fields(raw, 'schema account_ref_sha256 capture capture_hash basis contracts')
    if (raw['schema'] != 'DEMO_OPENING_BINDING_V1' or
            sha(raw['account_ref_sha256']) != manifest['native']['account_ref_sha256']):
        raise ValueError('OPENING_ACCOUNT_REFERENCE_MISMATCH')
    capture = DemoAccountCapture.model_validate(raw['capture'])
    if (capture.status != 'CAPTURED' or
            sha(raw['capture_hash']) != hashlib.sha256(capture.model_dump_json().encode()).hexdigest()):
        raise ValueError('OPENING_CAPTURE_INVALID')
    # Availability is always local. V3 adds a separate conservative venue cutoff;
    # neither that bound nor the common-clock legacy input is source authority.
    if not 0 < manifest['native']['coverage']['start_ms'] - capture.completed_at_ms <= 120000:
        raise ValueError('OPENING_CUTOFF_MUST_PRECEDE_RECEIPT_WINDOW')
    boundary=None
    if manifest['native'].get('schema')=='BINANCE_USDM_ORDER_RECEIPTS_V3':
        clock=bracket_bounds(manifest['native']['clock_bracket'],start_ms=capture.completed_at_ms,
            end_ms=manifest['native']['coverage']['end_ms'])
        boundary={'policy':'CONSERVATIVE_VENUE_UPPER_BOUND_NOT_OBSERVATION_V1',
            'local_observed_at_ms':capture.completed_at_ms,
            'venue_not_before_ms':capture.completed_at_ms+clock['offset_upper_ms'],
            'clock_input_sha256':clock['input_sha256'],'source_verified':False}
    baseline = build_baseline(capture, as_of_ms=capture.completed_at_ms)
    wallets = [a for a in baseline['assets'] if a['asset'] == primary.margin_asset]
    if len(wallets) != 1 or decimal(wallets[0]['observed_wallet']) != decimal(manifest['initial_collateral']):
        raise ValueError('OPENING_COLLATERAL_MISMATCH')
    positions = [p for p in baseline['positions'] if p['margin_asset'] == primary.margin_asset]
    target = [p for p in positions if p['symbol'] == primary.instrument_id]
    if sum((decimal(p['quantity']) for p in target), Decimal(0)) != decimal(manifest['initial_position']):
        raise ValueError('OPENING_POSITION_MISMATCH')
    names = {p['symbol'] for p in positions}
    basis = {}
    specs = {primary.instrument_id: primary} if isinstance(primary, ContractSpec) else {}
    for key in ('basis', 'contracts'):
        if not isinstance(raw[key], list) or len(raw[key]) > 2000:
            raise ValueError('BOUNDED_OPENING_INPUTS_REQUIRED')
    for b in raw['basis']:
        fields(b, 'symbol entry_notional source_sha256')
        if b['symbol'] not in names or b['symbol'] in basis or decimal(b['entry_notional']) <= 0:
            raise ValueError('OPENING_BASIS_IDENTITY_INVALID')
        sha(b['source_sha256']); basis[b['symbol']] = b
    for row in raw['contracts']:
        spec = contract_spec(row)
        if (spec.instrument_id == primary.instrument_id or spec.instrument_id in specs or
                spec.instrument_id not in names or spec.margin_asset != primary.margin_asset):
            raise ValueError('OPENING_CONTRACT_IDENTITY_INVALID')
        specs[spec.instrument_id] = spec
    missing_basis = sorted(names - basis.keys())
    missing_contracts = sorted((names | {primary.instrument_id}) - specs.keys())
    issues = []
    if baseline['inventory_status'] != 'STABLE_OBSERVATIONS': issues.append('ACCOUNT_CHANGED_DURING_CAPTURE')
    if baseline['account_config']['dualSidePosition'] or any(p['position_side'] != 'BOTH' for p in positions):
        issues.append('HEDGE_MODE_UNSUPPORTED')
    if baseline['account_config']['multiAssetsMargin']: issues.append('MULTI_ASSET_COLLATERAL_UNSUPPORTED')
    if any(p['margin_mode'] is None for p in positions): issues.append('OPENING_MARGIN_MODE_MISSING')
    if any(p['margin_mode'] != 'CROSSED' for p in target): issues.append('TARGET_CROSS_MARGIN_REQUIRED')
    if missing_basis: issues.append('EXACT_ENTRY_BASIS_MISSING')
    if missing_contracts: issues.append('OPENING_CONTRACT_RULES_MISSING')
    if baseline['open_orders']: issues.append('PREEXISTING_ORDERS_UNRESOLVED')
    for flag in ('POSITION_MARGIN_ASSET_MISSING', 'POSITION_CONTRACT_REQUIRES_REVIEW'):
        if flag in baseline['issues']: issues.append(flag)
    seed = None
    if not issues:
        try:
            seed = OpeningLedgerState(timestamp=boundary['venue_not_before_ms'] if boundary else capture.completed_at_ms,
                source_sha256=hashlib.sha256(json.dumps({'opening':raw,'clock_boundary':boundary} if boundary else raw,
                    sort_keys=True,separators=(',', ':'),ensure_ascii=False).encode()).hexdigest(),
                positions=tuple(OpeningPosition(instrument_id=p['symbol'], quantity=decimal(p['quantity']),
                    entry_notional=decimal(basis[p['symbol']]['entry_notional']), mark_price=decimal(p['observed_mark_price']),
                    margin_mode=MarginMode.CROSS if p['margin_mode']=='CROSSED' else MarginMode.ISOLATED,
                    isolated_wallet=decimal(p['isolated_wallet'])) for p in positions))
            LinearPerpetualLedger(tuple(specs.values()), initial_collateral=decimal(manifest['initial_collateral']), opening_state=seed)
        except (ValueError, TypeError, ArithmeticError, PerpetualLedgerError) as exc:
            issues.append('OPENING_MODEL_' + type(exc).__name__.upper()); seed = None
    return tuple(specs.values()), seed, {
        'schema': 'DEMO_OPENING_ASSESSMENT_V1', 'scope': 'SINGLE_QUOTE_ACCOUNT_BOOK_TECHNICAL_PROJECTION',
        'status': 'INPUTS_INCOMPLETE' if issues else 'DECLARED_INPUTS_READY',
        'capture_hash': raw['capture_hash'], 'account_ref_sha256': raw['account_ref_sha256'],
        'opening_at_ms': capture.completed_at_ms, 'quote_asset': primary.margin_asset,
        **({'clock_boundary':boundary} if boundary else {}),
        'initial_collateral': manifest['initial_collateral'], 'initial_position': manifest['initial_position'],
        'positions_count': len(positions), 'other_quote_positions_count': len(baseline['positions']) - len(positions),
        'missing_basis_symbols': missing_basis, 'missing_contract_symbols': missing_contracts, 'issues': sorted(set(issues)),
        'basis_policy': 'CALLER_DECLARED_EXACT_ENTRY_NOTIONAL', 'source_verified': False,
        'atomic_snapshot_verified': False, 'submission_enabled': False, 'venue_cash_reconciliation': 'UNKNOWN',
        'positions': [{'symbol': p['symbol'], 'position_side': p['position_side'], 'quantity': p['quantity'], 'margin_mode': p['margin_mode'],
            'entry_notional': basis[p['symbol']]['entry_notional'] if p['symbol'] in basis else None,
            'basis_source_sha256': basis[p['symbol']]['source_sha256'] if p['symbol'] in basis else None,
            'contract_available': p['symbol'] in specs} for p in positions],
    }


@ledger_decimal_context()
def bind_opening(manifest, capture, *, basis=None, contracts=None):
    """Build a new frozen manifest; never modify an existing journal in place."""
    capture = DemoAccountCapture.model_validate(capture.model_dump())
    if capture.status != 'CAPTURED' or 'opening' in manifest:
        raise ValueError('NEW_COMPLETE_OPENING_BINDING_REQUIRED')
    result = deepcopy(manifest)
    symbol = result['native']['symbol']
    if symbol not in ('BTCUSDT', 'BTCUSDC'):
        raise ValueError('UNSUPPORTED_DEMO_SYMBOL')
    quote = symbol[3:]
    data = {r.kind: r.data for r in capture.reads}
    assets = [a for a in data['account_after']['assets'] if a['asset'] == quote]
    if len(assets) != 1:
        raise ValueError('OPENING_COLLATERAL_MISSING')
    result.update(schema=OPENING_MANIFEST_SCHEMA, initial_collateral=assets[0]['walletBalance'],
        initial_position=format(sum((decimal(p['positionAmt']) for p in data['account_after']['positions']
                                      if p['symbol'] == symbol), Decimal(0)), 'f'))
    result['opening'] = {'schema': 'DEMO_OPENING_BINDING_V1',
        'account_ref_sha256': result['native']['account_ref_sha256'],
        'capture_hash': hashlib.sha256(capture.model_dump_json().encode()).hexdigest(),
        'capture': capture.model_dump(), 'basis': deepcopy(basis) if basis is not None else [],
        'contracts': deepcopy(contracts) if contracts is not None else []}
    return result
