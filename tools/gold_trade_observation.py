"""Read operator-provided fills; optionally retain a private local archive.

Run with python -m tools.gold_trade_observation --input /path/to/fills.json.
No network, credentials, order submission or paper persistence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from services.research.gold_trade_observation import (
    MAX_BYTES, ObservationError, assess, load_document, normalize_binance_trades,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = ('tools/gold_trade_observation.py',
                'services/research/gold_trade_observation.py')


def _source_hashes():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
            for name in SOURCE_PATHS}


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _evaluate(raw, input_format):
    if input_format not in ('normalized', 'binance-user-trades'):
        raise ObservationError('INVALID_INPUT_FORMAT')
    document = load_document(raw)
    if input_format == 'binance-user-trades':
        document = normalize_binance_trades(document)
    report = assess(document)
    report['input_sha256'] = hashlib.sha256(raw).hexdigest()
    return document, report


def _write_private(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def _manifest(input_format, hashes, source):
    return {'schema': 'GOLD_FILL_ARCHIVE_V1', 'input_format': input_format,
            'files': hashes, 'source_sha256': source, 'history_complete': False,
            'source_authenticated': False, 'authoritative': False,
            'mode': 'OFFLINE_DECLARED_ACCOUNT_FACTS'}


def archive_observation(raw, destination, *, input_format='normalized'):
    """Reserve a new private directory; write completion manifest last.

    Validate before persistence so unsupported/secret-bearing inputs are not
    copied. Partial I/O attempts are retained without a completion manifest.
    This is a local diagnostic archive, not authenticated or WORM evidence.
    """
    source = _source_hashes()
    document, report = _evaluate(raw, input_format)
    files = {'input.json': raw, 'fills.json': _encode(document), 'report.json': _encode(report)}
    if any(len(content) > MAX_BYTES for content in files.values()):
        raise ObservationError('ARCHIVE_FILE_TOO_LARGE')
    destination = Path(destination).absolute()
    if destination.resolve() != destination:
        raise ObservationError('LINKED_ARCHIVE')
    destination.mkdir(mode=0o700)
    for name, content in files.items():
        _write_private(destination/name, content)
    if source != _source_hashes():
        raise ObservationError('SOURCE_CHANGED_DURING_ARCHIVE')
    manifest = _manifest(input_format,
        {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}, source)
    _write_private(destination/'manifest.json', _encode(manifest))
    descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return report


def verify_archive(directory):
    """Read only, recomputing facts and report under the same current sources."""
    root = Path(directory).absolute()
    if root.resolve() != root:
        raise ObservationError('LINKED_ARCHIVE')
    def read(name):
        path = root/name
        if path.resolve() != path or not path.is_file():
            raise ObservationError('INVALID_ARCHIVE_FILE')
        with path.open('rb') as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ObservationError('INVALID_INPUT_SIZE')
        return raw
    manifest = load_document(read('manifest.json'))
    if not isinstance(manifest, dict):
        raise ObservationError('INVALID_ARCHIVE_MANIFEST')
    source = _source_hashes()
    if manifest.get('source_sha256') != source:
        raise ObservationError('SOURCE_VERSION_MISMATCH')
    files = {name: read(name) for name in ('input.json', 'fills.json', 'report.json')}
    document, report = _evaluate(files['input.json'], manifest.get('input_format'))
    if (files['fills.json'] != _encode(document) or files['report.json'] != _encode(report)):
        raise ObservationError('ARCHIVE_RECOMPUTATION_MISMATCH')
    expected = _manifest(manifest.get('input_format'),
        {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}, source)
    if _encode(expected) != _encode(manifest):
        raise ObservationError('ARCHIVE_MANIFEST_MISMATCH')
    if source != _source_hashes():
        raise ObservationError('SOURCE_CHANGED_DURING_VERIFICATION')
    return {'status': 'VERIFIED_LOCAL_FILL_ARCHIVE', 'input_sha256': report['input_sha256'],
            'facts_sha256': report['facts_sha256'], 'unique_fills': report['unique_fills'],
            'history_complete': False, 'source_authenticated': False, 'authoritative': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--input', type=Path)
    action.add_argument('--verify-dir', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--format', choices=('normalized', 'binance-user-trades'),
                        default='normalized')
    args = parser.parse_args(argv)
    if args.verify_dir is not None and (args.output_dir is not None or args.format != 'normalized'):
        parser.error('--verify-dir cannot be combined with --output-dir or --format')
    try:
        if args.verify_dir is not None:
            report = verify_archive(args.verify_dir)
        else:
            with args.input.open('rb') as source:
                raw = source.read(MAX_BYTES + 1)
            if args.output_dir is not None:
                report = archive_observation(raw, args.output_dir, input_format=args.format)
            else:
                _, report = _evaluate(raw, args.format)
    except FileExistsError:
        print(json.dumps({'status': 'REJECTED', 'reason': 'ARCHIVE_ALREADY_EXISTS'}))
        return 2
    except OSError:
        reason = 'FILE_OPERATION_FAILED' if args.output_dir or args.verify_dir else 'INPUT_UNREADABLE'
        print(json.dumps({'status': 'REJECTED', 'reason': reason}))
        return 2
    except ObservationError as exc:
        print(json.dumps({'status': 'REJECTED', 'reason': str(exc)}))
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
