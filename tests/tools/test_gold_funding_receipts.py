import hashlib
import json

import pytest


def poll(root, *, received=2500, rate='0.01'):
    root.mkdir()
    (root/'plan.json').write_text(json.dumps(dict(schema='GOLD_FUNDING_OBSERVATION_PLAN_V1',symbol='XAUUSDT',submission_enabled=False)))
    path=root/'poll-0001'
    path.mkdir()
    raw=json.dumps([dict(symbol='XAUUSDT',fundingTime=1502,fundingRate=rate,markPrice='100')]).encode()
    (path/'response.json').write_bytes(raw)
    (path/'receipt.json').write_text(json.dumps(dict(endpoint='/fapi/v1/fundingRate?symbol=XAUUSDT&startTime=0&endTime=2399&limit=1000',requested_at_ms=2400,received_at_ms=received,response_sha256=hashlib.sha256(raw).hexdigest()))+'\n')


def test_multiple_observers_preserve_first_real_receipt(tmp_path):
    from tools.gold_funding_receipts import receipts_for_window
    poll(tmp_path/'first')
    poll(tmp_path/'later',received=2700)
    records=receipts_for_window([tmp_path/'later',tmp_path/'first'],start_ms=1000,end_ms=3000)
    assert len(records)==1 and records[0]['received_at']==2500
    assert records[0]['payload'][0]['fundingTime']==1502
    assert receipts_for_window([tmp_path/'first'],start_ms=1000,end_ms=2000) is None


def test_conflicting_later_receipt_is_not_hidden_by_first_receipt(tmp_path):
    from tools.gold_funding_receipts import receipts_for_window
    poll(tmp_path/'first')
    poll(tmp_path/'later',received=2700,rate='0.02')
    with pytest.raises(ValueError,match='CONFLICTING'):
        receipts_for_window([tmp_path/'first',tmp_path/'later'],start_ms=1000,end_ms=3000)
