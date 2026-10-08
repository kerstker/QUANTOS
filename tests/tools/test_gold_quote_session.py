import hashlib
import json

import pytest

from tools import gold_quote_session as tool


def test_session_retains_ordered_originals_and_refuses_output_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, 'source_identity', lambda: {'fixture':True})
    def capture(**kwargs):
        for n in range(3):
            kwargs['on_record']({'kind':'depth','received_at':n})
        return dict(sample_count=3, quote_coverage={'gap_count':0}, schema_version='GOLD_PUBLIC_QUOTE_SESSION_V1')
    monkeypatch.setattr(tool, 'capture_gold_session', capture)
    output = tmp_path/'session'
    result = tool.run(output, seconds=30)
    receipts = sorted(output.glob('receipt-*.json'))
    assert len(receipts) == 3
    assert result['receipt_chain_sha256'] == hashlib.sha256(b''.join(
        hashlib.sha256(p.read_bytes()).digest() for p in receipts)).hexdigest()
    assert result['replay_ready'] is False and result['submission_enabled'] is False
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())
    with pytest.raises(FileExistsError):
        tool.run(output, seconds=30)


def test_session_failure_keeps_receipts_and_does_not_publish_success(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, 'source_identity', lambda: {'fixture':True})
    def capture(**kwargs):
        kwargs['on_record']({'kind':'mark','received_at':1})
        kwargs['on_error']({'error_type':'TimeoutError'})
        raise TimeoutError()
    monkeypatch.setattr(tool, 'capture_gold_session', capture)
    output=tmp_path/'session'
    with pytest.raises(TimeoutError):
        tool.run(output, seconds=30)
    failure=json.loads((output/'failure.json').read_text())
    assert failure['status']=='INCOMPLETE' and failure['receipts']==1 and failure['errors']==1
    assert (output/'receipt-000000.json').exists()
    assert not (output/'summary.json').exists()
