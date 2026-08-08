from __future__ import annotations

from dataclasses import asdict

import pytest

from live.governance.mandate import Mandate
from live.governance.testnet_mandate_proposal import (
    MandateProposalError,
    build_testnet_mandate_proposal,
    derive_testnet_mandate_ceiling,
    validate_mandate_within_ceiling,
)
from quantos.contracts.hashing import stable_hash
from quantos.contracts.mft_execution import create_testnet_execution_intent
from quantos.contracts.portfolio import TargetPosition
from services.risk.pre_trade.config import RiskLimits


NOW = 2_000_000


def _intent(*, requested: bool = True):
    return create_testnet_execution_intent(
        portfolio_id="hourly-paper",
        alpha_ids=("a" * 64,),
        decided_at_ms=NOW - 10,
        expires_at_ms=NOW + 10_000,
        targets=(TargetPosition(instrument_id="BTC", target_weight=0.1),),
        market_watermark_ms=NOW - 50,
        market_snapshot_hash="1" * 64,
        workflow_receipt_hash="2" * 64,
        validation_artifact_hashes=("3" * 64,),
        policy_version="mft-testnet.v1",
        policy_hash="4" * 64,
        testnet_submission_requested=requested,
    )


def _runtime() -> dict[str, object]:
    limits = asdict(
        RiskLimits(
            allowed_instruments=frozenset({"BTC", "ETH"}),
            allowed_accounts=frozenset({"testnet-account"}),
            max_order_notional=25_000.0,
            max_gross_exposure=80_000.0,
            max_net_exposure=40_000.0,
        )
    )
    limits["allowed_instruments"] = ["BTC", "ETH"]
    limits["allowed_accounts"] = ["testnet-account"]
    limits["max_position_qty"] = []
    return {
        "schema_version": "mft-testnet-runtime.v1",
        "market_snapshot_hash": "1" * 64,
        "market_watermark_ms": NOW - 50,
        "risk_policy_version": "operator-risk.v1",
        "risk_limits": limits,
    }


def _mandate(**overrides) -> Mandate:
    values = {
        "mandate_id": "human-testnet-mandate",
        "venue": "testnet",
        "exchange": "binance",
        "allowed_instruments": frozenset({"BTC"}),
        "max_single_order_notional": 25_000.0,
        "max_gross_notional": 80_000.0,
        "max_net_notional": 40_000.0,
        "approved_by": ("human-operator",),
        "issued_at": NOW - 1_000,
        "expires_at": NOW + 10_000,
    }
    values.update(overrides)
    return Mandate(**values)


def test_proposal_is_derived_from_the_bound_runtime_policy_not_human_numbers() -> None:
    runtime = _runtime()
    ceiling = derive_testnet_mandate_ceiling(_intent(), runtime)
    proposal = build_testnet_mandate_proposal(ceiling, generated_at_ms=NOW)

    assert ceiling.allowed_instruments == ("BTC", "ETH")
    assert ceiling.max_single_order_notional == 25_000.0
    assert ceiling.max_gross_notional == 80_000.0
    assert ceiling.max_net_notional == 40_000.0
    assert ceiling.runtime_input_hash == stable_hash(runtime)
    assert proposal["status"] == "HUMAN_REVIEW_REQUIRED"
    assert proposal["execution_authority"] is False
    assert proposal["micro_live_authority"] is False
    assert proposal["unsigned_mandate"]["approved_by"] == []
    assert proposal["unsigned_mandate"]["expires_at"] == 0
    assert proposal["unsigned_mandate"]["max_gross_notional"] == 80_000.0
    proposal_body = dict(proposal)
    proposal_hash = proposal_body.pop("proposal_hash")
    assert proposal_hash == stable_hash(proposal_body)


@pytest.mark.parametrize(
    "mandate",
    [
        _mandate(max_single_order_notional=25_000.01),
        _mandate(max_gross_notional=80_000.01),
        _mandate(max_net_notional=40_000.01),
        _mandate(allowed_instruments=frozenset({"BTC", "SOL"})),
        _mandate(venue="mainnet"),
        _mandate(exchange="bybit"),
    ],
)
def test_final_mandate_cannot_expand_the_system_ceiling(mandate: Mandate) -> None:
    ceiling = derive_testnet_mandate_ceiling(_intent(), _runtime())

    with pytest.raises(MandateProposalError):
        validate_mandate_within_ceiling(mandate, ceiling)


def test_final_mandate_may_only_tighten_the_system_ceiling() -> None:
    ceiling = derive_testnet_mandate_ceiling(_intent(), _runtime())
    mandate = _mandate(
        max_single_order_notional=10_000.0,
        max_gross_notional=30_000.0,
        max_net_notional=20_000.0,
        per_instrument_max_notional=(("BTC", 15_000.0),),
    )

    validate_mandate_within_ceiling(mandate, ceiling)


def test_proposal_rejects_unrequested_or_unbound_runtime_inputs() -> None:
    with pytest.raises(MandateProposalError, match="submission_requested"):
        derive_testnet_mandate_ceiling(_intent(requested=False), _runtime())

    mismatch = _runtime()
    mismatch["market_snapshot_hash"] = "f" * 64
    with pytest.raises(MandateProposalError, match="snapshot"):
        derive_testnet_mandate_ceiling(_intent(), mismatch)

    disallowed = _runtime()
    disallowed["risk_limits"] = dict(disallowed["risk_limits"])
    disallowed["risk_limits"]["allowed_instruments"] = ["ETH"]
    with pytest.raises(MandateProposalError, match="allowed"):
        derive_testnet_mandate_ceiling(_intent(), disallowed)

    ceiling = derive_testnet_mandate_ceiling(_intent(), _runtime())
    with pytest.raises(MandateProposalError, match="validity window"):
        build_testnet_mandate_proposal(ceiling, generated_at_ms=NOW + 10_000)
