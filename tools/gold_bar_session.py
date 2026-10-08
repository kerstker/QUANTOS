"""Bounded continuous public K-line journal, independent of research workers."""
import argparse
import asyncio
import base64
from contextlib import aclosing
import hashlib
import json
import os
from pathlib import Path

from quantos.contracts.gold_collection_budget import for_duration, plan_budget
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.research.gold_bar_dataset import load_bars
from services.research.gold_bar_candidates import INTERVAL_MS
from services.research.gold_bar_stream import GoldBarStream
from tools.gold_bar_capture import _write
from tools.gold_bar_observe import _messages, URL
from tools.gold_cost_review import _read
from tools.perpetual_replay_research import source_identity

CHUNK_MESSAGES = 10000
CHUNK_BYTES = 2_000_000
MAX_MESSAGES = 500000
MAX_BYTES = 512 * 1024 * 1024
SCHEMA = 'GOLD_CONTINUOUS_BAR_SESSION_V1'
BUDGETED_SCHEMA = 'GOLD_CONTINUOUS_BAR_SESSION_V2'
RETAINED_SCHEMA = 'GOLD_CONTINUOUS_BAR_SESSION_V3'
LATE_OPEN_POLICY = 'RETAIN_LATE_OPEN_DIAGNOSTICS_V1'


def _policy(value):
    if value is not None and (type(value) is not str or value != LATE_OPEN_POLICY):
        raise ValueError('BAR_SESSION_LATE_OPEN_POLICY')
    return value


def bar_plan_budget(plan):
    retained = plan.get('schema') == RETAINED_SCHEMA
    budget = plan_budget(plan, legacy_schema=SCHEMA,
        budget_schema=RETAINED_SCHEMA if retained else BUDGETED_SCHEMA, minimum=1)
    if retained:
        if _policy(plan.get('late_open_policy')) != LATE_OPEN_POLICY or budget is None:
            raise ValueError('BAR_SESSION_LATE_OPEN_POLICY_REQUIRED')
    elif 'late_open_policy' in plan:
        raise ValueError('BAR_SESSION_LATE_OPEN_POLICY_DOWNGRADE')
    return budget


class _RetainedBarCursor:
    """A raw receipt cursor; rejected wires never enter the decision cursor."""
    def __init__(self, stream):
        self._stream = stream
        self.last_received = stream.last_received
        self.blocked = False

    @property
    def bars(self):
        return self._stream.bars

    def ingest(self, raw, *, received_at_ms):
        if self.blocked or self._stream.blocked:
            raise ValueError('BAR_STREAM_BLOCKED')
        try:
            if (type(raw) is not str or len(raw) > 131072 or type(received_at_ms) is not int
                    or not self.last_received <= received_at_ms <= 2**63 - 1):
                raise ValueError('INVALID_BAR_STREAM_RECEIPT')
            document = decode(raw.encode())
            if (type(document) is dict and set(document) == {'stream', 'data'}
                    and document['stream'] == 'xauusdt@kline_15m'):
                data = document['data']
                if (type(data) is dict and data.get('e') == 'kline' and data.get('s') == 'XAUUSDT'
                        and type(data.get('E')) is int and 0 <= data['E'] < received_at_ms - 30000):
                    k = data.get('k')
                    if (type(k) is dict and k.get('s') == 'XAUUSDT' and k.get('i') == '15m'
                            and k.get('x') is False):
                        self.last_received = received_at_ms
                        return dict(status='REJECTED_LATE_OPEN_BAR', received_at_ms=received_at_ms,
                            event_ms=data['E'], delay_ms=received_at_ms-data['E'],
                            reason='INVALID_BAR_EVENT_CLOCK_OR_SYMBOL', submission_enabled=False,
                            authoritative=False)
            result = self._stream.ingest(raw, received_at_ms=received_at_ms)
            self.last_received = received_at_ms
            return result
        except (ValueError, TypeError, KeyError):
            self.blocked = True
            raise


def _encode(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False)+'\n').encode()


def _cursor(raw, receipt, *, late_open_policy=None):
    if hashlib.sha256(raw).hexdigest() != receipt['response_sha256']:
        raise ValueError('BAR_SESSION_WARMUP_HASH')
    policy = _policy(late_open_policy)
    stream = GoldBarStream(load_bars(raw, requested_at_ms=receipt['requested_at_ms'],
                                    received_at_ms=receipt['received_at_ms']))
    return _RetainedBarCursor(stream) if policy is not None else stream


