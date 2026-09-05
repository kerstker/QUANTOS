"""Keyless, GET-only Binance spot archives; immutable checksum-bound captures."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time
from urllib.request import urlopen
import zipfile

SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'BTCUSDC', 'ETHUSDC')


def minute_timestamp(raw: str) -> int:
    value = int(raw)
    divisor = 1000 if value >= 10**15 else 1
    if value <= 0 or value % (60000 * divisor):
        raise ValueError('invalid minute timestamp precision or alignment')
    return value // divisor


def verify_archive(raw: bytes, checksum: str, filename: str) -> str:
    fields = checksum.split()
    digest = hashlib.sha256(raw).hexdigest()
    if len(fields) != 2 or fields[0] != digest or fields[1].lstrip('*') != filename:
        raise ValueError('official checksum/name mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist() != [filename[:-4] + '.csv']:
            raise ValueError('unexpected archive member')
        if archive.testzip() is not None:
            raise ValueError('archive CRC mismatch')
    return digest


def capture(output: Path, symbol: str, period: str, *, daily: bool = False) -> dict:
    if symbol not in SYMBOLS:
        raise ValueError('symbol not whitelisted')
    datetime.strptime(period, '%Y-%m-%d' if daily else '%Y-%m')
    frequency = 'daily' if daily else 'monthly'
    name = f'{symbol}-1m-{period}.zip'
    url = f'https://data.binance.vision/data/spot/{frequency}/klines/{symbol}/1m/{name}'
    output.mkdir(parents=True, exist_ok=True)
    path = output / name
    metadata = output / (name + '.json')
    if path.exists() or metadata.exists():
        if not (path.exists() and metadata.exists()):
            raise ValueError('incomplete existing capture; inspect, do not overwrite')
        record = json.loads(metadata.read_text())
        if record['url'] != url:
            raise ValueError('capture identity drift')
        verify_archive(path.read_bytes(), record['official_checksum'], name)
        return record
    started = time.time_ns() // 1_000_000
    with urlopen(url + '.CHECKSUM', timeout=45) as response:
        checksum = response.read().decode('ascii')
    with urlopen(url, timeout=90) as response:
        raw = response.read()
    digest = verify_archive(raw, checksum, name)
    record = dict(url=url, sha256=digest, official_checksum=checksum,
                  fetched_started_ms=started, fetched_completed_ms=time.time_ns() // 1_000_000,
                  bytes=len(raw), historical_revision_status='UNKNOWN',
                  source_availability='RETROSPECTIVE_ARCHIVE', symbol=symbol)
    with path.open('xb') as stream:
        stream.write(raw)
    with metadata.open('x') as stream:
        json.dump(record, stream, indent=2)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--symbols', nargs='+', choices=SYMBOLS, required=True)
    args = parser.parse_args()
    tasks = []
    for symbol in args.symbols:
        for year in range(2022, 2027):
            for month in range(1, 13):
                period = f'{year}-{month:02}'
                if '2022-11' <= period <= '2026-07':
                    tasks.append((symbol, period, False))
        tasks.append((symbol, '2026-08-01', True))
    def work(task):
        symbol, period, daily = task
        try:
            result = capture(args.output, symbol, period, daily=daily)
            print(symbol, period, result['sha256'], flush=True)
            return result
        except Exception as exc:
            print(symbol, period, type(exc).__name__, str(exc), flush=True)
            return {'symbol': symbol, 'period': period, 'error': str(exc)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(work, tasks))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    with (args.output / f'capture-manifest-{stamp}.json').open('x') as stream:
        json.dump(records, stream, indent=2)
    if any('error' in row for row in records):
        raise SystemExit('Some archives unavailable; inspect manifest, no fabricated bars.')


if __name__ == '__main__':
    main()
