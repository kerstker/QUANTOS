import pytest

from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS, SafetyConfig, SafetyViolation
from services.trading.broker_adapter import (BrokerAdapter, DryRunBrokerAdapter, LiveBrokerAdapter,
                                             assert_dry_run_adapter)
from services.trading.dry_run_adapters.domain.engine import DryRunAdapterEngine
from services.trading.dry_run_adapters.domain.models import DryRunCommand


def _cmd():
    return DryRunCommand(command_id="c1", adapter_type="DRY_RUN_GENERIC", instrument_id="BTC",
                         side="BUY", order_type="MARKET", quantity=1, price=100.0,
                         risk_approval_ref="r", pre_trade_risk_ref="r", oms_state_ref="VALIDATED")


def test_dry_run_broker_adapter_satisfies_protocol():
    a = DryRunBrokerAdapter()
    assert isinstance(a, BrokerAdapter)          # runtime_checkable structural match
    assert a.is_dry_run is True


def test_dry_run_broker_adapter_delegates_to_engine():
    engine = DryRunAdapterEngine()
    a = DryRunBrokerAdapter(engine)
    run = a.submit_order(_cmd())                  # delegates to engine.process
    assert not run.rejection_reasons and run.dry_run is True
    rep = a.fetch_fill(run.run_id)                # delegates to engine.simulate_fill
    assert rep is not None and rep.dry_run is True and rep.live_trading_allowed is False


def test_live_broker_adapter_refuses_construction_at_l2():
    with pytest.raises(SafetyViolation):
        LiveBrokerAdapter(RESEARCH_SAFE_DEFAULTS)     # live_trading_armed is False -> raise


def test_live_broker_adapter_refuses_even_when_armed():
    armed = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True, allow_order_submission=True)
    assert armed.live_trading_armed is True
    with pytest.raises(SafetyViolation):
        LiveBrokerAdapter(armed)                       # no real implementation -> still raise


def test_live_broker_adapter_methods_are_fail_closed():
    # construction always raises, so bypass __init__ to prove the method bodies also refuse
    inst = object.__new__(LiveBrokerAdapter)
    assert inst.is_dry_run is False
    with pytest.raises(SafetyViolation):
        inst.submit_order(_cmd())
    with pytest.raises(SafetyViolation):
        inst.fetch_fill("x")


def test_assert_dry_run_adapter_allows_dry_run():
    assert_dry_run_adapter(DryRunAdapterEngine())     # whitelisted engine
    assert_dry_run_adapter(DryRunBrokerAdapter())     # whitelisted wrapper

    class _DuckDryRun:
        is_dry_run = True
    assert_dry_run_adapter(_DuckDryRun())             # self-attested dry-run duck


def test_assert_dry_run_adapter_rejects_unknown_fail_closed():
    with pytest.raises(SafetyViolation):
        assert_dry_run_adapter(object())              # unknown -> fail-closed
