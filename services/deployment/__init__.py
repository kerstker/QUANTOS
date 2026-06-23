"""QUANTOS Deployment Governance (blueprint §14 / Phase 14).

The deployment chokepoint: research environments (DEV/TEST) are free; PAPER needs
a verified human approval; STAGING/LIVE additionally require a readiness level and
safety flags that are OFF at L2_DRY_RUN_ONLY — so live deployment is structurally
impossible without a deliberate, multi-step, human-approved escalation. Fail-closed.
"""
from .gate import (
    DeploymentDecision, ReadinessLevel, evaluate_deployment,
)

__all__ = ["DeploymentDecision", "ReadinessLevel", "evaluate_deployment"]
