"""Dry-Run CTP/FIX Adapters 鈥?Domain Models (Phase 2G). Dry-run only. No CTP/FIX SDK."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from uuid import uuid4
import time, hashlib, json


class AdapterType(str, Enum): DRY_RUN_CTP="DRY_RUN_CTP"; DRY_RUN_FIX="DRY_RUN_FIX"; DRY_RUN_GENERIC="DRY_RUN_GENERIC"

class DryRunDecision(str, Enum):
    DRY_RUN_ACCEPTED="DRY_RUN_ACCEPTED"; DRY_RUN_REJECTED="DRY_RUN_REJECTED"
    DRY_RUN_ACK_GENERATED="DRY_RUN_ACK_GENERATED"; DRY_RUN_REJECT_GENERATED="DRY_RUN_REJECT_GENERATED"
    DRY_RUN_FILL_GENERATED="DRY_RUN_FILL_GENERATED"; DRY_RUN_CANCEL_ACK_GENERATED="DRY_RUN_CANCEL_ACK_GENERATED"
    DRY_RUN_CANCEL_REJECT_GENERATED="DRY_RUN_CANCEL_REJECT_GENERATED"
    DRY_RUN_SESSION_EVENT_GENERATED="DRY_RUN_SESSION_EVENT_GENERATED"
    DRY_RUN_NEEDS_MANUAL_REVIEW="DRY_RUN_NEEDS_MANUAL_REVIEW"; DRY_RUN_FAILED="DRY_RUN_FAILED"

class AdapterConnState(str, Enum):
    DISCONNECTED="DISCONNECTED"; CONNECTING_DRY_RUN="CONNECTING_DRY_RUN"
    CONNECTED_DRY_RUN="CONNECTED_DRY_RUN"; DISCONNECTING="DISCONNECTING"; FAILED="FAILED"

class AdapterSessionState(str, Enum):
    NO_SESSION="NO_SESSION"; LOGON_SIMULATED="LOGON_SIMULATED"; ACTIVE_DRY_RUN="ACTIVE_DRY_RUN"
    LOGOUT_SIMULATED="LOGOUT_SIMULATED"; SESSION_FAILED="SESSION_FAILED"

class AdapterOrderState(str, Enum):
    COMMAND_RECEIVED="COMMAND_RECEIVED"; MAPPED="MAPPED"; SYNTHETIC_SUBMITTED="SYNTHETIC_SUBMITTED"
    SYNTHETIC_ACKED="SYNTHETIC_ACKED"; SYNTHETIC_REJECTED="SYNTHETIC_REJECTED"
    SYNTHETIC_FILLED="SYNTHETIC_FILLED"; SYNTHETIC_CANCEL_REQUESTED="SYNTHETIC_CANCEL_REQUESTED"
    SYNTHETIC_CANCELLED="SYNTHETIC_CANCELLED"; SYNTHETIC_CANCEL_REJECTED="SYNTHETIC_CANCEL_REJECTED"
    STATUS_UNKNOWN="STATUS_UNKNOWN"; FAILED="FAILED"

class RejectionReason(str, Enum):
    LIVE_NOT_ALLOWED="REJECTED_LIVE_TRADING_NOT_ALLOWED"; DRY_RUN_MISSING="REJECTED_DRY_RUN_FLAG_MISSING"
    COMMAND_MISSING="REJECTED_COMMAND_MISSING"; CHECKSUM_MISMATCH="REJECTED_COMMAND_CHECKSUM_MISMATCH"
    RISK_APPROVAL_MISSING="REJECTED_RISK_APPROVAL_MISSING"; PRE_TRADE_MISSING="REJECTED_PRE_TRADE_RISK_MISSING"
    OMS_INVALID="REJECTED_OMS_STATE_INVALID"; EMS_INVALID="REJECTED_EMS_STATE_INVALID"
    KILL_SWITCH="REJECTED_KILL_SWITCH_ACTIVE"; STRATEGY_SUSPENDED="REJECTED_STRATEGY_SUSPENDED"
    FORBIDDEN_INST="REJECTED_INSTRUMENT_FORBIDDEN"; DUPLICATE="REJECTED_DUPLICATE_COMMAND"
    STALE="REJECTED_STALE_COMMAND"; AUDIT_LINEAGE="REJECTED_AUDIT_LINEAGE_MISSING"
    UNSUPPORTED_TYPE="REJECTED_UNSUPPORTED_ADAPTER_TYPE"; INVALID_MAPPING="REJECTED_INVALID_PROTOCOL_MAPPING"
    SEQUENCE_GAP="REJECTED_SEQUENCE_GAP"; IDEMPOTENCY="REJECTED_IDEMPOTENCY_CONFLICT"
    AUDIT_FAILURE="REJECTED_AUDIT_FAILURE"; EVENT_FAILURE="REJECTED_EVENT_PUBLISH_FAILURE"
    UNKNOWN="REJECTED_UNKNOWN_FAIL_CLOSED"


@dataclass
class DryRunAdapterConfig:
    adapter_type: AdapterType = AdapterType.DRY_RUN_CTP; seed: int = 42
    dry_run: bool = True; live_trading_allowed: bool = False
    synthetic_latency_ms: float = 5.0

@dataclass
class DryRunCommand:
    command_id: str = ""; adapter_type: str = "DRY_RUN_CTP"; ems_run_id: str = ""
    parent_order_id: str = ""; child_order_id: str = ""; instrument_id: str = ""
    side: str = "BUY"; order_type: str = "LIMIT"; quantity: int = 0
    price: Optional[float] = None; risk_approval_ref: str = ""; pre_trade_risk_ref: str = ""
    oms_state_ref: str = ""; ems_state_ref: str = ""; kill_switch_ref: str = ""
    idempotency_key: str = ""; checksum: str = ""; dry_run: bool = True
    live_trading_allowed: bool = False

@dataclass
class DryRunProtocolMessage:
    message_id: str = field(default_factory=lambda: str(uuid4()))
    adapter_type: AdapterType = AdapterType.DRY_RUN_CTP
    message_type: str = ""; raw_fields: Dict = field(default_factory=dict)
    protocol_hash: str = ""; dry_run: bool = True

@dataclass
class NormalizedReport:
    report_id: str = field(default_factory=lambda: str(uuid4()))
    adapter_type: str = ""; report_type: str = ""; command_id: str = ""
    child_order_id: str = ""; external_order_id: str = "SYNTHETIC-001"
    status: str = "ACKED"; rejection_reason: str = ""
    fill_price: float = 0.0; fill_quantity: int = 0
    checksum: str = ""; dry_run: bool = True; live_trading_allowed: bool = False
    event_time: float = field(default_factory=time.time)


@dataclass
class DryRunAdapterRun:
    run_id: str = field(default_factory=lambda: str(uuid4()))
    command_id: str = ""; adapter_type: AdapterType = AdapterType.DRY_RUN_CTP
    command: Optional[DryRunCommand] = None
    protocol_message: Optional[DryRunProtocolMessage] = None
    normalized_report: Optional[NormalizedReport] = None
    conn_state: AdapterConnState = AdapterConnState.DISCONNECTED
    session_state: AdapterSessionState = AdapterSessionState.NO_SESSION
    order_state: AdapterOrderState = AdapterOrderState.COMMAND_RECEIVED
    rejection_reasons: List[str] = field(default_factory=list)
    checksum: str = ""; reproducibility_hash: str = ""
    dry_run: bool = True; live_trading_allowed: bool = False
    created_at: float = field(default_factory=time.time)


@dataclass
class DryRunAuditRecord:
    audit_id: str = field(default_factory=lambda: str(uuid4())); run_id: str = ""
    action: str = ""; reason: str = ""; timestamp: float = field(default_factory=time.time)
    dry_run: bool = True; live_trading_allowed: bool = False
