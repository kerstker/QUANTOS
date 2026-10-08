"""Bounded XAUUSDT public aggregate capture, no accounts or order interface.

python -m tools.gold_public_capture --output /task/output/new.json --seconds 15
The output must not exist. A crash may leave an incomplete file, never success.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from websockets.asyncio.client import connect

from services.data.gold_execution_tape import GoldTradeCursor
from services.data.binance_execution_tape import ExecutionTapeError, MAX_EVENTS
from services.research.gold_trade_observation import load_document, ObservationError

URL='wss://fstream.binance.com/market/stream?streams=xauusdt@aggTrade'
RAW_BUDGET=3_000_000


class CaptureError(ValueError):
    pass


class PublicCapture:
    def __init__(self, *, started_at_ms):
        if type(started_at_ms) is not int or started_at_ms<0:
            raise CaptureError('INVALID_CAPTURE_START')
        self.started=started_at_ms
        self.last_received=started_at_ms
        self.cursor=GoldTradeCursor('XAUUSDT')
        self.frames=[]
        self.packets=[]
        self.unique=0
        self.raw_bytes=0
        self.blocked=False
        self.closed=False

    def ingest(self, raw, *, received_at_ms):
        if self.blocked or self.closed:
            raise CaptureError('BLOCKED_CAPTURE')
        try:
            if (not isinstance(raw,str) or len(raw)>131072 or type(received_at_ms) is not int
                    or received_at_ms<self.last_received):
                raise CaptureError('INVALID_PUBLIC_FRAME')
            encoded=raw.encode('utf-8')
            if len(self.frames)>=MAX_EVENTS or self.raw_bytes+len(encoded)>RAW_BUDGET:
                raise CaptureError('CAPTURE_BUDGET_EXHAUSTED')
            self.frames.append(raw)
            self.raw_bytes+=len(encoded)
            self.last_received=received_at_ms
            frame=load_document(encoded)
            if (not isinstance(frame,dict) or set(frame)!={'stream','data'}
                    or frame['stream']!='xauusdt@aggTrade'):
                raise CaptureError('WRONG_PUBLIC_STREAM')
            # Retain rejected input for diagnosis as well as accepted packets.
            self.packets.append({'received_at_ms':received_at_ms,'payload':frame['data']})
            trade=self.cursor.apply(frame['data'],received_at_ms=received_at_ms)
            if trade is not None:
                self.unique+=1
        except (ValueError,TypeError,UnicodeError,ExecutionTapeError,ObservationError):
            self.blocked=True
            raise CaptureError('PUBLIC_STREAM_VALIDATION_FAILED') from None

    def finish(self, *, ended_at_ms, reason):
        if (type(ended_at_ms) is not int or ended_at_ms<self.last_received
                or reason not in ('DURATION_REACHED','STREAM_INTERRUPTED','VALIDATION_FAILED')):
            raise CaptureError('INVALID_CAPTURE_END')
        status=('INVALID_STREAM' if self.blocked else 'INTERRUPTED' if reason!='DURATION_REACHED'
                else 'CAPTURED_PUBLIC_PREFIX' if self.unique else 'NO_DATA')
        self.closed=True
        return {'schema':'GOLD_PUBLIC_CAPTURE_V1','source_url':URL,'symbol':'XAUUSDT',
            'started_at_ms':self.started,'ended_at_ms':ended_at_ms,'status':status,
            'stop_reason':reason,'unique_aggregates':self.unique,
            'raw_frames':self.frames,'packets':self.packets,
            'raw_frames_sha256':hashlib.sha256(json.dumps(self.frames,
                ensure_ascii=True,separators=(',',':')).encode()).hexdigest(),
            'history_complete':False,'authoritative':False,
            'limitations':['BOUNDED_PUBLIC_AGGREGATE_PREFIX','NO_PRIVATE_ACCOUNT_DATA',
                'LOCAL_RECEIVE_CLOCK_NOT_ATTESTED','NO_STRATEGY_OR_ORDER_AUTHORITY']}


def save_capture(path, report):
    with Path(path).open('x',encoding='utf-8') as destination:
        json.dump(report,destination,sort_keys=True,ensure_ascii=True,indent=2)
        destination.write('\n')


async def collect(seconds):
    if type(seconds) is not int or not 1<=seconds<=60:
        raise CaptureError('DURATION_MUST_BE_1_TO_60_SECONDS')
    clock=lambda: time.time_ns()//1_000_000
    capture=PublicCapture(started_at_ms=clock())
    reason='DURATION_REACHED'
    try:
        async with asyncio.timeout(seconds):
            async with connect(URL,open_timeout=min(10,seconds),close_timeout=1,
                               max_size=131072,max_queue=32) as socket:
                while True:
                    raw=await socket.recv()
                    capture.ingest(raw,received_at_ms=clock())
    except TimeoutError:
        pass
    except CaptureError:
        reason='VALIDATION_FAILED'
    except Exception:
        reason='STREAM_INTERRUPTED'
    # A local clock reversal invalidates the report instead of altering time.
    return capture.finish(ended_at_ms=clock(),reason=reason)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=int,choices=range(1,61),default=15)
    args=parser.parse_args(argv)
    try:
        # Reserve before opening a network connection; never overwrite an archive.
        with args.output.open('x',encoding='utf-8') as destination:
            report=asyncio.run(collect(args.seconds))
            json.dump(report,destination,sort_keys=True,ensure_ascii=True,indent=2)
            destination.write('\n')
    except (OSError,CaptureError):
        print(json.dumps({'status':'REJECTED','reason':'CAPTURE_OR_OUTPUT_FAILED'}))
        return 2
    print(json.dumps({k:report[k] for k in ('status','stop_reason','unique_aggregates','raw_frames_sha256')}))
    return 0 if report['status']=='CAPTURED_PUBLIC_PREFIX' else 2


if __name__=='__main__':
    raise SystemExit(main())
