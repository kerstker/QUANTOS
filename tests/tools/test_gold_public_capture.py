import json

import pytest

from tests.research.test_gold_trade_windows import packet
from tools.gold_public_capture import PublicCapture, CaptureError, save_capture


def message(identifier=1, at=1000):
    return json.dumps({'stream':'xauusdt@aggTrade','data':packet(identifier,at,'4000')['payload']})


def test_capture_keeps_raw_frames_and_validates_every_packet(tmp_path):
    c=PublicCapture(started_at_ms=900)
    raw=message(); c.ingest(raw,received_at_ms=1010)
    c.ingest(raw,received_at_ms=1011)
    r=c.finish(ended_at_ms=1100,reason='DURATION_REACHED')
    assert r['status']=='CAPTURED_PUBLIC_PREFIX'
    assert r['raw_frames']==[raw,raw]
    assert len(r['packets'])==2 and r['unique_aggregates']==1
    assert r['history_complete'] is False and r['authoritative'] is False
    dest=tmp_path/'capture.json'; save_capture(dest,r)
    before=dest.read_bytes()
    with pytest.raises(FileExistsError): save_capture(dest,r)
    assert dest.read_bytes()==before


def test_gap_retains_failed_evidence_and_cannot_resume_as_healthy():
    c=PublicCapture(started_at_ms=900)
    c.ingest(message(),received_at_ms=1010)
    with pytest.raises(CaptureError): c.ingest(message(3,1020),received_at_ms=1030)
    r=c.finish(ended_at_ms=1040,reason='DURATION_REACHED')
    assert r['status']=='INVALID_STREAM'
    assert r['unique_aggregates']==1 and len(r['raw_frames'])==2
    with pytest.raises(CaptureError): c.ingest(message(2,1015),received_at_ms=1040)


def test_disconnect_and_no_data_are_not_healthy_capture():
    c=PublicCapture(started_at_ms=900)
    assert c.finish(ended_at_ms=1000,reason='DURATION_REACHED')['status']=='NO_DATA'
    c=PublicCapture(started_at_ms=900); c.ingest(message(),received_at_ms=1010)
    assert c.finish(ended_at_ms=1100,reason='STREAM_INTERRUPTED')['status']=='INTERRUPTED'


def test_wrong_stream_duplicate_keys_and_timestamp_reversal_rejected():
    for raw in [message().replace('xauusdt@aggTrade','btcusdt@aggTrade'),
                '{"stream":1,"stream":2}']:
        with pytest.raises(CaptureError):
            PublicCapture(started_at_ms=900).ingest(raw,received_at_ms=1010)
    with pytest.raises(CaptureError):
        PublicCapture(started_at_ms=1100).ingest(message(),received_at_ms=1010)


def test_existing_output_refused_before_any_network(tmp_path,monkeypatch,capsys):
    from tools import gold_public_capture as tool
    path=tmp_path/'existing.json'; path.write_text('preserve')
    def forbidden(*args):
        pytest.fail('network should not start')
    monkeypatch.setattr(tool,'collect',forbidden)
    assert tool.main(['--output',str(path),'--seconds','1'])==2
    assert path.read_text()=='preserve'


def test_closed_capture_cannot_mutate_saved_fact_set():
    c=PublicCapture(started_at_ms=900); c.ingest(message(),received_at_ms=1010)
    r=c.finish(ended_at_ms=1020,reason='DURATION_REACHED')
    with pytest.raises(CaptureError): c.ingest(message(2,1030),received_at_ms=1040)
    assert len(r['packets'])==1
