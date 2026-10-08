"""Phase 8 — P0-1 end-to-end: a REJECTED risk decision yields an empty
pre_trade_risk_ref, so the dry-run boundary fails it closed (PRE_TRADE_MISSING);
an APPROVED decision yields a usable ref. The gate is a real gatekeeper, not advisory."""
from quantos.contracts.rejection import RejectionReason
from quantos.contracts.risk import RiskDecision, RiskDecisionType
from services.risk.pre_trade.gate import approval_ref
from services.trading.dry_run_adapters.domain.engine import DryRunValidator
from services.trading.dry_run_adapters.domain.models import DryRunCommand
from services.trading.dry_run_adapters.domain.models import RejectionReason as DRReason


def _cmd(pre_trade_ref):
    return DryRunCommand(
        command_id="c1", adapter_type="DRY_RUN_CTP", instrument_id="AAA", side="BUY",
        order_type="LIMIT", quantity=10, price=100.0, risk_approval_ref="RA1",
        pre_trade_risk_ref=pre_trade_ref, oms_state_ref="VALIDATED", kill_switch_ref="",
        dry_run=True, live_trading_allowed=False,
    )


def test_rejected_decision_ref_fails_dry_run_boundary_closed():
    rejected = RiskDecision(decision=RiskDecisionType.REJECTED,
                            reasons=(RejectionReason.POSITION_LIMIT_EXCEEDED,))
    approved = RiskDecision(decision=RiskDecisionType.APPROVED)
    v = DryRunValidator()
    # rejected -> "" ref -> boundary rejects with PRE_TRADE_MISSING
    assert DRReason.PRE_TRADE_MISSING.value in v.validate(_cmd(approval_ref(rejected, "d1")))
    # approved -> usable ref -> boundary does not raise PRE_TRADE_MISSING
    assert DRReason.PRE_TRADE_MISSING.value not in v.validate(_cmd(approval_ref(approved, "d1")))
