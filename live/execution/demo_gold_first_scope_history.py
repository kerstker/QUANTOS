"""Bind the original verified issuer prefix across a first Scope installation.

Rows must come from IssuerStore.snapshot(), which verifies signatures, budgets
and the claim index. These helpers bind that original sequence; they do not
authenticate a database, operator, source or an unretained past on their own.
"""
from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_common import require
from services.validation import demo_submission as submission

SCHEMA = 'DEMO_GOLD_FIRST_SCOPE_ISSUER_PREFIX_V1'


def validate_anchor(anchor):
    _fields(anchor, 'schema records head_sha256 history_sha256')
    require(anchor['schema'] == SCHEMA and type(anchor['records']) is int
        and 2 <= anchor['records'] <= 20000, 'FIRST_SCOPE_ISSUER_ANCHOR')
    submission._digest(anchor['head_sha256'])
    submission._digest(anchor['history_sha256'])
    return anchor


def history_anchor(rows):
    """Summarize the whole original issuer, including unsigned reservations."""
    require(type(rows) is list and 2 <= len(rows) <= 20000
        and any(row['kind'] == 'SIGNED' for row in rows), 'FIRST_SCOPE_ORIGINAL_ISSUER_REQUIRED')
    previous = ''
    for sequence, row in enumerate(rows, 1):
        _fields(row, 'seq kind proposal_sha256 at_ms payload prev_hash record_hash')
        require(row['seq'] == sequence and row['prev_hash'] == previous
            and _digest({key:value for key,value in row.items() if key != 'record_hash'}) == row['record_hash'],
            'FIRST_SCOPE_ISSUER_PREFIX_CHAIN')
        previous = row['record_hash']
    return validate_anchor(dict(schema=SCHEMA, records=len(rows), head_sha256=previous,
        history_sha256=_digest(rows)))


def verify_prefix(anchor, rows):
    """Require the independently retained original prefix; later rows may grow."""
    validate_anchor(anchor)
    require(type(rows) is list and len(rows) >= anchor['records'], 'FIRST_SCOPE_ISSUER_PREFIX_MISSING')
    require(history_anchor(rows[:anchor['records']]) == anchor, 'FIRST_SCOPE_ISSUER_PREFIX_CHANGED')
    return anchor
