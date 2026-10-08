"""Bounded listing of the exact saved inputs consumed by gold campaigns."""
import hashlib
import os
from pathlib import Path
import stat

from services.research.gold_cost_inputs import MAX_BYTES, load_inputs


def _directory(path):
    current = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).absolute().parts[1:]:
            if part in ('.', '..'):
                raise ValueError('unsafe input directory')
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _read(directory, name, allowance):
    with os.fdopen(os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= min(MAX_BYTES, allowance)):
            raise ValueError('input file unavailable')
        raw = stream.read(before.st_size + 1)
        after = os.fstat(stream.fileno())
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        def identity(s):
            return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(after) or identity(after) != identity(named):
            raise ValueError('input file changed')
        return raw


def catalog(root):
    result = {'status': 'NO_SNAPSHOT', 'items': [], 'errors': [], 'truncated': False}
    try:
        directory = _directory(root / 'data/cache/ai_factory/gold-inputs')
    except FileNotFoundError:
        return result
    except (OSError, ValueError):
        return {**result, 'status': 'DATA_BLOCKED', 'errors': ['GOLD_INPUT_DIRECTORY_UNAVAILABLE']}
    budget = 64_000_000
    examined = 0
    try:
        for input_id in range(1000, 0, -1):
            if examined >= 32 or budget < MAX_BYTES:
                result['truncated'] = True
                break
            try:
                raw = _read(directory, f'{input_id}.json', budget)
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                examined += 1
                result['errors'].append(f'INPUT_{input_id}_UNAVAILABLE')
                continue
            examined += 1
            budget -= len(raw)
            try:
                args = load_inputs(raw)
                document = args['document']
                start = document['start_ms']
                times = document['decision_times_ms']
                if (type(start) is not int or start < 0 or type(times) is not list or not times
                        or any(type(t) is not int or t < start for t in times)
                        or not args['contract_rules']
                        or any(rule.spec.instrument_id != 'XAUUSDT' for rule in args['contract_rules'])):
                    raise ValueError('input scope or times invalid')
                result['items'].append({'input_id': input_id, 'input_sha256': hashlib.sha256(raw).hexdigest(),
                    'bytes': len(raw), 'start_ms': start, 'end_ms': max(times),
                    'frame_count': len(args['frames']), 'decision_count': len(times), 'source_verified': False})
            except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
                result['errors'].append(f'INPUT_{input_id}_INVALID')
    finally:
        os.close(directory)
    result['status'] = 'AVAILABLE' if result['items'] else 'DATA_BLOCKED' if result['errors'] else 'NO_SNAPSHOT'
    return result
