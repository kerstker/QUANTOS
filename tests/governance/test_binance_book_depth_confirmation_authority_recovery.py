from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "config" / "binance_book_depth_confirmation_authority_recovery_v1.json"


def test_recovery_contract_is_exact_and_cannot_authorize() -> None:
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    old = payload["old_authority"]
    reconstruction = payload["reconstruction"]
    acceptance = payload["exact_acceptance"]
    authorization = payload["authorization"]

    assert payload["status"] == "FROZEN_BEFORE_RECONSTRUCTION_OR_OUTCOME_ACCESS"
    assert old["head_seq"] == 61152
    assert old["head_row_hash"] == "15270ff476d19526369489f6133ff5490b59975e7622fea5ea8a7dd13216cb66"
    assert old["recoverable"] is False
    assert old["claim_new_chain_equals_old_chain"] is False
    assert reconstruction["day_count"] == 97
    assert len(reconstruction["universe"]) == 14
    assert reconstruction["official_checksum_required"] is True
    assert reconstruction["mark_price_checksum_required"] is True
    assert reconstruction["old_chain_identity_must_not_be_claimed"] is True
    assert acceptance == {
        "complete_day_count": 91,
        "excluded_day_count": 6,
        "excluded_days_ms": [
            1770336000000,
            1774483200000,
            1775606400000,
            1776297600000,
            1776816000000,
            1776988800000,
        ],
        "parent_complete_day_count": 85,
        "supplement_complete_day_count": 6,
        "actual_adjacent_24h_decision_count": 84,
        "accepted_observation_count": 30576,
        "all_97_days_must_be_processed": True,
        "excluded_days_are_never_imputed": True,
        "any_mismatch_blocks_before_alpha_outcome": True,
    }
    assert set(authorization.values()) == {False}
