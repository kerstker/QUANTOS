"""Append received Gold bars and causal epochs on the original session."""
from bisect import bisect_left
from itertools import groupby, islice

from services.execution.perpetual_simulation import LinearPerpetualSimulationSession


def execute(configuration, strategy, bars, initial_bars, *, history=None):
    config = dict(configuration)
    frames = config.pop('frames')
    updates = config.pop('environment_updates', None)
    session = (LinearPerpetualSimulationSession(**config) if history is None
               else history.new_session(config, strategy=strategy))
    if history is not None:
        return _execute_stream(session, strategy, frames, updates, bars, initial_bars, history)
    times = [frame.timestamp for frame in frames]
    boundaries = {} if updates is None else {u['first_frame']: u for u in updates}
    if updates is not None and (len(boundaries) != len(updates)
            or any(type(i) is not int or not 0 < i < len(frames) for i in boundaries)):
        raise ValueError('INVALID_GOLD_ENVIRONMENT_BOUNDARIES')
    append = {}
    for available, group in groupby(bars[len(initial_bars):], key=lambda b: b.available_ms):
        if available > times[-1]:
            break
        index = bisect_left(times, available)
        append[index] = (*append.get(index, ()), *tuple(group))
    events = sorted({0, len(frames), *append, *boundaries})
    for index, end in zip(events, events[1:]):
        if index in append:
            strategy.append_bars(append[index])
        if index in boundaries:
            update = {k: v for k, v in boundaries[index].items() if k != 'first_frame'}
            if history is None:
                session.advance_environment(frames[index:end], **update)
            else:
                history.advance(session, frames[index:end], environment=update)
        else:
            if history is None:
                session.advance(frames[index:end])
            else:
                history.advance(session, frames[index:end])
    return session.report() if history is None else history.result(session)


def _execute_stream(session, strategy, frames, updates, bars, initial_bars, history):
    """Merge causal bar receipts with frames using one bounded account batch."""
    boundaries = {} if updates is None else {u['first_frame']: u for u in updates}
    if updates is not None and (len(boundaries) != len(updates)
            or any(type(i) is not int or not 0 < i < len(frames) for i in boundaries)):
        raise ValueError('INVALID_GOLD_ENVIRONMENT_BOUNDARIES')
    incoming = iter(islice(bars, len(initial_bars), None))
    bar = next(incoming, None)
    batch = []
    environment = None

    def flush():
        nonlocal batch, environment
        if batch:
            history.advance(session, batch, environment=environment)
            batch = []
            environment = None

    for index, frame in enumerate(frames):
        if (bar is not None and bar.available_ms <= frame.timestamp) or index in boundaries:
            flush()
            received = []
            while bar is not None and bar.available_ms <= frame.timestamp:
                received.append(bar)
                bar = next(incoming, None)
            if received:
                strategy.append_bars(tuple(received))
            if index in boundaries:
                environment = {k: v for k, v in boundaries[index].items() if k != 'first_frame'}
        batch.append(frame)
        if len(batch) == 4096:
            flush()
    flush()
    return history.result(session)
