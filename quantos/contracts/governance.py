"""Governance domain entities (blueprint §6.7).

Audit, approval, versioning, deployment, and incident records. Two safety
touches at the contract boundary:
  * AuditEvent supports a hash chain (prev_hash -> entry_hash) for tamper-evidence
    (CT-3 / G13).
  * A LIVE DeploymentRecord cannot be constructed unless explicitly approved
    (fail-closed; live remains impossible without sign-off).

DatasetVersion lives in `quantos.contracts.data` (§6.1) and is reused here.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .hashing import stable_hash


class Environment(str, Enum):
    DEV = "DEV"
    TEST = "TEST"
    PAPER = "PAPER"
    STAGING = "STAGING"
    LIVE = "LIVE"


class IncidentSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    target: str = ""
    timestamp: int = Field(ge=0)
    metadata: dict[str, str] = Field(default_factory=dict)
    prev_hash: str = ""

    @property
    def entry_hash(self) -> str:
        """Tamper-evident chain hash linking this entry to the previous one."""
        return stable_hash({
            "event_id": self.event_id, "actor": self.actor, "action": self.action,
            "target": self.target, "timestamp": self.timestamp,
            "metadata": self.metadata, "prev_hash": self.prev_hash,
        })


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    approval_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    approver: str = Field(min_length=1)   # human identity; agents are barred (enforced in Phase 11)
    approved: bool = False
    timestamp: int = Field(ge=0)
    note: str = ""


class ConfigVersion(BaseModel):
    model_config = ConfigDict(frozen=True)
    config_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = ""
    created_at: int = Field(default=0, ge=0)


class StrategyVersion(BaseModel):
    model_config = ConfigDict(frozen=True)
    strategy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = ""
    created_at: int = Field(default=0, ge=0)
    approved: bool = False


class ModelVersion(BaseModel):
    model_config = ConfigDict(frozen=True)
    model_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = ""
    created_at: int = Field(default=0, ge=0)


class DeploymentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    deployment_id: str = Field(min_length=1)
    environment: Environment
    version: str = Field(min_length=1)
    approved: bool = False
    timestamp: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _live_requires_approval(self) -> "DeploymentRecord":
        if self.environment == Environment.LIVE and not self.approved:
            raise ValueError("LIVE deployment requires explicit approval (blueprint §1, §14)")
        return self


class IncidentReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    incident_id: str = Field(min_length=1)
    severity: IncidentSeverity
    summary: str = ""
    created_at: int = Field(default=0, ge=0)
    resolved: bool = False
