"""Dry-Run Adapters 鈥?Engine (Phase 2G). Dry-run only. No CTP/FIX SDK."""
import time, hashlib, json, random, logging
from typing import Dict, List, Optional, Any
from .models import *

logger = logging.getLogger(__name__)

class DryRunValidator:
    def validate(self, cmd: Optional[DryRunCommand], existing_ids: List[str] = None) -> List[str]:
        reasons = []
        if not cmd: reasons.append(RejectionReason.COMMAND_MISSING.value); return reasons
        if cmd.live_trading_allowed: reasons.append(RejectionReason.LIVE_NOT_ALLOWED.value)
        if not cmd.dry_run: reasons.append(RejectionReason.DRY_RUN_MISSING.value)
        if not cmd.risk_approval_ref: reasons.append(RejectionReason.RISK_APPROVAL_MISSING.value)
        if not cmd.pre_trade_risk_ref: reasons.append(RejectionReason.PRE_TRADE_MISSING.value)
        if cmd.oms_state_ref not in ("", "VALIDATED","ACCEPTED_BY_SIM_OMS","SCHEDULED","SIMULATING","SIMULATED"):
            reasons.append(RejectionReason.OMS_INVALID.value)
        if cmd.kill_switch_ref == "ACTIVE": reasons.append(RejectionReason.KILL_SWITCH.value)
        if cmd.adapter_type not in ("DRY_RUN_CTP","DRY_RUN_FIX","DRY_RUN_GENERIC"):
            reasons.append(RejectionReason.UNSUPPORTED_TYPE.value)
        if existing_ids and cmd.command_id in existing_ids: reasons.append(RejectionReason.DUPLICATE.value)
        return reasons


class ProtocolMapper:
    def map_ctp(self, cmd: DryRunCommand) -> DryRunProtocolMessage:
        fields = {"broker_id_placeholder":"BROKER_9999","investor_id_placeholder":"INVESTOR_001",
            "instrument_id":cmd.instrument_id,"order_ref":f"EXPY-{int(time.time())}",
            "request_id":1,"direction":"BUY" if cmd.side=="BUY" else "SELL",
            "limit_price":str(cmd.price or 0),"volume_total_original":cmd.quantity,
            "order_type":cmd.order_type}
        return DryRunProtocolMessage(adapter_type=AdapterType.DRY_RUN_CTP, message_type="ORDER_INSERT",
            raw_fields=fields, protocol_hash=hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()[:16])

    def map_fix(self, cmd: DryRunCommand) -> DryRunProtocolMessage:
        fields = {"BeginString":"FIX.4.4","MsgType":"D","ClOrdID":f"EXPY-{int(time.time())}",
            "Symbol":cmd.instrument_id,"Side":"1" if cmd.side=="BUY" else "2",
            "OrdType":"2","OrderQty":str(cmd.quantity),"Price":str(cmd.price or 0),
            "TimeInForce":"0","TransactTime":str(int(time.time()))}
        return DryRunProtocolMessage(adapter_type=AdapterType.DRY_RUN_FIX, message_type="NewOrderSingle",
            raw_fields=fields, protocol_hash=hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()[:16])

    def map(self, cmd: DryRunCommand) -> Optional[DryRunProtocolMessage]:
        if cmd.adapter_type == "DRY_RUN_CTP": return self.map_ctp(cmd)
        if cmd.adapter_type == "DRY_RUN_FIX": return self.map_fix(cmd)
        if cmd.adapter_type == "DRY_RUN_GENERIC":
            fields = {"command_id": cmd.command_id, "instrument_id": cmd.instrument_id, "dry_run": True}
            return DryRunProtocolMessage(adapter_type=AdapterType.DRY_RUN_GENERIC, message_type="GENERIC_DRY_RUN",
                raw_fields=fields, protocol_hash=hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16])
        return None


class Normalizer:
    def ack(self, cmd: DryRunCommand, proto: DryRunProtocolMessage) -> NormalizedReport:
        return NormalizedReport(adapter_type=cmd.adapter_type, report_type="ORDER_ACK", command_id=cmd.command_id,
            child_order_id=cmd.child_order_id, external_order_id=f"SYNTH-{int(time.time())}",
            status="ACKED", checksum=hashlib.sha256(f"ack:{cmd.command_id}".encode()).hexdigest()[:12])

    def fill(self, cmd: DryRunCommand, proto: DryRunProtocolMessage) -> NormalizedReport:
        return NormalizedReport(adapter_type=cmd.adapter_type, report_type="TRADE_FILL", command_id=cmd.command_id,
            child_order_id=cmd.child_order_id, status="FILLED",
            fill_price=cmd.price or 10.0, fill_quantity=cmd.quantity,
            checksum=hashlib.sha256(f"fill:{cmd.command_id}".encode()).hexdigest()[:12])

    def reject(self, cmd: DryRunCommand, reason: str) -> NormalizedReport:
        return NormalizedReport(adapter_type=cmd.adapter_type, report_type="ORDER_REJECT", command_id=cmd.command_id,
            child_order_id=cmd.child_order_id, status="REJECTED", rejection_reason=reason,
            checksum=hashlib.sha256(f"rej:{cmd.command_id}:{reason}".encode()).hexdigest()[:12])


