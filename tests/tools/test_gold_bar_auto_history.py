import hashlib
import json

import pytest


@pytest.mark.parametrize('partial', [False, True])
def test_auto_history_is_captured_once_and_partial_attempt_is_not_replaced(tmp_path, monkeypatch, partial):
    from tools import gold_bar_auto_history as automatic
    from tests.research.test_gold_bar_dataset import raw_rows, STEP
    raw = json.dumps(raw_rows()).encode()
    digest = hashlib.sha256(raw).hexdigest()
    calls = []
    def capture(path, *, protected_network):
        assert protected_network is True
        calls.append(1)
        path.mkdir(mode=0o700)
        (path/'request.json').write_text('{}')
        if partial:
            raise ValueError('CAPTURE_FAILED')
        values = {'receipt.json':dict(response_sha256=digest, wire_sha256=digest,
            requested_at_ms=1000*STEP+100, received_at_ms=1000*STEP+200,
            content_encoding='identity', network_mode='PROTECTED_DEMO_NETWORK_V1'),
            'summary.json':dict(schema='GOLD_BAR_HISTORY_CAPTURE_V1', status='CAPTURED_FOR_RESEARCH',
                source_sha256=digest, count=1000, submission_enabled=False)}
        for name, value in values.items():
            (path/name).write_text(json.dumps(value))
        for name in ('response.json', 'response.wire'):
            (path/name).write_bytes(raw)
        for item in path.iterdir():
            item.chmod(0o600)
    monkeypatch.setattr(automatic, 'capture', capture)
    def load():
        return automatic.load(tmp_path, {'fixture':'same episode'}, uid=tmp_path.stat().st_uid)
    if partial:
        with pytest.raises(ValueError, match='CAPTURE_FAILED'):
            load()
        with pytest.raises((ValueError, FileNotFoundError)):
            load()
    else:
        path = load()
        assert load() == path
        assert automatic.load(tmp_path, {'fixture':'same episode'}, uid=tmp_path.stat().st_uid, read_only=True) == path
        (path/'response.json').write_bytes(b'[]')
        with pytest.raises(ValueError):
            load()
    assert calls == [1]


def test_missing_readonly_history_never_captures_or_creates(tmp_path, monkeypatch):
    from tools import gold_bar_auto_history as automatic
    monkeypatch.setattr(automatic, 'capture', lambda *a, **k: pytest.fail('read-only capture'))
    with pytest.raises((ValueError, FileNotFoundError)):
        automatic.load(tmp_path, {'fixture': 'absent'}, uid=tmp_path.stat().st_uid, read_only=True)
    assert list(tmp_path.iterdir()) == []