async def observe(history, output, *, seconds=86400, collection_budget=None, late_open_policy=None):
    budget = for_duration(collection_budget, seconds, minimum=1)
    policy = _policy(late_open_policy)
    if policy is not None and budget is None:
        raise ValueError('BAR_SESSION_LATE_OPEN_BUDGET_REQUIRED')
    schema = RETAINED_SCHEMA if policy else BUDGETED_SCHEMA if budget else SCHEMA
    maximum_messages = budget['maximum_bar_messages'] if budget else MAX_MESSAGES
    maximum_bytes = budget['maximum_bar_bytes'] if budget else MAX_BYTES
    history, output = Path(history), Path(output)
    raw = _read(history, 'response.json')
    receipt = decode(_read(history, 'receipt.json'))
    cursor = _cursor(raw, receipt, late_open_policy=policy)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = source_identity()
    (output/'warmup.json').write_bytes(raw)
    _write(output/'warmup-receipt.json', receipt)
    extra = dict(collection_budget=budget) if budget else {}
    if policy is not None:
        extra['late_open_policy'] = policy
    _write(output/'plan.json', dict(schema=schema, seconds=seconds, source=source,
                                   url=URL, submission_enabled=False, **extra))
    chunks = []
    handle = None
    chunk_count = chunk_bytes = count = total = closed = missing = 0
    digest = None
    previous_close = cursor.bars[-1].close_ms if cursor.bars else None
    first_received = last_received = None
    rejected_hash = None
    late_open_count = 0

    def reject(message, received, exc):
        nonlocal rejected_hash
        if budget is None:
            return
        if type(message) is str and len(message) <= 131072:
            original = dict(raw=message)
        elif type(message) is bytes and len(message) <= 131072:
            original = dict(raw_base64=base64.b64encode(message).decode(), raw_encoding='BINARY')
        else:
            original = dict(raw_not_retained=True, raw_type=type(message).__name__)
        raw = _encode(dict(schema='GOLD_REJECTED_BAR_WIRE_V1', **original,
            received_at_ms=received, error_type=type(exc).__name__, error=str(exc)[:512],
            collection_budget=budget, submission_enabled=False))
        if len(raw) > CHUNK_BYTES:
            raise ValueError('BAR_SESSION_REJECTED_MESSAGE_BUDGET')
        with (output / 'rejected-message.json').open('xb') as handle:
            os.chmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        rejected_hash = hashlib.sha256(raw).hexdigest()

    def seal():
        nonlocal handle
        if handle is None:
            return
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        chunks.append(dict(index=len(chunks), messages=chunk_count,
                           bytes=chunk_bytes, sha256=digest.hexdigest()))
        # Sealed chunks remain usable evidence even if later collection fails.
        _write(output/f'chunk-{len(chunks)-1:04d}.json', chunks[-1])
        handle = None

    transport = dict(connected=False, errors=[])
    reason = 'TRANSPORT_RETRIES_EXHAUSTED'
    cancelled = False
    timer = asyncio.timeout(seconds)
    try:
        async with timer:
            options = dict(maximum_connections=budget['maximum_bar_connections']) if budget else {}
            async with aclosing(_messages(seconds, transport, **options)) as stream:
                async for message, received in stream:
                    try:
                        decision = cursor.ingest(message, received_at_ms=received)
                    except (ValueError, TypeError, KeyError) as exc:
                        reject(message, received, exc)
                        raise
                    row = _encode(dict(raw=message, received_at_ms=received, decision=decision))
                    if count >= maximum_messages or total + len(row) > maximum_bytes:
                        exc = ValueError('BAR_SESSION_BUDGET')
                        reject(message, received, exc)
                        raise exc
                    if handle is not None and (chunk_count >= CHUNK_MESSAGES
                                               or chunk_bytes + len(row) > CHUNK_BYTES):
                        seal()
                    if handle is None:
                        handle = (output/f'messages-{len(chunks):04d}.jsonl').open('xb')
                        chunk_count = chunk_bytes = 0
                        digest = hashlib.sha256()
                    handle.write(row)
                    handle.flush()
                    digest.update(row)
                    count += 1
                    late_open_count += decision['status'] == 'REJECTED_LATE_OPEN_BAR'
                    total += len(row)
                    chunk_count += 1
                    chunk_bytes += len(row)
                    first_received = first_received if first_received is not None else received
                    last_received = received
                    if decision['status'] == 'CLOSED_BAR':
                        closed += 1
                        if previous_close is not None:
                            missing += max(0, (decision['close_ms']-previous_close)//INTERVAL_MS-1)
                        previous_close = decision['close_ms']
                        os.fsync(handle.fileno())
    except asyncio.CancelledError:
        cancelled = True
        reason = 'CANCELLED'
    except TimeoutError:
        reason = 'DURATION_COMPLETE' if timer.expired() else 'TRANSPORT_TIMEOUT'
    except Exception as exc:
        reason = 'FAILED_'+type(exc).__name__
    finally:
        seal()
    unchanged = source_identity() == source
    manifest = dict(schema=schema, chunks=chunks, **extra,
        warmup_sha256=hashlib.sha256(raw).hexdigest(),
        receipt_sha256=hashlib.sha256(_read(output,'warmup-receipt.json')).hexdigest(),
        plan_sha256=hashlib.sha256(_read(output,'plan.json')).hexdigest())
    _write(output/'manifest.json', manifest)
    summary = dict(schema=schema, status='OBSERVED' if reason=='DURATION_COMPLETE'
        and count and transport['connected'] and unchanged else 'INCOMPLETE',
        reason=reason, messages=count, closed_bars=closed, missing_closed_bars=missing,
        chunks=len(chunks), bytes=total, source_unchanged=unchanged, transport=transport,
        manifest_sha256=hashlib.sha256(_read(output,'manifest.json')).hexdigest(),
        submission_enabled=False, economic_eligible=False, replay_ready=False, **extra)
    if budget is not None:
        summary.update(first_received_at_ms=first_received, last_received_at_ms=last_received,
                       rejected_message_sha256=rejected_hash)
    if policy is not None:
        summary['rejected_late_open_messages'] = late_open_count
    _write(output/'summary.json', summary)
    if cancelled:
        raise asyncio.CancelledError
    return summary


def restore(directory):
    root = Path(directory).absolute()
    summary_raw = _read(root,'summary.json')
    summary = decode(summary_raw)
    manifest_raw = _read(root,'manifest.json')
    manifest = decode(manifest_raw)
    if (summary.get('schema') not in (SCHEMA, BUDGETED_SCHEMA, RETAINED_SCHEMA) or summary.get('status') != 'OBSERVED'
            or summary.get('reason') != 'DURATION_COMPLETE'
            or summary.get('source_unchanged') is not True
            or summary.get('submission_enabled') is not False):
        raise ValueError('BAR_SESSION_INCOMPLETE')
    if hashlib.sha256(manifest_raw).hexdigest() != summary['manifest_sha256']:
        raise ValueError('BAR_SESSION_MANIFEST_HASH')
    data = {name:_read(root,name) for name in ('warmup.json','warmup-receipt.json','plan.json')}
    for name, field in [('warmup.json','warmup_sha256'),('warmup-receipt.json','receipt_sha256'),('plan.json','plan_sha256')]:
        if hashlib.sha256(data[name]).hexdigest() != manifest[field]:
            raise ValueError('BAR_SESSION_INPUT_HASH')
    plan = decode(data['plan.json'])
    budget = bar_plan_budget(plan)
    policy = plan.get('late_open_policy')
    schema = RETAINED_SCHEMA if policy else BUDGETED_SCHEMA if budget else SCHEMA
    maximum_messages = budget['maximum_bar_messages'] if budget else MAX_MESSAGES
    maximum_bytes = budget['maximum_bar_bytes'] if budget else MAX_BYTES
    if any(document.get('collection_budget') != budget if budget else 'collection_budget' in document
           for document in (summary, manifest)):
        raise ValueError('BAR_SESSION_BUDGET_BINDING')
    if any(document.get('late_open_policy') != policy if policy else 'late_open_policy' in document
           for document in (summary, manifest)):
        raise ValueError('BAR_SESSION_LATE_OPEN_POLICY_BINDING')
    if budget is not None and any(type(summary.get(name)) is not int or summary[name] < 0
            for name in ('messages', 'bytes', 'closed_bars', 'missing_closed_bars', 'chunks')):
        raise ValueError('BAR_SESSION_REPLAY_COUNTS')
    if (manifest.get('schema') != schema or summary.get('schema') != schema
            or plan.get('url') != URL or plan.get('submission_enabled') is not False
            or type(manifest.get('chunks')) is not list or not 1 <= len(manifest['chunks']) <= maximum_messages):
        raise ValueError('BAR_SESSION_PLAN')
    cursor = _cursor(data['warmup.json'], decode(data['warmup-receipt.json']), late_open_policy=policy)
    count = closed = total = missing = 0
    late_open_count = 0
    first_received = last_received = None
    chunk_metadata = {}
    previous_close = cursor.bars[-1].close_ms if cursor.bars else None
    for index, chunk in enumerate(manifest['chunks']):
        raw = _read(root,f'messages-{index:04d}.jsonl')
        if (any(type(chunk.get(k)) is not int for k in ('index','messages','bytes'))
                or not 1 <= chunk['messages'] <= CHUNK_MESSAGES
                or not 1 <= chunk['bytes'] <= CHUNK_BYTES
                or chunk['index'] != index or hashlib.sha256(raw).hexdigest() != chunk['sha256']
                or len(raw) != chunk['bytes']):
            raise ValueError('BAR_SESSION_CHUNK_HASH')
        if budget is not None:
            name = f'chunk-{index:04d}.json'
            chunk_metadata[name] = _read(root, name)
            if decode(chunk_metadata[name]) != chunk:
                raise ValueError('BAR_SESSION_CHUNK_METADATA_CHANGED')
        lines = raw.splitlines()
        count += len(lines)
        total += len(raw)
        if len(lines) != chunk['messages'] or count > maximum_messages or total > maximum_bytes:
            raise ValueError('BAR_SESSION_REPLAY_BUDGET')
        for line in lines:
            row = decode(line)
            observed = cursor.ingest(row['raw'], received_at_ms=row['received_at_ms'])
            late_open_count += observed['status'] == 'REJECTED_LATE_OPEN_BAR'
            first_received = first_received if first_received is not None else row['received_at_ms']
            last_received = row['received_at_ms']
            if observed != row['decision']:
                raise ValueError('BAR_SESSION_DECISION_MISMATCH')
            if observed['status']=='CLOSED_BAR':
                closed += 1
                if previous_close is not None:
                    missing += max(0,(observed['close_ms']-previous_close)//INTERVAL_MS-1)
                previous_close = observed['close_ms']
    if (count != summary['messages'] or total != summary['bytes'] or closed != summary['closed_bars']
            or missing != summary['missing_closed_bars'] or len(manifest['chunks']) != summary['chunks']):
        raise ValueError('BAR_SESSION_REPLAY_COUNTS')
    if policy is not None and (type(summary.get('rejected_late_open_messages')) is not int
            or summary['rejected_late_open_messages'] != late_open_count):
        raise ValueError('BAR_SESSION_REJECTED_LATE_OPEN_COUNTS')
    if budget is not None:
        if (summary.get('first_received_at_ms') != first_received
                or summary.get('last_received_at_ms') != last_received
                or summary.get('rejected_message_sha256') is not None):
            raise ValueError('BAR_SESSION_REPLAY_COVERAGE')
        expected = {'plan.json', 'manifest.json', 'summary.json', 'warmup.json', 'warmup-receipt.json',
                    *chunk_metadata, *(f'messages-{i:04d}.jsonl' for i in range(len(manifest['chunks'])))}
        with os.scandir(root) as members:
            for member in members:
                if (member.name not in expected or not member.is_file(follow_symlinks=False)
                        or member.stat(follow_symlinks=False).st_nlink != 1):
                    raise ValueError('BAR_SESSION_EXTRA_OR_ALIASED_ORIGINAL')
                expected.remove(member.name)
        if expected:
            raise ValueError('BAR_SESSION_MISSING_ORIGINAL')
    for index, chunk in enumerate(manifest['chunks']):
        if hashlib.sha256(_read(root,f'messages-{index:04d}.jsonl')).hexdigest() != chunk['sha256']:
            raise ValueError('BAR_SESSION_CHANGED_DURING_REVIEW')
    if any(_read(root,name) != raw for name, raw in data.items()):
        raise ValueError('BAR_SESSION_CHANGED_DURING_REVIEW')
    if any(_read(root, name) != raw for name, raw in chunk_metadata.items()):
        raise ValueError('BAR_SESSION_CHANGED_DURING_REVIEW')
    if (_read(root,'summary.json') != summary_raw or _read(root,'manifest.json') != manifest_raw):
        raise ValueError('BAR_SESSION_CHANGED_DURING_REVIEW')
    result = dict(schema='GOLD_BAR_SESSION_REVIEW_V3' if policy else 'GOLD_BAR_SESSION_REVIEW_V2' if budget else 'GOLD_BAR_SESSION_REVIEW_V1', closed_bars=closed, messages=count,
        missing_closed_bars=missing, source=plan['source'], economic_eligible=False,
        submission_enabled=False, source_authenticity_proven=False)
    if budget is not None:
        result.update(collection_budget=budget, first_received_at_ms=first_received,
                      last_received_at_ms=last_received, bytes=total)
    if policy is not None:
        result.update(late_open_policy=policy, rejected_late_open_messages=late_open_count)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=86400)
    parser.add_argument('--budget-file', type=Path)
    parser.add_argument('--late-open-policy', choices=(LATE_OPEN_POLICY,))
    args=parser.parse_args(argv)
    budget = None
    if args.budget_file is not None:
        path = args.budget_file.absolute()
        budget = decode(_read(path.parent, path.name))
    result=asyncio.run(observe(args.history_dir,args.output_dir,seconds=args.seconds,
                             collection_budget=budget, late_open_policy=args.late_open_policy))
    print(json.dumps(result),flush=True)
    return 0 if result['status']=='OBSERVED' else 2


if __name__=='__main__':
    raise SystemExit(main())
