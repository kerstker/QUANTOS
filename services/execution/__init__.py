"""QUANTOS Execution Boundary (blueprint §07 / Phase 10) — DRY-RUN ONLY.

Turns an APPROVED target book into OrderIntents (a strategy/translation concern)
and admits them to the (simulated) execution path ONLY after independently
re-verifying the pre-trade risk approval ref, with idempotency. Carries NO broker
SDK and NO order submission — live execution remains structurally impossible.
"""
from .intent_builder import build_intents
from .boundary import AdmitDecision, ExecutionBoundary

__all__ = ["build_intents", "AdmitDecision", "ExecutionBoundary"]
