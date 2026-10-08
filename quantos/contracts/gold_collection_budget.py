"""Explicit public observation resources; never research or trading authority."""
from copy import deepcopy

SCHEMA = 'GOLD_PUBLIC_COLLECTION_BUDGET_V1'
MAXIMUM_SECONDS = 32 * 86400
LIMITS = {
    'maximum_duration_seconds': MAXIMUM_SECONDS,
    'maximum_quote_bytes': 64 * 1024**3,
    'maximum_quote_receipts': 8_000_000,
    'maximum_quote_segments': 4096,
    'maximum_bar_bytes': 16 * 1024**3,
    'maximum_bar_messages': 16_000_000,
    'maximum_bar_connections': 512,
}


def validate(value):
    if (type(value) is not dict or set(value) != {'schema', *LIMITS}
            or value.get('schema') != SCHEMA
            or any(type(value.get(name)) is not int or not 1 <= value[name] <= maximum
                   for name, maximum in LIMITS.items())):
        raise ValueError('INVALID_GOLD_COLLECTION_BUDGET')
    return deepcopy(value)


def for_duration(value, seconds, *, minimum=30):
    budget = validate(value) if value is not None else None
    maximum = budget['maximum_duration_seconds'] if budget else 86400
    if type(seconds) is not int or not minimum <= seconds <= maximum:
        raise ValueError('GOLD_COLLECTION_DURATION_BUDGET')
    return budget


def plan_budget(plan, *, legacy_schema, budget_schema, minimum=30):
    if plan.get('schema') == legacy_schema:
        if 'collection_budget' in plan:
            raise ValueError('GOLD_COLLECTION_BUDGET_DOWNGRADE')
        return for_duration(None, plan.get('seconds'), minimum=minimum)
    if plan.get('schema') != budget_schema or 'collection_budget' not in plan:
        raise ValueError('GOLD_COLLECTION_PLAN_SCHEMA')
    budget = validate(plan['collection_budget'])
    return for_duration(budget, plan.get('seconds'), minimum=minimum)
