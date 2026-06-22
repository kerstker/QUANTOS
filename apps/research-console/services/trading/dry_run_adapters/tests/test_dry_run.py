"""Dry-Run Adapters 鈥?Unit Tests (Phase 2G). Dry-run only. No CTP/FIX SDK."""
import pytest, random, time, os, ast
from ..domain.models import *
from ..domain.engine import *

random.seed(42)

def _cmd(**kw):
    d = DryRunCommand(command_id="cmd-1", adapter_type="DRY_RUN_CTP", ems_run_id="ems-1",
        parent_order_id="po-1", child_order_id="ch-1", instrument_id="inst_1",
        side="BUY", order_type="LIMIT", quantity=10000, price=10.0,
        risk_approval_ref="risk-ok", pre_trade_risk_ref="ptr-ok",
        oms_state_ref="VALIDATED", ems_state_ref="SIMULATING",
        kill_switch_ref="DISENGAGED", idempotency_key="idem-1",
        dry_run=True, live_trading_allowed=False)
    for k, v in kw.items(): setattr(d, k, v)
    return d

class TestValidate:
    def test_valid(self):
        r = DryRunValidator().validate(_cmd()); assert len(r) == 0, f"Got reasons: {r}"
    def test_live_not_allowed(self):
        r = DryRunValidator().validate(_cmd(live_trading_allowed=True)); assert len(r) > 0
    def test_missing_cmd(self):
        r = DryRunValidator().validate(None); assert len(r) > 0
    def test_missing_dry_run(self):
        r = DryRunValidator().validate(_cmd(dry_run=False)); assert len(r) > 0
    def test_missing_risk_approval(self):
        r = DryRunValidator().validate(_cmd(risk_approval_ref="")); assert len(r) > 0
    def test_missing_pre_trade(self):
        r = DryRunValidator().validate(_cmd(pre_trade_risk_ref="")); assert len(r) > 0
    def test_kill_switch_active(self):
        r = DryRunValidator().validate(_cmd(kill_switch_ref="ACTIVE")); assert len(r) > 0
    def test_unsupported_type(self):
        r = DryRunValidator().validate(_cmd(adapter_type="REAL_CTP")); assert len(r) > 0
    def test_duplicate(self):
        r = DryRunValidator().validate(_cmd(), existing_ids=["cmd-1"]); assert len(r) > 0

class TestMapping:
    def test_ctp(self):
        proto = ProtocolMapper().map_ctp(_cmd())
        assert proto.adapter_type == AdapterType.DRY_RUN_CTP
        assert proto.message_type == "ORDER_INSERT"
        assert "broker_id_placeholder" in proto.raw_fields
    def test_fix(self):
        proto = ProtocolMapper().map_fix(_cmd(adapter_type="DRY_RUN_FIX"))
        assert proto.adapter_type == AdapterType.DRY_RUN_FIX
        assert proto.message_type == "NewOrderSingle"
        assert "ClOrdID" in proto.raw_fields

class TestNormalizer:
    def test_ack(self):
        cmd = _cmd(); proto = ProtocolMapper().map_ctp(cmd)
        nr = Normalizer().ack(cmd, proto)
        assert nr.status == "ACKED"; assert nr.dry_run is True; assert nr.live_trading_allowed is False
    def test_fill(self):
        cmd = _cmd(); proto = ProtocolMapper().map_ctp(cmd)
        nr = Normalizer().fill(cmd, proto)
        assert nr.status == "FILLED"; assert nr.fill_quantity == 10000
    def test_reject(self):
        nr = Normalizer().reject(_cmd(), "TEST_REJECT")
        assert nr.status == "REJECTED"; assert nr.rejection_reason == "TEST_REJECT"

class TestEngine:
    def test_valid_process(self):
        run = DryRunAdapterEngine().process(_cmd())
        assert run.order_state == AdapterOrderState.SYNTHETIC_ACKED, f"Got {run.order_state}: {run.rejection_reasons}"
        assert run.dry_run is True; assert run.live_trading_allowed is False
        assert run.protocol_message is not None; assert run.normalized_report is not None
    def test_simulate_fill(self):
        e = DryRunAdapterEngine(); run = e.process(_cmd())
        nr = e.simulate_fill(run.run_id)
        assert nr is not None; assert nr.status == "FILLED"
    def test_kill_switch(self):
        e = DryRunAdapterEngine(); run = e.process(_cmd())
        e.trigger_kill_switch(run.run_id)
        assert run.order_state == AdapterOrderState.FAILED
    def test_audit_created(self):
        e = DryRunAdapterEngine(); run = e.process(_cmd())
        trail = e.get_audit(run.run_id); assert len(trail) > 0
        for a in trail: assert a.dry_run is True
    def test_reproducibility(self):
        r1 = DryRunAdapterEngine().process(_cmd())
        r2 = DryRunAdapterEngine().process(_cmd())
        assert r1.checksum == r2.checksum

class TestNoExecution:
    def test_no_forbidden_imports(self):
        pdir = os.path.join(os.path.dirname(__file__), '..')
        FB = ["ctp","fix","quickfix","broker","socket","exchange_gateway","live_trading","real_order","ctp_sdk"]
        viols = []
        for rt,ds,fs in os.walk(pdir):
            for f in fs:
                if not f.endswith('.py'): continue
                try:
                    with open(os.path.join(rt,f), encoding="utf-8") as fh:
                        content = fh.read()
                    for node in ast.walk(ast.parse(content)):
                        mod = None
                        if isinstance(node,ast.Import):
                            for a in node.names: mod = a.name
                        elif isinstance(node,ast.ImportFrom): mod = node.module or ""
                        if mod:
                            for fb in FB:
                                if fb in mod.lower(): viols.append(f"{f}:{mod}")
                except: pass
        assert len(viols) == 0, f"Forbidden imports: {viols}"

    def test_no_real_credentials(self):
        pdir = os.path.join(os.path.dirname(__file__), '..')
        FB = ["password","auth_code","investor_password","broker_password","front_address","real_broker","real_investor"]
        viols = []
        for rt,ds,fs in os.walk(pdir):
            for f in fs:
                if not f.endswith('.py'): continue
                with open(os.path.join(rt,f), encoding="utf-8") as fh:
                    c = fh.read()
                for fb in FB:
                    if fb in c and "_placeholder" not in c.split(fb)[0][-20:]:
                        viols.append(f"{f}: '{fb}' (non-placeholder)")
        assert len(viols) == 0, f"Real credentials: {viols}"

class TestDryRunFlags:
    def test_flags_enforced(self):
        run = DryRunAdapterEngine().process(_cmd())
        assert run.dry_run is True; assert run.live_trading_allowed is False
        assert run.normalized_report.dry_run is True
        assert run.normalized_report.live_trading_allowed is False
