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


def test_preflight_replays_account_fee_schedule_without_granting_funds_authority():
    from tests.tools.test_kraken_fee_observation import fixture
    raw,evidence,now,_=fixture()
    report=build(order(),raw,as_of_ms=now,fee_evidence=evidence,account_ref='a'*64)
    assert report['observed_fee_schedule']['taker_or_flat']['fee']=='0.26'
    assert report['fee_evidence_hash']==evidence['result_hash']
    assert report['observed_fee_schedule']['fee_asset_status']=='UNKNOWN'
    assert 'ACCOUNT_FEE_AND_FUNDS_NOT_BOUND' in report['blockers']
    assert report['submission_enabled'] is False
    with pytest.raises(ValueError):build(order(),raw,as_of_ms=now,fee_evidence=evidence,account_ref='b'*64)
    with pytest.raises(ValueError):build(order(),raw,as_of_ms=now,fee_evidence=evidence)
