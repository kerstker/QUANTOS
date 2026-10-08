"""Bind public gold observations and signal decisions without fabricating quotes."""
from copy import deepcopy

from services.execution.perpetual_public_replay import prepare_public_replay
from services.execution.perpetual_simulation import SimulationFrame
from tools.gold_cost_inputs import dump_inputs, load_inputs

SCHEMA = 'GOLD_PUBLIC_COST_ASSEMBLY_V1'
ALIGNMENT = 'EMPTY_DECISION_FRAME_NO_MARKET_UPDATE_V1'


def assemble_inputs(*, capture, document, plan, release_identity, initial_collateral,
                    costs, limits=None):
    capture, document = deepcopy((capture, document))
    if (type(capture) is not dict or capture.get('schema_version') != 'GOLD_PUBLIC_REPLAY_CAPTURE_V1'
            or capture.get('symbol') != 'XAUUSDT'):
        raise ValueError('GOLD_CAPTURE_REQUIRED')
    prepared = prepare_public_replay(capture)
    frames = prepared['frames']
    times = document.get('decision_times_ms') if type(document) is dict else None
    if (type(times) is not list or not 1 <= len(times) <= 1000
            or any(type(t) is not int for t in times) or times != sorted(set(times))):
        raise ValueError('INVALID_GOLD_DECISION_SCHEDULE')
    if any(not frames[0].timestamp <= t <= frames[-1].timestamp for t in times):
        raise ValueError('GOLD_DECISION_OUTSIDE_QUOTE_COVERAGE')
    by_time = {f.timestamp: f for f in frames}
    for t in times:
        # Original frames are preserved. An inserted clock tick carries neither
        # quotes nor marks nor capacity updates, so it cannot create a fill.
        if t not in by_time:
            by_time[t] = SimulationFrame(t)
    arguments = dict(document=document, plan=plan, release_identity=release_identity,
        market_identity={'schema': SCHEMA, 'capture': capture, 'manifest': prepared['manifest'],
                         'alignment': ALIGNMENT},
        frames=tuple(by_time[t] for t in sorted(by_time)),
        initial_collateral=initial_collateral, costs=costs, limits=limits,
        **{k: prepared[k] for k in ('contract_rules', 'funding_policies', 'order_rule_profiles')})
    return load_inputs(dump_inputs(arguments))


def verify_assembly(arguments):
    """Reconstruct declared public-derived inputs; this does not authenticate them."""
    identity = arguments['market_identity']
    if type(identity) is not dict or identity.get('schema') != SCHEMA:
        raise ValueError('GOLD_PUBLIC_ASSEMBLY_REQUIRED')
    expected = assemble_inputs(capture=identity['capture'],
        **{k: arguments[k] for k in ('document', 'plan', 'release_identity',
                                     'initial_collateral', 'costs', 'limits')})
    if dump_inputs(expected) != dump_inputs(arguments):
        raise ValueError('GOLD_PUBLIC_ASSEMBLY_BINDING_CHANGED')
