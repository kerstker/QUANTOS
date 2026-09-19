import pytest
from tools.kraken_order_preflight import build
from tests.live.test_kraken_wire import order, NOW
from tests.tools.test_kraken_public_capture import capture


def test_preflight_binds_fresh_source_and_never_claims_venue_validation():
    raw,_=capture();report=build(order(),raw,as_of_ms=NOW+100)
    assert report['market_capture_hash']==raw['result_hash']
    assert report['status']=='OFFLINE_PROTOCOL_CHECKED' and not report['venue_validated']
    assert 'validate=true' in report['request_body'] and report['submission_enabled'] is False


def test_old_or_modified_public_spec_is_not_a_valid_preflight_source():
    raw,_=capture()
    with pytest.raises(ValueError):build(order(),raw,as_of_ms=NOW+200000)
    raw['instrument']['ordermin']='0'
    with pytest.raises(ValueError):build(order(),raw,as_of_ms=NOW+100)
