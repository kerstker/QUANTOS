"""Offline prerequisites for a separately reviewed BTC-to-gold Demo enrollment.

The existing ledger rebuild supplies the legacy context. No currency conversion,
new book, cash authority, operator approval, persistence or execution is created.
"""
from decimal import Decimal
import json

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from live.execution.demo_position_context import validate_origin
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.research.demo_account_baseline import build_baseline
from services.research.demo_execution_origin import account_capture, assess
from services.validation.demo_submission import validate_registry, registry_sha256


def _inventory(origin, now_ms):
    baseline = build_baseline(account_capture(origin), as_of_ms=now_ms)
    require(baseline['inventory_status']=='STABLE_OBSERVATIONS' and not baseline['positions'],
            'TRANSITION_ALL_POSITIONS_MUST_BE_FLAT')
    for row in baseline['assets']:
        wallet = Decimal(row['observed_wallet'])
        require(wallet>=0 and all(Decimal(row[key])==wallet for key in
            ('observed_cross_wallet', 'observed_available', 'observed_margin_balance'))
            and Decimal(row['observed_unrealized'])==Decimal(row['account_identity_residual'])==0,
            'TRANSITION_ALL_COLLATERAL_MUST_BE_FREE_AND_FLAT')
    fields = ('asset', 'observed_wallet', 'observed_cross_wallet', 'observed_available',
              'observed_margin_balance', 'observed_unrealized', 'account_identity_residual')
    return sorted([{key: row[key] for key in fields} for row in baseline['assets']],
                  key=lambda row: row['asset'])


@ledger_decimal_context()
def review(*, legacy_origin, gold_origin, legacy_context, registry, now_ms):
    """Reconcile observations while keeping distinct USDC and USDT evidence.

    Inputs are diagnostics here. The operator path obtains the context through
    the original protected executor, but these observations remain non-atomic
    and non-authoritative. This result cannot be installed as a policy or book.
    """
    require(type(now_ms) is int and now_ms>0, 'TRANSITION_TIME')
    validate_registry(registry)
    require(registry['keys'] and all(key['valid_until_ms']<=now_ms for key in registry['keys']),
            'TRANSITION_EXPIRED_PRIOR_ENROLLMENT_REQUIRED')
    require(legacy_origin['symbol']=='BTCUSDC' and legacy_context['symbol']=='BTCUSDC'
        and gold_origin['symbol']=='XAUUSDT', 'TRANSITION_MARKET_SCOPE')
    require(legacy_context['quantity']=='0' and Decimal(legacy_context['entry_notional'])==0,
            'TRANSITION_LEGACY_BOOK_NOT_FLAT')
    require(legacy_origin['account_ref_sha256']==gold_origin['account_ref_sha256']
        and legacy_origin['source_sha256']==gold_origin['source_sha256'], 'TRANSITION_CAPTURE_BINDING')
    require(legacy_origin['completed_at_ms']<=gold_origin['observed_at_ms']<=now_ms
        and gold_origin['observed_at_ms']//86400000==now_ms//86400000, 'TRANSITION_CAPTURE_SEQUENCE')
    account = validate_origin(legacy_origin, legacy_context, as_of_ms=now_ms)
    gold = assess(gold_origin, as_of_ms=now_ms)
    require(gold['status']=='OBSERVED_FLAT_QUOTE', 'TRANSITION_GOLD_ORIGIN_UNRESOLVED')
    legacy_inventory = _inventory(legacy_origin, now_ms)
    gold_inventory = _inventory(gold_origin, now_ms)
    require(legacy_inventory==gold_inventory, 'TRANSITION_CROSS_CAPTURE_CASH_DIFFERENCE')
    # Copy original risk facts without converting their units or restarting them.
    risk = {key: legacy_context[key] for key in ('canonical_wallet', 'initial_equity',
        'initial_day_pnl', 'initial_plan_turnover', 'peak_equity_floor', 'capacity_consumed',
        'ledger_state_hash', 'day_id', 'cash_flow_policy')}
    for key in ('day_checkpoint_sha256', 'day_open_equity'):
        if key in legacy_context: risk[key] = legacy_context[key]
    risk.update(symbol='BTCUSDC', collateral='USDC', quantity='0',
                context_sha256=_digest(legacy_context), observed_day_pnl=account['day_pnl'])
    result = dict(schema='DEMO_MARKET_TRANSITION_REVIEW_V1', status='NEW_GOLD_ENROLLMENT_REQUIRED',
        environment='BINANCE_USDM_DEMO', reviewed_at_ms=now_ms,
        account_ref_sha256=legacy_origin['account_ref_sha256'], registry_sha256=registry_sha256(registry),
        legacy_origin_sha256=_digest(legacy_origin), gold_origin_sha256=_digest(gold_origin),
        legacy_book=risk, gold_observation=dict(symbol='XAUUSDT', collateral='USDT', wallet=gold['wallet'],
            observed_at_ms=gold_origin['observed_at_ms'], installed_pilot_baseline=False),
        account_inventory_sha256=_digest(gold_inventory), collateral_conversion='NOT_PERFORMED',
        required_handoff='REVIEWED_MARKET_PARTITION_AND_NEW_GOLD_APPROVAL',
        migration_installed=False, approval_granted=False, source_verified=False,
        atomic_snapshot_verified=False, submission_enabled=False, economic_authority=False)
    return json.loads(json.dumps(result, allow_nan=False))
