"""Bounded raw public depth/trade/mark capture for passive execution research."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import sys
import time

from websockets.asyncio.client import connect

from services.data.binance_execution_tape import (
    ExecutionTapeError, MAX_EVENTS,
)
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest
from tools.perpetual_replay_research import source_identity
from tools.profitability_study import write_new

from services.execution.passive_execution_input import (
    # 2026-09-20 审计 P2-12:main() 的 argparse `choices=sorted(SYMBOLS)` 引用了
    # 一个本模块从未导入的名字,于是这个被 docs/runbooks/profitability-validation.md
    # 明确记录的操作命令**每次调用都会立刻 NameError 崩掉**。SYMBOLS 早已在
    # services.execution.binance_execution_tape 定义并由 passive_execution_input
    # 转出(stream_urls 也用它校验),所以正确修法是导入它——不是在本文件里另写一份
    # 符号表(那会制造第二个真相来源)。
    SCHEMA, MAX_RAW_BYTES, MAX_MESSAGE_BYTES, SYMBOLS, stream_urls, analyze,
)

def now_ms():
    return time.time_ns() // 1_000_000


async def _request_snapshot(symbol, timeout_seconds):
    stream_urls(symbol)
    url = f'https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=1000'
    child = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).with_name('profitability_public_http.py')),
        url, str(timeout_seconds), '--include-raw',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        stdout, _ = await asyncio.wait_for(child.communicate(), timeout_seconds)
        if child.returncode != 0 or len(stdout) > 1_500_000:
            raise ExecutionTapeError('SNAPSHOT_CHILD_FAILED')
        response = load_json_without_duplicate_keys(stdout)
        if response.get('ok') is not True:
            category = response.get('category')
            if category not in {'RESPONSE_TOO_LARGE', 'HTTP_ERROR', 'TLS_VERIFICATION_FAILED',
                                'TRANSPORT_ERROR', 'TIMEOUT', 'INVALID_RESPONSE'}:
                category = 'UNKNOWN_PUBLIC_RESPONSE'
            raise ExecutionTapeError(f'SNAPSHOT_{category}')
        raw = response.get('raw_utf8')
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_MESSAGE_BYTES:
            raise ExecutionTapeError('INVALID_SNAPSHOT_RESPONSE')
        return raw
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()


async def collect(symbol, *, duration_seconds=30, connector=None, snapshot_fetch=None,
                  clock=now_ms, max_events=MAX_EVENTS, max_bytes=MAX_RAW_BYTES, journal=None):
    urls = stream_urls(symbol)
    if (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (float, int))
            or not math.isfinite(duration_seconds) or not .01 <= duration_seconds <= 120
            or type(max_events) is not int or not 1 <= max_events <= MAX_EVENTS
            or type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_RAW_BYTES):
        raise ValueError('bounded capture duration, messages and bytes required')
    connector, snapshot_fetch = connector or connect, snapshot_fetch or _request_snapshot
    deadline = time.monotonic() + duration_seconds
    document = {'schema': SCHEMA, 'symbol': symbol, 'started_at_ms': clock(),
                'ended_at_ms': None, 'stream_urls': urls, 'snapshot': None,
                'messages': [], 'connections': {}, 'failures': [],
                'limits': {'duration_seconds': duration_seconds, 'max_events': max_events,
                           'max_raw_bytes': max_bytes}, 'economic_eligible': False}
    first_depth, stop = asyncio.Event(), asyncio.Event()
    used_bytes = 0
    emit = journal or (lambda row: None)
    emit({'kind': 'CAPTURE_BEGIN', 'symbol': symbol, 'started_at_ms': document['started_at_ms']})

    def failure(route, reason):
        document['failures'].append({'route': route, 'reason': reason, 'observed_at_ms': clock()})
        stop.set()

    async def read(route):
        nonlocal used_bytes
        record = {'status': 'CONNECTING', 'messages': 0, 'started_at_ms': clock()}
        document['connections'][route] = record
        try:
            async with connector(urls[route], open_timeout=max(.001, min(10, deadline-time.monotonic())),
                                 close_timeout=1, max_size=MAX_MESSAGE_BYTES, max_queue=256,
                                 ping_interval=20, ping_timeout=10) as socket:
                record['status'] = 'CONNECTED'
                record['connected_at_ms'] = clock()
                while not stop.is_set():
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(socket.recv(), min(.5, remaining))
                    except TimeoutError:
                        continue
                    received = clock()
                    if not isinstance(raw, str):
                        failure(route, 'NON_TEXT_PUBLIC_MESSAGE')
                        break
                    size = len(raw.encode('utf-8'))
                    if len(document['messages']) >= max_events:
                        failure(route, 'MESSAGE_BUDGET_EXHAUSTED')
                        break
                    if size > MAX_MESSAGE_BYTES or used_bytes+size > max_bytes:
                        failure(route, 'RAW_BYTE_BUDGET_EXHAUSTED')
                        break
                    message = {'sequence': len(document['messages']), 'route': route,
                               'received_at_ms': received, 'raw_utf8': raw}
                    emit({'kind': 'MESSAGE', 'event': message})
                    document['messages'].append(message)
                    used_bytes += size
                    record['messages'] += 1
                    if route == 'public':
                        first_depth.set()
                record['status'] = 'STOPPED' if stop.is_set() else 'DURATION_REACHED'
        except asyncio.CancelledError:
            record['status'] = 'CANCELLED'
            raise
        except Exception as exc:
            # Transport exceptions can contain proxy configuration: retain only the type.
            failure(route, 'PUBLIC_STREAM_'+type(exc).__name__)
            record['status'] = 'FAILED'
        finally:
            record['closed_at_ms'] = clock()

    tasks = [asyncio.create_task(read(route)) for route in ('public', 'market')]
    try:
        try:
            await asyncio.wait_for(first_depth.wait(), max(.001, min(10, deadline-time.monotonic())))
            if not stop.is_set():
                remaining = max(.001, min(10, deadline-time.monotonic()))
                raw = await asyncio.wait_for(snapshot_fetch(symbol, remaining), remaining)
                document['snapshot'] = {'received_at_ms': clock(), 'raw_utf8': raw}
                emit({'kind': 'SNAPSHOT', 'event': document['snapshot']})
        except TimeoutError:
            failure('snapshot', 'SNAPSHOT_TIMEOUT')
        except Exception as exc:
            failure('snapshot', str(exc) if isinstance(exc, ExecutionTapeError) else 'SNAPSHOT_'+type(exc).__name__)
        await asyncio.gather(*tasks)
    finally:
        stop.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        document['ended_at_ms'] = clock()
        emit({'kind': 'CAPTURE_END', 'ended_at_ms': document['ended_at_ms'],
              'connections': document['connections'], 'failures': document['failures']})
    return document


def capture(output, symbol, **kwargs):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    duration = kwargs.get('duration_seconds', 30)
    plan = {'schema': 'PASSIVE_PUBLIC_CAPTURE_PLAN_V1', 'symbol': symbol, 'source': source_identity(),
            'duration_seconds': duration, 'streams': stream_urls(symbol), 'created_at_ms': now_ms(),
            'max_events': kwargs.get('max_events', MAX_EVENTS), 'max_raw_bytes': kwargs.get('max_bytes', MAX_RAW_BYTES),
            'snapshot_requests': 1, 'economic_eligible': False, 'scope': 'PUBLIC_DATA_ONLY'}
    write_new(output/'preregistration.json', plan)
    try:
        if 'journal' in kwargs:
            raise ValueError('capture owns its immutable raw journal')
        with (output/'raw-events.jsonl').open('x', encoding='utf-8') as raw_journal:
            def journal(row):
                raw_journal.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
                raw_journal.flush()
            try:
                document = asyncio.run(collect(symbol, journal=journal, **kwargs))
            finally:
                raw_journal.flush()
                os.fsync(raw_journal.fileno())
        write_new(output/'raw-tape.json', document)
        result = analyze(document)
        result['source'] = plan['source']
        result['preregistration_hash'] = digest(plan)
        write_new(output/'result.json', result)
        return result
    except BaseException as exc:
        write_new(output/'failure.json', {'stage': 'P5_INPUT', 'status': 'FAILED',
                  'reason': type(exc).__name__, 'raw_journal': 'raw-events.jsonl', 'economic_eligible': False})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--symbol', choices=sorted(SYMBOLS), required=True)
    parser.add_argument('--duration-seconds', type=float, default=30)
    args = parser.parse_args()
    result = capture(args.output, args.symbol, duration_seconds=args.duration_seconds)
    print(json.dumps({'status': result['status'], 'counts': result['counts'],
                      'failures': result['failures'], 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
