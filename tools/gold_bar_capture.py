"""One fixed, public XAUUSDT 15-minute history request; no credentials/orders."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request
import urllib.error

from services.execution.perpetual_public_replay import decode_public_gzip
from services.research.gold_bar_candidates import INTERVAL_MS
from services.research.gold_bar_dataset import load_bars, split_plan
from tools.perpetual_public_capture import HOST, _NoRedirect


def _write(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, sort_keys=True, allow_nan=False, indent=2)
        handle.write('\n')


def _fetch(output, endpoint, requested, *, protected_network=False):
    _write(output / 'request.json', dict(endpoint=endpoint, requested_at_ms=requested,
                                       symbol='XAUUSDT', submission_enabled=False))
    if type(protected_network) is not bool:
        raise ValueError('BAR_HISTORY_NETWORK_MODE')
    transport = None
    if protected_network:
        from tools.gold_bar_network import opener
        transport = opener()
    for attempt in range(1, 3):
        request = urllib.request.Request(HOST + endpoint, method='GET',
                                        headers={'Accept': 'application/json', 'Accept-Encoding': 'gzip'})
        actual_requested = time.time_ns() // 1_000_000
        _write(output / f'attempt-{attempt}-request.json',
               dict(endpoint=endpoint, requested_at_ms=actual_requested, attempt=attempt))
        try:
            with (transport or urllib.request.build_opener(_NoRedirect())).open(request, timeout=20) as response:
                if response.geturl() != HOST + endpoint:
                    raise ValueError('UNEXPECTED_PUBLIC_URL')
                wire = response.read(2_000_001)
                encoding = response.headers.get('Content-Encoding', 'identity').lower()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            retry = attempt == 1 and not isinstance(exc, urllib.error.HTTPError)
            _write(output / f'attempt-{attempt}-error.json', dict(attempt=attempt,
                observed_at_ms=time.time_ns() // 1_000_000, error_type=type(exc).__name__,
                reason_type=type(getattr(exc, 'reason', None)).__name__, retry_scheduled=retry))
            if not retry:
                raise
            time.sleep(0.25)
        else:
            break
    received = time.time_ns() // 1_000_000
    with (output / 'response.wire').open('xb') as handle:
        handle.write(wire)
    if len(wire) > 2_000_000 or encoding not in ('identity', 'gzip'):
        raise ValueError('BAR_RESPONSE_TRANSPORT')
    raw = decode_public_gzip(wire) if encoding == 'gzip' else wire
    with (output / 'response.json').open('xb') as handle:
        handle.write(raw)
    _write(output / 'receipt.json', dict(endpoint=endpoint, requested_at_ms=actual_requested,
        received_at_ms=received, content_encoding=encoding,
        wire_sha256=hashlib.sha256(wire).hexdigest(), response_sha256=hashlib.sha256(raw).hexdigest(),
        **({'network_mode':'PROTECTED_DEMO_NETWORK_V1'} if protected_network else {})))
    return raw, received


def capture(output, *, protected_network=False):
    output = Path(output)
    output.mkdir(mode=0o700)  # Do not overwrite any prior attempt.
    requested = time.time_ns() // 1_000_000
    endpoint = ('/fapi/v1/klines?symbol=XAUUSDT&interval=15m&limit=1500'
                f'&endTime={requested // INTERVAL_MS * INTERVAL_MS - 1}')
    raw, received = _fetch(output, endpoint, requested,
        **({'protected_network':protected_network} if protected_network is not False else {}))
    bars = load_bars(raw, requested_at_ms=requested, received_at_ms=received)
    plan = split_plan(bars, source_raw=raw)
    _write(output / 'split-plan.json', plan)
    summary = dict(schema='GOLD_BAR_HISTORY_CAPTURE_V1', status='CAPTURED_FOR_RESEARCH',
        count=len(bars), start_ms=bars[0].open_ms, end_exclusive_ms=bars[-1].close_ms,
        received_at_ms=received, source_sha256=hashlib.sha256(raw).hexdigest(),
        split_plan_sha256=plan['plan_sha256'], historical_pit_proven=False,
        net_pnl=None, authoritative=False, submission_enabled=False,
        missing_evidence=['HISTORICAL_PUBLICATION_CLOCKS', 'EXECUTABLE_QUOTES',
                          'HISTORICAL_CONTRACT_RULES', 'FUNDING_AND_FEE_INPUTS'])
    _write(output / 'summary.json', summary)
    return summary


def capture_funding(output, history):
    from services.research.gold_funding_dataset import load_funding_observations
    from services.governance.json_integrity import load_json_without_duplicate_keys
    history = Path(history)
    raw = (history / 'response.json').read_bytes()
    receipt = load_json_without_duplicate_keys((history / 'receipt.json').read_bytes())
    if hashlib.sha256(raw).hexdigest() != receipt['response_sha256']:
        raise ValueError('BAR_HISTORY_CHANGED')
    bars = load_bars(raw, requested_at_ms=receipt['requested_at_ms'],
                     received_at_ms=receipt['received_at_ms'])
    start, end = bars[0].open_ms, bars[-1].close_ms
    output = Path(output)
    output.mkdir(mode=0o700)
    requested = time.time_ns() // 1_000_000
    if requested < end:
        raise ValueError('FUTURE_HISTORY')
    endpoint = (f'/fapi/v1/fundingRate?symbol=XAUUSDT&startTime={start}'
                f'&endTime={end - 1}&limit=1000')
    funding, received = _fetch(output, endpoint, requested)
    funding_receipt = load_json_without_duplicate_keys((output / 'receipt.json').read_bytes())
    observations = load_funding_observations(funding, start_ms=start, end_ms=end,
        requested_at_ms=funding_receipt['requested_at_ms'], received_at_ms=received)
    events = tuple(row.event for row in observations)
    _write(output / 'observed-settlements.json', [row.as_document() for row in observations])
    result = dict(schema='GOLD_FUNDING_HISTORY_CAPTURE_V1', status='CAPTURED_FOR_RESEARCH',
        bar_source_sha256=hashlib.sha256(raw).hexdigest(),
        funding_source_sha256=hashlib.sha256(funding).hexdigest(), count=len(events),
        start_ms=start, end_exclusive_ms=end, received_at_ms=received,
        first_settlement_ms=events[0].timestamp if events else None,
        last_settlement_ms=events[-1].timestamp if events else None,
        schedule_completeness_proven=False, historical_pit_proven=False,
        authoritative=False, submission_enabled=False, net_pnl=None)
    _write(output / 'summary.json', result)
    return result


def capture_funding_schedule(output):
    """Retain a received next-funding forecast, never an actual settlement time."""
    from services.governance.json_integrity import load_json_without_duplicate_keys
    output = Path(output)
    output.mkdir(mode=0o700)
    raw, received = _fetch(output, '/fapi/v1/premiumIndex?symbol=XAUUSDT',
                           time.time_ns() // 1_000_000)
    document = load_json_without_duplicate_keys(raw)
    receipt = load_json_without_duplicate_keys((output / 'receipt.json').read_bytes())
    if (type(document) is not dict or document.get('symbol') != 'XAUUSDT'
            or any(type(document.get(k)) is not int or not 0 <= document[k] <= 2**63 - 1
                   for k in ('time', 'nextFundingTime'))
            or not 0 <= received - document['time'] <= 30000
            or not 0 <= receipt['requested_at_ms'] <= received
            or receipt['received_at_ms'] != received
            or receipt['response_sha256'] != hashlib.sha256(raw).hexdigest()):
        raise ValueError('INVALID_FUNDING_FORECAST_RECEIPT')
    result = dict(schema='GOLD_FUNDING_SCHEDULE_OBSERVATION_V1',
        status='FORECAST_OBSERVED' if document['nextFundingTime'] > received
               else 'FORECAST_EXPIRED_ON_RECEIPT',
        symbol='XAUUSDT', event_at_ms=document['time'],
        requested_at_ms=receipt['requested_at_ms'], available_at_ms=received,
        next_settlement_nominal_ms=document['nextFundingTime'],
        source_sha256=receipt['response_sha256'], actual_settlement_time_proven=False,
        schedule_completeness_proven=False, authoritative=False, submission_enabled=False)
    _write(output / 'summary.json', result)
    return result


def observe_funding(output, *, seconds=86400, interval_seconds=60):
    """Retain overlapping public polls and first-observed clocks for up to a day.

    This is input collection, not a complete settlement schedule or account cash.
    The existing ledger must not consume these as known-at-settlement rates.
    """
    from services.research.gold_funding_dataset import load_funding_observations
    from services.governance.json_integrity import load_json_without_duplicate_keys
    if (type(seconds) is not int or not 1 <= seconds <= 86400
            or type(interval_seconds) is not int or not 60 <= interval_seconds <= 3600):
        raise ValueError('FUNDING_OBSERVATION_BUDGET')
    output = Path(output)
    output.mkdir(mode=0o700)
    start = time.time_ns() // 1_000_000 - 2 * 86400000
    deadline = time.monotonic() + seconds
    _write(output / 'plan.json', dict(schema='GOLD_FUNDING_OBSERVATION_PLAN_V1',
        start_ms=start, seconds=seconds, interval_seconds=interval_seconds,
        symbol='XAUUSDT', submission_enabled=False))
    seen = {}
    polls = duplicates = 0
    reason = 'DURATION_COMPLETE'
    with (output / 'settlements.jsonl').open('x', encoding='utf-8') as journal:
        try:
            while time.monotonic() < deadline:
                polls += 1
                poll = output / f'poll-{polls:04d}'
                poll.mkdir(mode=0o700)
                end = time.time_ns() // 1_000_000
                endpoint = (f'/fapi/v1/fundingRate?symbol=XAUUSDT&startTime={start}'
                            f'&endTime={end - 1}&limit=1000')
                raw, received = _fetch(poll, endpoint, end)
                receipt = load_json_without_duplicate_keys((poll / 'receipt.json').read_bytes())
                observations = load_funding_observations(raw, start_ms=start, end_ms=end,
                    requested_at_ms=receipt['requested_at_ms'], received_at_ms=received)
                # Validate the whole response before extending the derived journal.
                if any(item.event.timestamp in seen and seen[item.event.timestamp] != item.event
                       for item in observations):
                    raise ValueError('CONFLICTING_FUNDING_SETTLEMENT')
                for item in observations:
                    if item.event.timestamp in seen:
                        duplicates += 1
                        continue
                    seen[item.event.timestamp] = item.event
                    journal.write(json.dumps(item.as_document(), sort_keys=True) + '\n')
                journal.flush()
                os.fsync(journal.fileno())
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(interval_seconds, remaining))
        except (ValueError, OSError) as exc:
            reason = ('CONFLICTING_FUNDING_SETTLEMENT' if str(exc) == 'CONFLICTING_FUNDING_SETTLEMENT'
                      else 'CAPTURE_FAILED_' + type(exc).__name__)
    result = dict(schema='GOLD_FUNDING_OBSERVATION_V1',
        status='OBSERVATION_COMPLETE' if reason == 'DURATION_COMPLETE' else 'INCOMPLETE',
        reason=reason, polls=polls, settlements=len(seen), duplicates=duplicates,
        journal_sha256=hashlib.sha256((output / 'settlements.jsonl').read_bytes()).hexdigest(),
        schedule_completeness_proven=False, account_cash_verified=False,
        historical_pit_proven=False, authoritative=False, submission_enabled=False)
    _write(output / 'summary.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--funding-for-history', type=Path)
    mode.add_argument('--observe-funding', action='store_true')
    mode.add_argument('--funding-schedule', action='store_true')
    parser.add_argument('--seconds', type=int, default=86400)
    args = parser.parse_args(argv)
    try:
        result = (capture_funding_schedule(args.output_dir) if args.funding_schedule
                  else observe_funding(args.output_dir, seconds=args.seconds) if args.observe_funding
                  else capture_funding(args.output_dir, args.funding_for_history)
                  if args.funding_for_history else capture(args.output_dir))
    except (OSError, ValueError) as exc:
        print(json.dumps(dict(status='CAPTURE_INCOMPLETE', error_type=type(exc).__name__,
                              submission_enabled=False)))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 2 if result['status'] == 'INCOMPLETE' else 0


if __name__ == '__main__':
    raise SystemExit(main())
