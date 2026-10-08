import copy
import pytest
from services.data.gold_capture_snapshot import summarize_capture
from tools.gold_public_capture import PublicCapture
from tests.tools.test_gold_public_capture import message

def captured():
    c=PublicCapture(started_at_ms=900)
    c.ingest(message(),received_at_ms=1010)
    return c.finish(ended_at_ms=1100,reason='DURATION_REACHED')

def test_recent_public_record_does_not_claim_live_connection():
    r=summarize_capture(captured(),as_of_ms=1200)
    assert r['status']=='OBSERVED_RECENTLY'
    assert r['last_trade_price']=='4000' and r['unique_aggregates']==1
    assert r['stream_connected'] is False and r['source_authenticated'] is False

def test_duplicate_and_new_archive_end_cannot_freshen_old_trade():
    c=PublicCapture(started_at_ms=900)
    c.ingest(message(),received_at_ms=1010)
    c.ingest(message(),received_at_ms=40000)
    r=summarize_capture(c.finish(ended_at_ms=40000,reason='DURATION_REACHED'),as_of_ms=40001)
    assert r['status']=='STALE' and r['last_received_ms']==1010

@pytest.mark.parametrize('case',['future','hash','payload','count','sequence','authority'])
def test_inconsistent_or_future_capture_is_rejected(case):
    d=captured()
    if case=='future':d['ended_at_ms']=1300
    if case=='hash':d['raw_frames_sha256']='a'*64
    if case=='payload':d['packets'][0]['payload']['q']='99'
    if case=='count':d['unique_aggregates']=2
    if case=='sequence':d['packets'].append(copy.deepcopy(d['packets'][0]))
    if case=='authority':d['authoritative']=True
    with pytest.raises(ValueError):summarize_capture(d,as_of_ms=1200)

def test_interrupted_capture_stays_interrupted_even_when_recent():
    c=PublicCapture(started_at_ms=900);c.ingest(message(),received_at_ms=1010)
    assert summarize_capture(c.finish(ended_at_ms=1100,reason='STREAM_INTERRUPTED'),as_of_ms=1200)['status']=='INTERRUPTED'

def test_empty_capture_has_no_price():
    d=PublicCapture(started_at_ms=900).finish(ended_at_ms=1100,reason='DURATION_REACHED')
    r=summarize_capture(d,as_of_ms=1200)
    assert r['status']=='NO_DATA' and r['last_trade_price'] is None
