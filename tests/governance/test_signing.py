"""出进程签名权威接缝单元测试(轨道3 子项目3)。L2 下:签不出、验不过。"""
import pytest

from services.governance.signing import (NullSigningAuthority, SignedDecisionRef,
                                          SigningAuthority, SigningUnavailable,
                                          verify_signed_ref)


def test_signed_decision_ref_is_frozen_value():
    ref = SignedDecisionRef(payload_hash="ph", signature="sig", key_id="k1", issued_by="authority-x")
    assert ref.payload_hash == "ph"
    with pytest.raises(Exception):        # frozen
        ref.signature = "other"


def test_null_authority_conforms_to_protocol_but_refuses_to_sign():
    a = NullSigningAuthority()
    assert isinstance(a, SigningAuthority)         # runtime_checkable 结构一致
    assert a.is_out_of_process is False
    with pytest.raises(SigningUnavailable):
        a.sign("payload-hash")


def test_verify_is_fail_closed_without_out_of_process_authority():
    ref = SignedDecisionRef(payload_hash="ph", signature="sig", key_id="k1", issued_by="x")
    assert verify_signed_ref(ref, "ph", None) is False                      # 无权威
    assert verify_signed_ref(ref, "ph", NullSigningAuthority()) is False     # 非出进程权威
    assert verify_signed_ref(None, "ph", NullSigningAuthority()) is False    # 无 ref


def test_verify_refuses_even_a_spoofed_out_of_process_authority():
    # 受信代码即便伪造一个 is_out_of_process=True 的权威,也过不去(L2 无真验签能力)——fail-closed
    class SpoofAuthority:
        is_out_of_process = True
        key_id = "spoof"
        def sign(self, payload_hash):        # pragma: no cover
            return SignedDecisionRef(payload_hash=payload_hash, signature="x", key_id="spoof", issued_by="spoof")
    ref = SignedDecisionRef(payload_hash="ph", signature="x", key_id="spoof", issued_by="spoof")
    assert verify_signed_ref(ref, "ph", SpoofAuthority()) is False
