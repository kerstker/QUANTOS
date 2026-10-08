"""Validated closed-bar journal plus contemporaneous original public quotes."""
from services.execution.perpetual_public_replay import prepare_public_replay
from services.execution.perpetual_simulation import SimulationFrame
from tools.gold_bar_cost_replay import _compare_bars
from tools.gold_bar_journal import restore, bind_capture
from quantos.contracts.gold_observation_window import EXTENDED_CAPTURE_SCHEMA


def _prepare_observed(journal, capture, observed_funding_records, settlement_window_ms):
    cursor, review = restore(journal)
    bind_capture(review, capture, observed_funding_records)
    if (capture.get('schema_version') not in ('GOLD_PUBLIC_REPLAY_CAPTURE_V1', 'GOLD_PUBLIC_CONTINUOUS_CAPTURE_V1', EXTENDED_CAPTURE_SCHEMA, 'GOLD_PUBLIC_SESSION_PREFIX_V1')
            or capture.get('symbol') != 'XAUUSDT'):
        raise ValueError('GOLD_OBSERVATION_MARKET_SCOPE')
    prepared = prepare_public_replay(capture, observed_funding_records=observed_funding_records,
                                     settlement_window_ms=settlement_window_ms)
    count = review['closed_bars']
    if not count:
        raise ValueError('NO_CLOSED_BAR_FOR_QUOTE_COVERAGE')
    warmup, observed = cursor.bars[:-count], cursor.bars[-count:]
    if capture.get('schema_version') == EXTENDED_CAPTURE_SCHEMA:
        if [bar.close_ms for bar in observed] != capture['window']['close_times_ms']:
            raise ValueError('EXTENDED_JOURNAL_CLOSE_WINDOW_MISMATCH')
    frames = prepared['frames']
    times = [bar.available_ms for bar in observed]
    if (not warmup or max(b.available_ms for b in warmup) > frames[0].timestamp
            or any(not frames[0].timestamp <= t <= frames[-1].timestamp for t in times)):
        raise ValueError('CLOSED_BAR_OUTSIDE_CONTEMPORANEOUS_QUOTE_COVERAGE')
    by_time = {frame.timestamp: frame for frame in frames}
    for timestamp in times:
        # A decision clock contains no fabricated quote, mark or capacity refresh.
        if timestamp not in by_time:
            by_time[timestamp] = SimulationFrame(timestamp)
    prepared['frames'] = tuple(by_time[t] for t in sorted(by_time))
    identity = dict(schema='GOLD_OBSERVED_BAR_QUOTE_BINDING_V1',
        journal_hashes=review['hashes'], journal_source=review['source'],
        quote_manifest=prepared['manifest'], closed_decision_times=times,
        evaluated_frames=len(prepared['frames']))
    return cursor, review, prepared, identity, times


def compare_observed(*, journal, capture, release_identity, quantity, initial_collateral, fee_rate,
                     observed_funding_records=None, settlement_window_ms=None, history=None):
    cursor, review, prepared, identity, times = _prepare_observed(
        journal, capture, observed_funding_records, settlement_window_ms)
    if 'execution_source' in review and review['execution_source'] != release_identity:
        raise ValueError('BAR_PREFIX_EXECUTION_SOURCE_CHANGED')
    result = _compare_bars(bars=cursor.bars, prepared=prepared, dataset_identity=identity,
        release_identity=release_identity, quantity=quantity,
        initial_collateral=initial_collateral, fee_rate=fee_rate,
        entry_not_before_ms=min(times), incremental_bars=True, history=history)
    _, after = restore(journal)
    if after != review:
        raise ValueError('BAR_JOURNAL_CHANGED_DURING_COMPARISON')
    return result



def compare_observed_portfolio(*, journal, capture, release_identity, quantities,
                               initial_collateral, fee_rate, max_notional,
                               observed_funding_records=None, settlement_window_ms=None, history=None):
    from services.research.gold_net_portfolio import compare_portfolio
    cursor, review, prepared, identity, times = _prepare_observed(
        journal, capture, observed_funding_records, settlement_window_ms)
    if 'execution_source' in review and review['execution_source'] != release_identity:
        raise ValueError('BAR_PREFIX_EXECUTION_SOURCE_CHANGED')
    result = compare_portfolio(bars=cursor.bars, prepared=prepared,
        release_identity=release_identity, quantities=quantities,
        initial_collateral=initial_collateral, fee_rate=fee_rate, max_notional=max_notional,
        entry_not_before_ms=min(times), observation_binding=identity, incremental_bars=True,
        history=history)
    _, after = restore(journal)
    if after != review:
        raise ValueError('BAR_JOURNAL_CHANGED_DURING_COMPARISON')
    return result