class DryRunAdapterEngine:
    def __init__(self):
        self.validator = DryRunValidator(); self.mapper = ProtocolMapper()
        self.normalizer = Normalizer(); self._runs: Dict[str, DryRunAdapterRun] = {}
        self._audit_records: List[DryRunAuditRecord] = []
        self._seq_ctp: int = 0; self._seq_fix: int = 0

    def process(self, cmd: DryRunCommand, existing_ids: List[str] = None) -> DryRunAdapterRun:
        reasons = self.validator.validate(cmd, existing_ids)
        run = DryRunAdapterRun(command_id=cmd.command_id, adapter_type=AdapterType(cmd.adapter_type) if cmd else AdapterType.DRY_RUN_GENERIC,
            command=cmd, rejection_reasons=reasons, dry_run=True)

        if reasons:
            run.order_state = AdapterOrderState.SYNTHETIC_REJECTED
            if cmd: run.normalized_report = self.normalizer.reject(cmd, reasons[0])
            for r in reasons: self._record_audit(DryRunAuditRecord(run_id=run.run_id, action="REJECT", reason=r))
        else:
            proto = self.mapper.map(cmd)
            if proto:
                run.protocol_message = proto; run.order_state = AdapterOrderState.MAPPED
                self._record_audit(DryRunAuditRecord(run_id=run.run_id, action="MAP", reason="Protocol mapping successful"))
                if cmd.adapter_type == "DRY_RUN_CTP": self._seq_ctp += 1
                else: self._seq_fix += 1

                run.normalized_report = self.normalizer.ack(cmd, proto)
                run.order_state = AdapterOrderState.SYNTHETIC_ACKED
                self._record_audit(DryRunAuditRecord(run_id=run.run_id, action="SYNTHETIC_ACK", reason="Generated"))
            else:
                run.order_state = AdapterOrderState.FAILED
                run.rejection_reasons.append(RejectionReason.INVALID_MAPPING.value)

        run.checksum = hashlib.sha256(f"dr:{run.command_id}:{run.adapter_type.value}:{run.order_state.value}:{run.rejection_reasons}".encode()).hexdigest()[:16]
        run.reproducibility_hash = run.checksum
        self._runs[run.run_id] = run
        return run

    def simulate_fill(self, rid: str) -> Optional[NormalizedReport]:
        run = self._runs.get(rid)
        if not run or not run.command: return None
        nr = self.normalizer.fill(run.command, run.protocol_message)
        run.normalized_report = nr; run.order_state = AdapterOrderState.SYNTHETIC_FILLED
        self._record_audit(DryRunAuditRecord(run_id=rid, action="SYNTHETIC_FILL", reason="Generated"))
        return nr

    def simulate_logon(self, rid: str):
        run = self._runs.get(rid)
        if run: run.session_state = AdapterSessionState.LOGON_SIMULATED
        self._record_audit(DryRunAuditRecord(run_id=rid, action="SESSION_LOGON", reason="Simulated"))

    def simulate_logout(self, rid: str):
        run = self._runs.get(rid)
        if run: run.session_state = AdapterSessionState.LOGOUT_SIMULATED
        self._record_audit(DryRunAuditRecord(run_id=rid, action="SESSION_LOGOUT", reason="Simulated"))

    def simulate_recovery(self, rid: str):
        run = self._runs.get(rid)
        if run: run.order_state = AdapterOrderState.COMMAND_RECEIVED
        self._record_audit(DryRunAuditRecord(run_id=rid, action="RECOVERY_SIMULATED", reason="Dry-run"))

    def trigger_kill_switch(self, rid: str):
        run = self._runs.get(rid)
        if run: run.order_state = AdapterOrderState.FAILED
        self._record_audit(DryRunAuditRecord(run_id=rid, action="KILL_SWITCH_TRIGGERED", reason="Dry-run"))

    def _record_audit(self, a: DryRunAuditRecord): self._audit_records.append(a)
    def get(self, rid: str) -> Optional[DryRunAdapterRun]: return self._runs.get(rid)
    def get_audit(self, rid: str) -> List[DryRunAuditRecord]:
        return [a for a in self._audit_records if a.run_id == rid]
    def get_report(self, rid: str) -> Optional[Dict]:
        r = self.get(rid)
        if not r: return None
        return {"run_id":r.run_id,"adapter_type":r.adapter_type.value,"order_state":r.order_state.value,
                "dry_run":r.dry_run,"live_trading_allowed":r.live_trading_allowed,
                "checksum":r.checksum,"rejection_reasons":r.rejection_reasons}
