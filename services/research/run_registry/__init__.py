"""run_registry — durable source of truth for research/backtest runs (research-ops console)."""
from .contract import ALLOWED_KINDS, VALID_STATUS, ResearchRun, make_run_id
from .registry import RunRegistry
from .store import ResearchRunStore

__all__ = ["ResearchRun", "make_run_id", "VALID_STATUS", "ALLOWED_KINDS",
           "ResearchRunStore", "RunRegistry"]
