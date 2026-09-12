"""Project retained Demo origin terms into an explicitly bounded V2 model.

This is not submission readiness. Old origins lack the symbol max-notional
observation; source authenticity and actual cash rounding remain unverified.
"""
from decimal import Decimal

from services.execution.frozen_account_terms import parse_margin_table, ROUNDING, OVERFLOW
from services.execution.perpetual_ledger_v2 import ContractSpec, PerpetualLedgerError, ledger_decimal_context
from services.research.ai_factory_store import digest
from services.research.demo_execution_origin import assess, ACCOUNT_TERMS_SCHEMA

SETTLEMENT_POLICY = 'EXPLICIT_DEMO_QUOTE_QUANTUM_1E8_V1'


@ledger_decimal_context()
def contract_from_origin(origin, *, allow_observed_position=False):
    try:
        observed = assess(origin, as_of_ms=origin['completed_at_ms'])
        held = (allow_observed_position is True and observed['quote_positions'] == 1 and
                set(observed['issues']) <= {'QUOTE_BOOK_NOT_FLAT', 'QUOTE_WALLET_NOT_FREE_AND_FLAT'})
        if observed['status'] != 'OBSERVED_FLAT_QUOTE' and not held:
            raise ValueError('DEMO_CONTRACT_ORIGIN_UNRESOLVED')
        symbol = origin['symbol']; quote = symbol[3:]
        data = {r['kind']:r['data'] for r in origin['reads']}
        terms = data['rules']['terms']
        filters = {f['filterType']:f for f in terms['filters']}
        lot = filters['LOT_SIZE']; notional = filters['MIN_NOTIONAL']
        selected = next(s['leverage'] for s in data['symbols_after'] if s['symbol']==symbol)
        config=observed.get('target_configuration',{}).get('after')
        selected_cap=config['maxNotionalValue'] if config else None
        brackets, facts, cap = parse_margin_table(data['brackets'][0], symbol=symbol, selected_leverage=selected,
                                                 selected_cap=selected_cap)
        spec = ContractSpec(instrument_id=symbol, contract_type=f'LINEAR_{quote}_PERPETUAL', margin_asset=quote,
            contract_multiplier=Decimal(1), quantity_step=Decimal(lot['stepSize']), min_quantity=Decimal(lot['minQty']),
            min_notional=Decimal(notional['notional']), liquidation_fee_rate=Decimal(terms['liquidationFee']),
            settlement_quantum=Decimal('0.00000001'), margin_brackets=brackets,
            new_risk_notional_cap=cap, margin_overflow_policy=OVERFLOW)
        return spec.as_canonical_dict(), {
            'schema':'DEMO_ORIGIN_CONTRACT_PROJECTION_V2' if origin['schema']==ACCOUNT_TERMS_SCHEMA else 'DEMO_ORIGIN_CONTRACT_PROJECTION_V1','origin_sha256':digest(origin),
            'symbol':symbol,'quote_asset':quote,'selected_leverage':selected,'margin_brackets':facts,
            'initial_margin_rounding':ROUNDING,'settlement_policy':SETTLEMENT_POLICY,
            'settlement_rounding_verified':False,'symbol_config_cap_verified':False,
            **({'symbol_config_notional_cap':selected_cap,'symbol_config_cap_observed':True} if config else {}),
            'source_verified':False,'submission_enabled':False,'authoritative':False,
            'limitations':['DECLARED_QUOTE_QUANTUM_NOT_INFERRED_FROM_DISPLAY_PRECISION',
                *([] if config else ['SYMBOL_CONFIG_MAX_NOTIONAL_NOT_CAPTURED']),'FINITE_TABLE_TAIL_IS_RISK_ESTIMATE_ONLY',
                'CAPTURED_PUBLIC_LIQUIDATION_RATE_NOT_A_SIMULATED_LIQUIDATION_PERMISSION']}
    except (KeyError, TypeError, ArithmeticError, PerpetualLedgerError) as exc:
        raise ValueError('DEMO_CONTRACT_TERMS_UNSUPPORTED') from exc
