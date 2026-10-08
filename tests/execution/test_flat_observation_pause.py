from decimal import Decimal as D

import pytest

from services.execution.perpetual_simulation import SimulationFrame, OrderIntent
from tests.execution.test_perpetual_simulation import T, frame, run


def test_flat_pause_preserves_cash_and_suppresses_callbacks_until_fresh_resume():
    seen = []
    result = run((frame(0), SimulationFrame(T+1, observation_pause_until=T+50),
                  SimulationFrame(T+25), frame(50), frame(60)),
                 strategy=lambda ctx: seen.append(ctx.timestamp) or ())
    assert seen == [T, T+50, T+60]
    assert result['reconciliation']['initial_equity'] == '1000'
    assert result['reconciliation']['ending_equity'] == '1000'
    assert result['reconciliation']['residual'] == '0'


@pytest.mark.parametrize('filled', [False, True])
def test_pause_rejects_pending_orders_or_positions_instead_of_erasing_them(filled):
    tape = [frame(0)]
    if filled:
        tape.append(frame(10))
    tape.extend((SimulationFrame(T+11, observation_pause_until=T+50), frame(50)))
    with pytest.raises(ValueError, match='OBSERVATION_PAUSE_REQUIRES_FLAT_ACCOUNT'):
        run(tape, strategy=lambda ctx: (OrderIntent('entry', 'BTCUSDT', D('.1')),)
            if ctx.timestamp == T else ())


def test_pause_requires_fresh_quote_and_mark_at_resume():
    with pytest.raises(ValueError, match='OBSERVATION_RESUME_REQUIRES_FRESH_MARKET'):
        run((frame(0), SimulationFrame(T+1, observation_pause_until=T+50), SimulationFrame(T+50)))


def test_report_during_unfinished_pause_is_unavailable():
    with pytest.raises(ValueError, match='OBSERVATION_PAUSE_PENDING'):
        run((frame(0), SimulationFrame(T+1, observation_pause_until=T+50), SimulationFrame(T+25)))


def test_public_capture_requires_explicit_gap_policy_and_retains_missing_interval():
    from tests.execution.test_perpetual_public_replay import gold_capture_fixture, sealed
    from services.execution.perpetual_public_replay import prepare_public_replay

    doc = gold_capture_fixture()
    doc['schema_version'] = 'GOLD_PUBLIC_SESSION_PREFIX_V1'
    for sample in doc['samples'][2:]:
        for kind in ('mark', 'depth'):
            sample[kind]['requested_at'] += 40000
            sample[kind]['received_at'] += 40000
        sample['mark']['payload']['time'] += 40000
        sample['depth']['payload']['T'] += 40000
        sample['depth']['payload']['E'] += 40000
    doc['exchange_after']['requested_at'] += 40000
    doc['exchange_after']['received_at'] += 40000
    with pytest.raises(ValueError, match='noncausal, stale'):
        prepare_public_replay(sealed(doc))
    doc['observation_gap_policy'] = 'FLAT_ACCOUNT_PAUSE_V1'
    result = prepare_public_replay(sealed(doc))
    gap = result['manifest']['observation_gaps'][0]
    assert gap['pause_at_ms'] == doc['samples'][1]['depth']['received_at']+1
    assert gap['resume_at_ms'] == doc['samples'][2]['depth']['received_at']
    assert next(f for f in result['frames'] if f.timestamp == gap['pause_at_ms']).observation_pause_until == gap['resume_at_ms']


def test_existing_account_cannot_enable_gap_policy_after_observing_a_failure(tmp_path, monkeypatch):
    from tests.tools.test_gold_account_chain import setup
    from tools.gold_account_chain import advance

    args = setup(tmp_path, monkeypatch)
    advance(tmp_path/'chain', tmp_path/'first', **args)
    with pytest.raises(ValueError, match='ACCOUNT_CHAIN_CONFIGURATION_CHANGED'):
        advance(tmp_path/'chain', tmp_path/'full', allow_flat_observation_gaps=True, **args)
    assert not (tmp_path/'chain/000002').exists()
