"""验证证明门(live/governance/validation_attestation.py):真提交前的策略级准入——必须在案有
PASS 验证或可审计人工 override,否则 fail-closed 拒绝。审计 P1-1。"""
import json
import os

import pytest

from live.governance.validation_attestation import (HUMAN_OVERRIDE, VALIDATION_PASS,
                                                     ValidationAttestation,
                                                     ValidationAttestationError,
                                                     load_attestations,
                                                     require_validation_attestation)


def _override(strategy_id="crowd_contra", reason="deployed via IC panel + dry-run + human judgment"):
    return ValidationAttestation(strategy_id=strategy_id, kind=HUMAN_OVERRIDE, attested_by="alice",
                                 attested_at=1_700_000_000_000, reason=reason)


def _validation_pass(strategy_id="s"):
    return ValidationAttestation(strategy_id=strategy_id, kind=VALIDATION_PASS, attested_by="alice",
                                 attested_at=1_700_000_000_000, validation_result_hash="abc123")


# --- require_validation_attestation ---

def test_none_attestations_rejects_fail_closed():
    assert require_validation_attestation("crowd_contra", None) == "no_validation_attestation:crowd_contra"


def test_missing_strategy_rejects():
    r = require_validation_attestation("crowd_contra", {"other": _override("other")})
    assert r == "no_validation_attestation:crowd_contra"


def test_valid_human_override_passes():
    assert require_validation_attestation("crowd_contra", {"crowd_contra": _override()}) is None


def test_valid_validation_pass_passes():
    assert require_validation_attestation("s", {"s": _validation_pass()}) is None


def test_unknown_kind_rejects():
    att = ValidationAttestation("s", kind="hearsay", attested_by="alice", attested_at=1)
    assert require_validation_attestation("s", {"s": att}) == "unknown_attestation_kind:hearsay"


def test_unsigned_attestation_rejects():
    att = ValidationAttestation("s", kind=HUMAN_OVERRIDE, attested_by="", attested_at=1, reason="x")
    assert require_validation_attestation("s", {"s": att}) == "attestation_unsigned:s"


def test_validation_pass_without_result_hash_rejects():
    att = ValidationAttestation("s", kind=VALIDATION_PASS, attested_by="alice", attested_at=1)
    assert require_validation_attestation("s", {"s": att}) == "validation_pass_without_result_hash:s"


def test_human_override_without_reason_rejects():
    att = ValidationAttestation("s", kind=HUMAN_OVERRIDE, attested_by="alice", attested_at=1, reason="")
    assert require_validation_attestation("s", {"s": att}) == "human_override_without_reason:s"


def test_evaluation_exception_fails_closed():
    class _Boom:
        def get(self, _k):
            raise RuntimeError("boom")
    r = require_validation_attestation("s", _Boom())
    assert r.startswith("attestation_error:")


# --- load_attestations ---

def test_missing_file_is_empty_dict_not_error():
    # 缺文件 -> {} (require 因此对每个策略都拒绝 = fail-closed 全升人工),不是 raise
    assert load_attestations(os.path.join("nonexistent", "attest.json")) == {}


def test_missing_file_then_every_strategy_rejected(tmp_path):
    atts = load_attestations(os.path.join(str(tmp_path), "absent.json"))
    assert require_validation_attestation("crowd_contra", atts) == "no_validation_attestation:crowd_contra"


def _write(tmp_path, obj):
    p = os.path.join(str(tmp_path), "attest.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return p


def test_load_valid_override_roundtrips(tmp_path):
    p = _write(tmp_path, {"crowd_contra": {"kind": "human_override", "attested_by": "alice",
                                           "attested_at": 1_700_000_000_000, "reason": "IC+dry-run+human"}})
    atts = load_attestations(p)
    assert require_validation_attestation("crowd_contra", atts) is None
    assert atts["crowd_contra"].attested_by == "alice"


def test_load_missing_required_field_raises(tmp_path):
    p = _write(tmp_path, {"s": {"kind": "human_override", "attested_by": "alice"}})   # 缺 attested_at
    with pytest.raises(ValidationAttestationError):
        load_attestations(p)


def test_load_non_object_entry_raises(tmp_path):
    p = _write(tmp_path, {"s": "not-an-object"})
    with pytest.raises(ValidationAttestationError):
        load_attestations(p)


def test_load_non_integer_attested_at_raises(tmp_path):
    p = _write(tmp_path, {"s": {"kind": "human_override", "attested_by": "a", "attested_at": "soon"}})
    with pytest.raises(ValidationAttestationError):
        load_attestations(p)


def test_load_top_level_not_object_raises(tmp_path):
    p = _write(tmp_path, ["not", "a", "dict"])
    with pytest.raises(ValidationAttestationError):
        load_attestations(p)


def test_template_fails_closed_if_loaded_unedited(tmp_path):
    """签署模板若被原样复制成真实文件(占位符未填),必须 fail-closed:attested_at 是非整数占位符
    -> load_attestations raise -> 驱动器视同无凭证全部 BLOCK。防止"忘了填就上真钱"。"""
    from tools.create_validation_attestation_template import build_template
    p = _write(tmp_path, build_template())
    with pytest.raises(ValidationAttestationError):
        load_attestations(p)
