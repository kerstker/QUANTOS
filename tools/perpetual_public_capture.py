"""Bounded, keyless Binance public observations for offline technical replay.

Only fixed GET market-data endpoints are reachable. No account, SDK, credential,
private endpoint, or trading action is supported. Capture files are append-once.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import urllib.error
import ssl
import socket
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_public_replay import GOLD_UNIT_BASIS, decode_public_gzip

HOST='https://fapi.binance.com'
DAY_MS=86_400_000
SCHEMA='BINANCE_PUBLIC_REPLAY_CAPTURE_V2'
RAW_GAP_TRANSPORT = 'POOLED_RETAINED_GAPS_V1'
POOLED_SESSION_TRANSPORTS = ('POOLED_CADENCE_V1', RAW_GAP_TRANSPORT)


def quote_transport_profile(name):
    """Fixed prospective budgets; never restart a failed research window."""
    if type(name) is not str or name not in ('LEGACY_V1', 'BOUNDED_BACKOFF_V1', 'FRESH_CADENCE_V1', *POOLED_SESSION_TRANSPORTS):
        raise ValueError('INVALID_QUOTE_RETRY_PROFILE')
    if name in POOLED_SESSION_TRANSPORTS:
        value = dict(profile=name, delays_seconds=[0.25], timeout_seconds=5,
                    sampling_clock='REQUEST_START_CADENCE_V1', gaps_filled=False,
                    connection_pool='SINGLE_PUBLIC_ORIGIN_V1', automatic_retries=False,
                    redirects=False, tls_verification_required=True)
        if name == RAW_GAP_TRANSPORT:
            value.update(exhausted_transient_policy='WAIT_ORIGINAL_CADENCE_WITHIN_SESSION_V1',
                         collection_clock='BOOTSTRAP_START_MONOTONIC_V1',
                         continuation_scope='RAW_SESSION_ONLY')
        return value
    if name == 'FRESH_CADENCE_V1':
        return dict(profile=name, delays_seconds=[0.25], timeout_seconds=5,
                    sampling_clock='REQUEST_START_CADENCE_V1', gaps_filled=False)
    return dict(profile=name, delays_seconds=[0.25] if name == 'LEGACY_V1' else [1, 2, 4],
                timeout_seconds=15, gaps_filled=False)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('public capture redirects are forbidden')


def capture_public(*, samples: int=8, interval_seconds: int=1, symbol: str="BTCUSDT", on_record=None, on_error=None, cancel_event=None, retry_profile='LEGACY_V1') -> dict:
    return _capture_public(samples=samples, interval_seconds=interval_seconds, symbol=symbol,
                           on_record=on_record, on_error=on_error, continuous=False, cancel_event=cancel_event, retry_profile=retry_profile)


def capture_gold_continuous(*, samples=120, interval_seconds=10, on_record=None, on_error=None,
                            dense_close_times=(), observe_funding=False, cancel_event=None, retry_profile='LEGACY_V1'):
    """One uninterrupted, bounded gold tape; original gaps and epoch rules apply."""
    return _capture_public(samples=samples, interval_seconds=interval_seconds, symbol='XAUUSDT',
                           on_record=on_record, on_error=on_error, continuous=True,
                           dense_close_times=dense_close_times, observe_funding=observe_funding, cancel_event=cancel_event, retry_profile=retry_profile)


def capture_gold_extended(*, close_times, interval_seconds=10, on_record=None, on_error=None,
                          observe_funding=False, cancel_event=None, retry_profile='LEGACY_V1'):
    """Capture through the final planned close, including subsequent fill quotes."""
    from quantos.contracts.gold_observation_window import window_closes, EXTENDED_MAX_SAMPLES
    closes = window_closes(close_times)
    now = int(time.time() * 1000)
    if not 30000 <= closes[0] - now <= 180000:
        raise ValueError('EXTENDED_CAPTURE_START_WINDOW_MISSED')
    return _capture_public(samples=EXTENDED_MAX_SAMPLES, interval_seconds=interval_seconds,
        symbol='XAUUSDT', on_record=on_record, on_error=on_error, continuous=True,
        dense_close_times=closes, extended=True, observe_funding=observe_funding, cancel_event=cancel_event, retry_profile=retry_profile)


def capture_gold_session(*, seconds=86400, interval_seconds=10, on_record=None,
                         on_error=None, cancel_event=None, segment_samples=None,
                         on_segment=None, collection_budget=None, quote_transport=None):
    """Stream public quote receipts independently of research-window processing.

    The sink owns durable original records. Only the last sample remains in
    memory. This raw observation schema is not admitted by the replay adapter:
    day/rule transitions and funding coverage still require explicit integration.
    """
    from quantos.contracts.gold_collection_budget import for_duration
    budget = for_duration(collection_budget, seconds)
    if not callable(on_record) or budget is not None and segment_samples is None:
        raise ValueError('INVALID_QUOTE_SESSION_OR_RECEIPT_SINK')
    if quote_transport is not None:
        if budget is None:
            raise ValueError('QUOTE_TRANSPORT_EXPLICIT_BUDGET_REQUIRED')
        session_quote_transport_name(quote_transport_profile(quote_transport))
    return _capture_public(samples=seconds+(3 if segment_samples is not None else 1), interval_seconds=interval_seconds,
        symbol='XAUUSDT', on_record=on_record, on_error=on_error, continuous=True,
        cancel_event=cancel_event, session_seconds=seconds,
        segment_samples=segment_samples, on_segment=on_segment,
        collection_budget=budget,
        retry_profile=(quote_transport if quote_transport is not None else 'FRESH_CADENCE_V1')
                      if budget else 'LEGACY_V1')


def session_quote_transport_name(value):
    for name in ('FRESH_CADENCE_V1', *POOLED_SESSION_TRANSPORTS):
        expected = quote_transport_profile(name)
        if (type(value) is dict and value == expected
                and exact_identity_hash(value, name='quote_transport') ==
                    exact_identity_hash(expected, name='quote_transport')):
            return name
    raise ValueError('INVALID_QUOTE_SESSION_TRANSPORT')


def _capture_public(**params):
    if params.get('retry_profile') in POOLED_SESSION_TRANSPORTS:
        if (params.get('collection_budget') is None or params.get('session_seconds') is None
                or params.get('segment_samples') is None or params.get('symbol') != 'XAUUSDT'):
            raise ValueError('POOLED_QUOTE_EXPLICIT_SESSION_REQUIRED')
        if params.get('retry_profile') == RAW_GAP_TRANSPORT and not callable(params.get('on_error')):
            raise ValueError('CONTINUATION_ERROR_SINK_REQUIRED')
        from tools.gold_quote_transport import PublicQuotePool
        with PublicQuotePool() as pooled:
            return _capture_public_transport(**params, pooled_transport=pooled)
    return _capture_public_transport(**params)


def _capture_public_transport(*, samples, interval_seconds, symbol, on_record, on_error, continuous,
                    dense_close_times=(), extended=False, observe_funding=False, cancel_event=None, session_seconds=None, retry_profile='LEGACY_V1',
                    segment_samples=None, on_segment=None, collection_budget=None, pooled_transport=None):
    from quantos.contracts.gold_observation_window import EXTENDED_CAPTURE_SCHEMA, EXTENDED_MAX_SAMPLES
    transport = quote_transport_profile(retry_profile)
    delays = transport['delays_seconds']
    if cancel_event is not None and not all(callable(getattr(cancel_event, name, None))
                                            for name in ('is_set', 'wait')):
        raise ValueError('INVALID_CAPTURE_CANCELLATION_EVENT')
    def check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('PUBLIC_CAPTURE_CANCELLED')
    def pause(seconds):
        if cancel_event is None:
            time.sleep(seconds)
        else:
            cancel_event.wait(seconds)
            check_cancelled()
    if type(observe_funding) is not bool or observe_funding and (not continuous or symbol != 'XAUUSDT'):
        raise ValueError('INVALID_PUBLIC_FUNDING_OBSERVATION_MODE')
    if any(callback is not None and not callable(callback) for callback in (on_record, on_error)):
        raise ValueError('public receipt sink must be callable')
    if type(symbol) is not str or symbol not in ("BTCUSDT", "XAUUSDT"):
        raise ValueError("unsupported public capture symbol")
    session_mode = session_seconds is not None
    segmented = segment_samples is not None
    from quantos.contracts.gold_collection_budget import for_duration
    budget = for_duration(collection_budget, session_seconds) if session_mode else None
    if collection_budget is not None and (not session_mode or not segmented):
        raise ValueError('INVALID_PUBLIC_COLLECTION_BUDGET_MODE')
    if (segmented and (not session_mode or type(segment_samples) is not int
            or not 3 <= segment_samples <= 120 or not callable(on_segment))
            or not segmented and on_segment is not None):
        raise ValueError('INVALID_PUBLIC_SEGMENT_SESSION')
    if session_mode and (not continuous or symbol != 'XAUUSDT' or extended or dense_close_times
            or observe_funding or not callable(on_record)):
        raise ValueError('INVALID_PUBLIC_QUOTE_SESSION')
    lower, upper = ((31, session_seconds+(3 if segmented else 1)) if session_mode else
        (61, EXTENDED_MAX_SAMPLES) if extended else (61, 240) if continuous else (3, 60))
    if type(samples) is not int or not lower <= samples <= upper:
        raise ValueError('public capture sample budget invalid')
    if type(interval_seconds) is not int or not 1<=interval_seconds<=10:
        raise ValueError('interval must be between 1 and 10 seconds')
    if (type(dense_close_times) is not tuple or len(dense_close_times) > (4 if extended else 2)
            or any(type(v) is not int or v <= 0 or v % 900000 for v in dense_close_times)
            or tuple(sorted(set(dense_close_times))) != dense_close_times
            or dense_close_times and (not continuous or not extended and samples > 120)):
        raise ValueError('invalid dense close sampling schedule')
    retained_gaps = retry_profile == RAW_GAP_TRANSPORT
    session_end = time.monotonic() + session_seconds if retained_gaps else None
    def fetch_once(kind,path):
        check_cancelled()
        requested=int(time.time()*1000)
        if pooled_transport is not None:
            wire, encoding = pooled_transport.fetch(path)
        else:
            request=urllib.request.Request(HOST+path,headers={'Accept':'application/json','Accept-Encoding':'gzip'},method='GET')
            with urllib.request.build_opener(_NoRedirect()).open(request,timeout=transport['timeout_seconds']) as response:
                if not response.geturl().startswith(HOST+'/fapi/v1/'):
                    raise ValueError('unexpected public-data redirect')
                wire=response.read(10_000_001)
                encoding=getattr(response,'headers',{}).get('Content-Encoding','identity').lower()
        received=int(time.time()*1000)
        if len(wire)>10_000_000:
            raise ValueError('public response exceeds size budget')
        if encoding not in ('identity','gzip'):
            raise ValueError('unsupported public content encoding')
        raw=decode_public_gzip(wire) if encoding=='gzip' else wire
        encoding_evidence=({'content_encoding':'gzip','wire_response_base64':base64.b64encode(wire).decode('ascii'),
            'wire_response_sha256':hashlib.sha256(wire).hexdigest()} if encoding=='gzip' else {})
        record = {'kind':kind,'requested_at':requested,'received_at':received,
                'endpoint':path,'payload':json.loads(raw),
                'response_sha256':hashlib.sha256(raw).hexdigest(),
                'response_base64':base64.b64encode(raw).decode('ascii'),**encoding_evidence}
        return record
    def fetch(kind, path):
        # Each cycle retains the original two-attempt request budget. Only the
        # explicit raw policy can schedule another cycle inside the same fixed
        # collection deadline; every gap and failed attempt remains original.
        for cycle in range(samples if retained_gaps else 1):
            for attempt in range(len(delays) + 1):
                try:
                    record = fetch_once(kind, path)
                except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                    transient = True
                    if retry_profile != 'LEGACY_V1':
                        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                        transient = isinstance(reason, (TimeoutError, ConnectionError, ssl.SSLEOFError, socket.gaierror))
                    eligible = transient and not isinstance(exc, urllib.error.HTTPError)
                    remaining = session_end - time.monotonic() if retained_gaps else None
                    retry = attempt < len(delays) and eligible and (remaining is None or remaining > 0)
                    continuing = bool(retained_gaps and not retry and eligible
                                      and remaining > 0 and cycle + 1 < samples)
                    wait = min(interval_seconds, remaining) if continuing else None
                    if on_error is not None:
                        on_error({'kind': kind, 'endpoint': path, 'attempt': attempt + 1,
                            'observed_at': int(time.time()*1000), 'error_type': type(exc).__name__,
                            'reason_type': type(getattr(exc, 'reason', None)).__name__,
                            'retry_scheduled': retry, **(dict(retry_profile=retry_profile,
                                retry_delay_seconds=delays[attempt] if retry else None)
                                if retry_profile != 'LEGACY_V1' else {}),
                            **(dict(http_status=getattr(exc, 'code', None))
                               if pooled_transport is not None else {}),
                            **(dict(continuation_cycle=cycle, continuation_scheduled=continuing,
                                    continuation_delay_seconds=wait) if retained_gaps else {})})
                    if continuing:
                        pause(wait)
                        if time.monotonic() >= session_end:
                            raise
                        break
                    if not retry:
                        raise
                    pause(delays[attempt])
                    if retained_gaps and time.monotonic() >= session_end:
                        raise
                else:
                    if on_record is not None:
                        on_record(record)
                    return record
    def fresh_bootstrap():
        day=int(time.time()*1000)//DAY_MS*DAY_MS
        return {
            'exchange':fetch('exchange','/fapi/v1/exchangeInfo'),
            'funding':fetch('funding',f'/fapi/v1/fundingRate?symbol={symbol}&limit=1'),
            'candles':fetch('candles',f'/fapi/v1/klines?symbol={symbol}&interval=1d&startTime={day-30*DAY_MS}&endTime={day-1}&limit=30'),
        }, day
    bootstrap, bootstrap_day = fresh_bootstrap()
    observations=[]
    sample_count = 0
    coverage = dict(first_received_at=None, last_received_at=None, max_gap_ms=0, gap_count=0)
    if not retained_gaps:
        session_end = time.monotonic() + session_seconds if session_mode else None
    funding_observations=[]
    nominal=None
    next_funding_poll=0
    settlement_seen=False
    phase_bootstrap = bootstrap
    phase_samples, phase_funding, phase_capacity = [], [], []
    capacity_day = bootstrap_day
    phase_nominal = None
    phase_settled = False
    phase_poll_after = 0
    segment_count = 0
    terminal_extra = None

    def segment_ready():
        if not phase_samples or phase_nominal is None:
            return False
        end = phase_samples[-1]['depth']['received_at']
        return phase_nominal > end and all(row['received_at'] <= end for row in phase_funding)

    def emit_segment(after):
        nonlocal segment_count
        if budget is not None and segment_count >= budget['maximum_quote_segments']:
            raise ValueError('QUOTE_SESSION_SEGMENT_BUDGET')
        document = dict(schema='GOLD_PUBLIC_QUOTE_SEGMENT_V1', index=segment_count,
            bootstrap=phase_bootstrap, samples=list(phase_samples), exchange_after=after,
            observed_funding_records=list(phase_funding), capacity_observations=list(phase_capacity),
            target_samples=segment_samples, maximum_samples=240,
            status='RAW_SEGMENT_NOT_REPLAY_VALIDATED', replay_ready=False,
            economic_eligible=False, submission_enabled=False)
        document['segment_id'] = exact_identity_hash(document, name='gold_raw_quote_segment')
        on_segment(document)
        segment_count += 1

    def segment_funding(sample):
        nonlocal phase_nominal, phase_settled, phase_poll_after
        forecast = sample['mark']['payload'].get('nextFundingTime')
        received = sample['depth']['received_at']
        if type(forecast) is not int or not 0 < forecast <= 2**63-1:
            raise ValueError('SEGMENT_FUNDING_FORECAST_UNAVAILABLE')
        if phase_nominal is None:
            if forecast <= received:
                raise ValueError('SEGMENT_INITIAL_FUNDING_FORECAST_UNAVAILABLE')
            phase_nominal = forecast
        if phase_settled and forecast > phase_nominal:
            phase_nominal, phase_settled, phase_poll_after = forecast, False, 0
        now = int(time.time()*1000)
        if not phase_settled and now >= max(phase_nominal+1,phase_poll_after):
            record = fetch('funding',f'/fapi/v1/fundingRate?symbol={symbol}'
                f'&startTime={phase_nominal}&endTime={now-1}&limit=1000')
            phase_funding.append(record)
            phase_poll_after = record['received_at']+60000
            phase_settled = any(type(row) is dict and type(row.get('fundingTime')) is int
                and row['fundingTime'] >= phase_nominal for row in record['payload'])
            if phase_settled and forecast > phase_nominal:
                phase_nominal, phase_settled, phase_poll_after = forecast, False, 0

    def observe():
        nonlocal nominal, next_funding_poll, settlement_seen, sample_count
        if sample_count >= upper:
            raise ValueError('public capture sample budget exhausted')
        observations.append({
            'mark':fetch('mark',f'/fapi/v1/premiumIndex?symbol={symbol}'),
            'depth':fetch('depth',f'/fapi/v1/depth?symbol={symbol}&limit=5'),
        })
        sample_count += 1
        received = observations[-1]['depth']['received_at']
        if coverage['last_received_at'] is not None:
            gap = received - coverage['last_received_at']
            coverage['max_gap_ms'] = max(coverage['max_gap_ms'], gap)
            if gap <= 0 or gap > 30000:
                coverage['gap_count'] += 1
        else:
            coverage['first_received_at'] = received
        coverage['last_received_at'] = received
        if session_mode:
            del observations[:-1]
        if segmented:
            phase_samples.append(observations[-1])
            if len(phase_samples)>240:
                raise ValueError('SEGMENT_SAMPLE_BUDGET_EXHAUSTED')
            segment_funding(observations[-1])
        if observe_funding:
            if nominal is None:
                nominal = observations[0]['mark']['payload']['nextFundingTime']
                if type(nominal) is not int or nominal <= observations[0]['depth']['received_at']:
                    raise ValueError('INITIAL_FUNDING_FORECAST_UNAVAILABLE')
            now = int(time.time() * 1000)
            if not settlement_seen and now >= max(nominal + 1, next_funding_poll):
                receipt = fetch('funding',
                    f'/fapi/v1/fundingRate?symbol={symbol}&startTime={nominal}&endTime={now-1}&limit=1000')
                funding_observations.append(receipt)
                next_funding_poll = receipt['received_at'] + 60000
                settlement_seen = any(type(row) is dict and type(row.get('fundingTime')) is int
                    and row['fundingTime'] >= nominal for row in receipt['payload'])

    cadence = retry_profile in ('FRESH_CADENCE_V1', *POOLED_SESSION_TRANSPORTS)
    previous_started = None
    for index in range(samples):
        if index and not dense_close_times:
            delay = max(0, previous_started + interval_seconds - time.monotonic()) if cadence else interval_seconds
            if segmented:
                offset = int(time.time()*1000)%900000
                if offset <= 20000 or offset >= 890000:
                    delay = min(delay,1)
            if delay:
                pause(delay)
        elif index:
            deadline = (previous_started if cadence else time.monotonic()) + interval_seconds
            while (remaining := deadline - time.monotonic()) > 0:
                now = time.time() * 1000
                active = any(close - 10000 <= now < close + 20000 for close in dense_close_times)
                starts = [(close - 10000 - now) / 1000 for close in dense_close_times if close - 10000 > now]
                delay = min(remaining, 1 if active else interval_seconds, *starts)
                pause(delay)
                now = time.time() * 1000
                if (time.monotonic() < deadline
                        and any(close - 10000 <= now < close + 20000 for close in dense_close_times)):
                    observe()
        pending_bootstrap = None
        if segmented:
            current_day = int(time.time()*1000)//DAY_MS*DAY_MS
            if current_day != capacity_day:
                if current_day != capacity_day+DAY_MS:
                    raise ValueError('SEGMENT_DAILY_CAPACITY_GAP')
                receipt = fetch('candles',f'/fapi/v1/klines?symbol={symbol}&interval=1d'
                    f'&startTime={current_day-30*DAY_MS}&endTime={current_day-1}&limit=30')
                phase_capacity.append(receipt)
                capacity_day = current_day
            if len(phase_samples)>=segment_samples-1:
                pending_bootstrap, pending_day = fresh_bootstrap()
        previous_started = time.monotonic()
        observe()
        if session_mode and time.monotonic() >= session_end:
            if not segmented or len(phase_samples)>=3 and segment_ready():
                break
            terminal_extra = 0 if terminal_extra is None else terminal_extra+1
            if terminal_extra >= 2:
                raise ValueError('SEGMENT_TERMINAL_TAIL_INCOMPLETE')
        elif segmented and pending_bootstrap is not None and segment_ready():
            boundary = phase_samples[-1]
            if pending_day == boundary['depth']['received_at']//DAY_MS*DAY_MS:
                after = fetch('exchange','/fapi/v1/exchangeInfo')
                emit_segment(after)
                phase_bootstrap, phase_samples = pending_bootstrap, [boundary]
                phase_funding, phase_capacity = [], []
                capacity_day = pending_day
                phase_nominal = boundary['mark']['payload']['nextFundingTime']
                phase_settled, phase_poll_after = False, 0
        if extended and observations[-1]['depth']['received_at'] >= dense_close_times[-1] + 30000:
            break
    if session_mode and time.monotonic() < session_end:
        raise ValueError('QUOTE_SESSION_SAMPLE_BUDGET_BEFORE_DEADLINE')
    if extended and observations[-1]['depth']['received_at'] < dense_close_times[-1] + 30000:
        raise ValueError('EXTENDED_CAPTURE_BUDGET_BEFORE_WINDOW_END')
    after=fetch('exchange','/fapi/v1/exchangeInfo')
    if segmented:
        if len(phase_samples)<3 or not segment_ready():
            raise ValueError('SEGMENT_TERMINAL_TAIL_INCOMPLETE')
        emit_segment(after)
    document={'schema_version':SCHEMA,'symbol':symbol,'bootstrap':bootstrap,
              'samples':observations,'exchange_after':after,'max_gap_ms':30_000,
              'declared_interval_ms':interval_seconds*1000}
    if continuous and not session_mode:
        # Retain late public settlement observations separately. They are not
        # decision-time knowledge or account-specific settled cash flows.
        start = observations[0]['depth']['received_at']
        end = observations[-1]['depth']['received_at']
        document['funding_after'] = fetch('funding',
            f'/fapi/v1/fundingRate?symbol={symbol}&startTime={start}&endTime={end}&limit=1000')
    if observe_funding:
        document['funding_observations'] = funding_observations
    if dense_close_times:
        document['sampling_schedule'] = dict(schema='GOLD_CLOSE_DENSE_SAMPLING_V1',
            close_times_ms=list(dense_close_times), before_close_ms=10000, after_close_ms=20000,
            dense_sleep_ms=1000, base_samples=samples, maximum_samples=upper)
    if symbol == 'XAUUSDT':
        document['schema_version'] = (EXTENDED_CAPTURE_SCHEMA if extended else
                                      'GOLD_PUBLIC_CONTINUOUS_CAPTURE_V1' if continuous
                                      else 'GOLD_PUBLIC_REPLAY_CAPTURE_V1')
        document['quantity_basis'] = dict(GOLD_UNIT_BASIS)
    if extended:
        document['window'] = dict(close_times_ms=list(dense_close_times), post_close_ms=30000)
    if session_mode:
        document.update(schema_version='GOLD_PUBLIC_QUOTE_SESSION_V1',
            requested_seconds=session_seconds, sample_count=sample_count,
            quote_coverage=coverage, samples_retained='LAST_ONLY_ORIGINALS_IN_RECEIPT_SINK',
            replay_ready=False, submission_enabled=False,
            limitations=['RAW_CONTINUOUS_QUOTES_ONLY', 'NO_CROSS_DAY_RULE_OR_FUNDING_COVERAGE',
                         'GAPS_RECORDED_NOT_FILLED'])
        if segmented:
            document.update(schema_version='GOLD_PUBLIC_SEGMENTED_QUOTE_SESSION_V1',
                segment_count=segment_count, segment_samples=segment_samples,
                maximum_segment_samples=240, maximum_terminal_extra_samples=2,
                shared_boundary='FRESH_BOOTSTRAP_THEN_ORIGINAL_QUOTE',
                dense_close_sampling=dict(clock='UTC_15M_CALENDAR',before_ms=10000,
                    after_ms=20000,interval_ms=1000),
                limitations=['RAW_SEGMENTS_NOT_REPLAY_VALIDATED',
                    'ORIGINAL_RECEIPTS_AND_GAPS_RETAINED', 'NO_INDEPENDENT_SOURCE_OR_ACCOUNT_AUTHORITY'])
        if budget is not None:
            document.update(schema_version='GOLD_PUBLIC_SEGMENTED_QUOTE_SESSION_V2',
                            collection_budget=budget)
    if cadence:
        document['quote_transport'] = transport
    if pooled_transport is not None:
        document['transport_metrics'] = pooled_transport.metrics()
    document['capture_id']=exact_identity_hash(document,name='public_replay_capture')
    return document


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--samples',type=int,default=8)
    parser.add_argument('--symbol', choices=('BTCUSDT', 'XAUUSDT'), default='BTCUSDT')
    args=parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('refusing to replace frozen capture')
    data=capture_public(samples=args.samples, symbol=args.symbol)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as handle:
        json.dump(data,handle,sort_keys=True,allow_nan=False)
    print(json.dumps({'capture_id':data['capture_id'],'samples':len(data['samples']),'output':str(args.output)}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
