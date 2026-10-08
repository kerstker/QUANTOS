"""Exact whole-batch budget review of explicitly supplied base-unit debit bounds.

Debit bounds are caller estimates, not verified venue costs. This module neither
derives xStocks order units/fees nor reserves funds or grants trading permission.
Pending debits are conservatively additional to venue holds; no sale proceeds,
fee rebates or credit lines finance new orders before reconciliation.
"""
from decimal import Context, Decimal, localcontext
import re

from services.data.kraken_account import canonical, number
from services.data.kraken_public import require


def _amounts(value):
    require(isinstance(value, dict) and len(value) <= 128, 'KRAKEN_FUNDING_AMOUNTS')
    result = {}
    for asset, amount in value.items():
        require(isinstance(asset, str) and re.fullmatch(r'[A-Za-z0-9._]{1,48}', asset),
                'KRAKEN_FUNDING_ASSET')
        require(isinstance(amount, str), 'KRAKEN_FUNDING_DECIMAL_STRING')
        result[asset] = number(amount)
    return result


def review(account, requirements, *, as_of_ms):
    require(isinstance(requirements, dict) and set(requirements) == {
        'schema', 'account_ref_sha256', 'quantity_basis', 'cash_reserves', 'pending_debits', 'orders'
    } and requirements['schema'] == 'KRAKEN_FUNDING_REQUIREMENTS_V1', 'KRAKEN_FUNDING_REQUIREMENTS')
    require(account.get('schema') == 'KRAKEN_ACCOUNT_OBSERVATION_V1' and
            account.get('quantity_basis') == requirements['quantity_basis'] == 'base' and
            account.get('submission_enabled') is False and account.get('authoritative') is False,
            'KRAKEN_FUNDING_ACCOUNT')
    ref = requirements['account_ref_sha256']
    require(isinstance(ref, str) and re.fullmatch('[a-f0-9]{64}', ref) and
            ref == account.get('account_ref_sha256'), 'KRAKEN_FUNDING_ACCOUNT_BINDING')
    require(type(as_of_ms) is int and as_of_ms > 0, 'KRAKEN_FUNDING_CLOCK')
    reserve = _amounts(requirements['cash_reserves'])
    pending = _amounts(requirements['pending_debits'])
    orders = requirements['orders']
    require(isinstance(orders, list) and 1 <= len(orders) <= 32, 'KRAKEN_FUNDING_BATCH')
    issues = []
    if account.get('status') != 'OBSERVED_STABLE' or account.get('issues') != []:
        issues.append('ACCOUNT_UNRESOLVED')
    if account.get('open_orders') != []:
        issues.append('OPEN_ORDERS_REQUIRE_RECONCILIATION')
    # Freshness starts at the first request, not the end of a slow account sweep.
    reads = account.get('reads')
    first = reads[0].get('requested_at_ms') if isinstance(reads, list) and reads else None
    observed = account.get('observed_at_ms')
    if not (type(first) is int and type(observed) is int and
            0 < first <= observed <= as_of_ms and as_of_ms-first <= 120000):
        issues.append('ACCOUNT_STALE_OR_CLOCK_INVALID')
    balances = account.get('balances')
    require(isinstance(balances, dict), 'KRAKEN_FUNDING_BALANCES')
    totals = {}; identifiers = set()
    with localcontext(Context(prec=100)):
        for order in orders:
            require(isinstance(order, dict) and set(order) == {
                'client_order_id', 'draft_sha256', 'principal_debits', 'fee_debits'
            }, 'KRAKEN_FUNDING_ORDER')
            oid = order['client_order_id']; draft = order['draft_sha256']
            require(isinstance(oid, str) and re.fullmatch('[a-f0-9]{32}', oid) and oid not in identifiers,
                    'KRAKEN_FUNDING_DUPLICATE_OR_INVALID_ID')
            identifiers.add(oid)
            require(isinstance(draft, str) and re.fullmatch('[a-f0-9]{64}', draft), 'KRAKEN_FUNDING_DRAFT')
            principal = _amounts(order['principal_debits'])
            require(principal and all(v > 0 for v in principal.values()), 'KRAKEN_FUNDING_PRINCIPAL')
            fees = order['fee_debits']
            if fees is None:
                issues.append('FEE_DEBITS_UNKNOWN:'+oid)
                fees = {}
            else:
                fees = _amounts(fees)
                require(bool(fees), 'KRAKEN_FUNDING_FEE_ASSET_REQUIRED')
            for mapping in (principal, fees):
                for asset, debit in mapping.items():
                    totals[asset] = totals.get(asset, Decimal(0)) + debit
        assets = {}
        # Reject credit exposure anywhere in the account, not only spent assets.
        for asset, balance in balances.items():
            if number(balance['credit_used']) != 0 or number(balance['balance'], signed=True) < 0:
                issues.append('ACCOUNT_CREDIT_EXPOSURE:'+asset)
            if number(balance['available'], signed=True) < 0:
                issues.append('NEGATIVE_AVAILABLE_BALANCE:'+asset)
        for asset in sorted(set(totals) | set(reserve) | set(pending)):
            balance = balances.get(asset)
            if balance is None:
                issues.append('ASSET_BALANCE_UNKNOWN:'+asset)
                owned = Decimal(0)
            else:
                owned = max(Decimal(0), min(
                    number(balance['balance'], signed=True)-number(balance['hold_trade']),
                    number(balance['available'], signed=True)))
            protected = reserve.get(asset, Decimal(0))
            committed = pending.get(asset, Decimal(0))
            new = totals.get(asset, Decimal(0))
            remaining = owned-protected-committed-new
            shortfall = max(Decimal(0), -remaining)
            if shortfall > 0:
                issues.append('INSUFFICIENT_OWNED_FUNDS:'+asset)
            assets[asset] = {k: canonical(v) for k, v in {
                'owned_available': owned, 'protected_reserve': protected,
                'pending_debits': committed, 'new_debits': new,
                'remaining': max(Decimal(0), remaining), 'shortfall': shortfall,
            }.items()}
    return {'schema': 'KRAKEN_FUNDING_REVIEW_V1', 'account_ref_sha256': ref,
            'as_of_ms': as_of_ms, 'status': 'BLOCKED' if issues else 'CONDITIONAL_BUDGET_FIT',
            'assets': assets, 'issues': issues, 'submission_enabled': False, 'authoritative': False,
            'reservation_created': False,
            'limitations': ['CALLER_DEBIT_BOUNDS_NOT_VENUE_VERIFIED',
                            'ACCOUNT_OBSERVATION_NOT_ATOMIC', 'NO_PERSISTENT_RESERVATION']}
