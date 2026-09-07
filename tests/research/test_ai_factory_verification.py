import pytest
from tests.governance.test_artifact_receipt_auth import _signed_envelope
from services.validation.ai_factory_verification import verify_research_receipt


def test_unconfigured_authority_does_not_admit_local_evidence():
    r=verify_research_receipt('a'*64,None,None,now=1700000000000)
    assert r['status']=='INDEPENDENT_AUTHORITY_UNCONFIGURED' and r['economic_eligible'] is False


def test_valid_signature_only_establishes_engineering_binding():
    envelope,key=_signed_envelope()
    r=verify_research_receipt('a'*64,envelope,key,now=1700000000000)
    assert r['status']=='VERIFIED_SIGNATURE_ENGINEERING_ONLY' and not r['economic_eligible']
    with pytest.raises(ValueError):verify_research_receipt('f'*64,envelope,key,now=1700000000000)
    with pytest.raises(ValueError):verify_research_receipt('a'*64,envelope,key,now=1700200000000)
    with pytest.raises(ValueError):verify_research_receipt('a'*64,envelope,key,now=1699000000000)
