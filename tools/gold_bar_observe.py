"""Bounded public closed-kline journal feeding all three research hypotheses."""
import argparse
import asyncio
from contextlib import aclosing
import hashlib
import json
import os
from pathlib import Path
import time

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from services.research.gold_bar_dataset import load_bars
from services.research.gold_bar_stream import GoldBarStream
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.gold_cost_review import _read
from tools.gold_bar_capture import _write
from tools.perpetual_replay_research import source_identity

URL = 'wss://fstream.binance.com/market/stream?streams=xauusdt@kline_15m'
_RETRY_DELAYS = (0.1, 0.2, 0.4)
_LONG_RETRY_DELAYS = (0.25, 1, 2, 4, 8, 16, 30)


async def _messages(seconds, transport, connection_options=None, *, maximum_connections=None):
    """Retry only socket operations, within the caller's original deadline."""
    if maximum_connections is not None and (type(maximum_connections) is not int
                                           or not 1 <= maximum_connections <= 512):
        raise ValueError('BAR_CONNECTION_BUDGET')
    total = maximum_connections if maximum_connections is not None else 4
    for attempt in range(total):
        try:
            async with connect(URL, open_timeout=min(seconds, 15), close_timeout=1,
                               max_size=131072, max_queue=32, **(connection_options or {})) as socket:
                transport['connected'] = True
                while True:
                    message = await socket.recv()
                    yield message, time.time_ns() // 1_000_000
        except (ConnectionClosed, ConnectionError, TimeoutError) as exc:
            transport['connected'] = False
            transport['errors'].append(dict(attempt=attempt + 1,
                received_at_ms=time.time_ns() // 1_000_000, reason=type(exc).__name__))
            if attempt == total - 1:
                return
            delay = (_LONG_RETRY_DELAYS[min(attempt, len(_LONG_RETRY_DELAYS) - 1)]
                     if maximum_connections is not None else _RETRY_DELAYS[attempt])
            await asyncio.sleep(delay)


async def observe(history, output, *, seconds, extended=False, on_closed=None, stop_after_close_ms=None, protected_network=False):
    from quantos.contracts.gold_observation_window import EXTENDED_MAX_SECONDS, EXTENDED_STREAM_SCHEMA
    if (type(extended) is not bool or type(seconds) is not int
            or not 1 <= seconds <= (EXTENDED_MAX_SECONDS if extended else 1800)):
        raise ValueError('BAR_OBSERVATION_DURATION')
    if on_closed is not None and not callable(on_closed):
        raise ValueError('BAR_OBSERVATION_CALLBACK')
    if stop_after_close_ms is not None and (type(stop_after_close_ms) is not int
            or not 0 <= stop_after_close_ms <= 2**63-1 or extended):
        raise ValueError('BAR_FIRST_CLOSE_PLAN')
    if type(protected_network) is not bool:
        raise ValueError('BAR_OBSERVATION_NETWORK_MODE')
    connection_options = {}
    if protected_network:
        from live.exchange import demo_network
        connection_options = {'proxy':demo_network.proxy_url(), 'ssl':demo_network.tls_context()}
    history, output = Path(history), Path(output)
    raw = _read(history, 'response.json')
    receipt = load_json_without_duplicate_keys(_read(history, 'receipt.json'))
    if hashlib.sha256(raw).hexdigest() != receipt['response_sha256']:
        raise ValueError('BAR_WARMUP_CHANGED')
    cursor = GoldBarStream(load_bars(raw, requested_at_ms=receipt['requested_at_ms'],
                                   received_at_ms=receipt['received_at_ms']))
    output.mkdir(mode=0o700)
    source = source_identity()
    _write(output / 'plan.json', dict(source=source, seconds=seconds, url=URL,
        warmup_source_sha256=receipt['response_sha256'], submission_enabled=False,
        **({'extended': True} if extended else {}),
        **({'stop_after_close_ms': stop_after_close_ms} if stop_after_close_ms is not None else {}),
        **({'network_mode':'PROTECTED_DEMO_NETWORK_V1'} if protected_network else {})))
    with (output / 'warmup.json').open('xb') as handle:
        handle.write(raw)
    _write(output / 'warmup-receipt.json', receipt)
    count = closed = size = 0
    reason = 'STREAM_INTERRUPTED'
    cancelled = False
    transport = dict(connected=False, errors=[])
    timer = asyncio.timeout(seconds)
    with (output / 'messages.jsonl').open('x') as messages, (output / 'decisions.jsonl').open('x') as decisions:
        try:
            async with timer:
                async with aclosing(_messages(seconds, transport, connection_options)) as stream:
                    async for message, now in stream:
                        if type(message) is not str:
                            raise ValueError('NON_TEXT_BAR_STREAM')
                        count += 1
                        size += len(message.encode())
                        if count > 20000 or size > 32_000_000:
                            raise ValueError('BAR_STREAM_CAPTURE_BUDGET')
                        messages.write(json.dumps(dict(received_at_ms=now, raw=message)) + '\n')
                        messages.flush()
                        result = cursor.ingest(message, received_at_ms=now)
                        if result['status'] == 'CLOSED_BAR':
                            closed += 1
                            decisions.write(json.dumps(result, sort_keys=True) + '\n')
                            decisions.flush()
                            os.fsync(decisions.fileno())
                            if on_closed is not None:
                                on_closed(result)
                            if stop_after_close_ms is not None and result['close_ms'] > stop_after_close_ms:
                                reason = 'FIRST_NEW_CLOSE_COMPLETE'
                                break
                    else:
                        reason = 'TRANSPORT_RETRIES_EXHAUSTED'
        except asyncio.CancelledError:
            cancelled = True
            reason = 'STREAM_CANCELLED'
        except TimeoutError:
            reason = 'DURATION_COMPLETE' if timer.expired() else 'TRANSPORT_TIMEOUT'
        except Exception as exc:
            reason = 'STREAM_FAILED_' + type(exc).__name__
        messages.flush()
        os.fsync(messages.fileno())
    unchanged = source_identity() == source
    summary = dict(schema=EXTENDED_STREAM_SCHEMA if extended else 'GOLD_BAR_STREAM_SUMMARY_V1', reason=reason,
        status='OBSERVED' if reason in ('DURATION_COMPLETE', 'FIRST_NEW_CLOSE_COMPLETE') and count and unchanged
               and transport['connected'] else 'INCOMPLETE',
        messages=count, closed_bars=closed, source_unchanged=unchanged,
        transport_errors=transport['errors'], connected_at_deadline=transport['connected'],
        decisions_sha256=hashlib.sha256((output / 'decisions.jsonl').read_bytes()).hexdigest(),
        messages_sha256=hashlib.sha256((output / 'messages.jsonl').read_bytes()).hexdigest(),
        submission_enabled=False, net_pnl=None, economic_eligible=False)
    _write(output / 'summary.json', summary)
    if cancelled:
        raise asyncio.CancelledError
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=60)
    args = parser.parse_args(argv)
    result = asyncio.run(observe(args.history_dir, args.output_dir, seconds=args.seconds))
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'OBSERVED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
