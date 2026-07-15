"""Test fixtures for promotion and lifecycle tests."""
from __future__ import annotations

import os
from typing import Any


class MockAlphaCatalog:
    """Mock catalog for promotion tests."""

    def __init__(self):
        self._alphas: dict[str, dict[str, Any]] = {}

    def get(self, alpha_id: str) -> dict[str, Any] | None:
        return self._alphas.get(alpha_id)

    def _set_state(self, alpha_id: str, state: Any) -> None:
        if alpha_id in self._alphas:
            self._alphas[alpha_id]["state"] = state


class MockExperimentRegistry:
    """Mock experiment registry for promotion tests."""

    def __init__(self, db_path: str, base_dir: str = ""):
        self._db_path = db_path
        self._base_dir = base_dir
        self._evaluations: dict[str, dict[str, Any]] = {}

    def load_into(self, catalog: MockAlphaCatalog) -> None:
        pass

    def get_evaluation(self, alpha_id: str) -> dict[str, Any] | None:
        return self._evaluations.get(alpha_id)

    def verify_evaluation(self, run_id: str) -> str:
        return "mock_content_hash"


class MockPaperStore:
    """Mock paper store for promotion tests."""

    def get_alpha_recommendation(self, alpha_id: str) -> dict[str, Any]:
        return {
            "alpha_id": alpha_id,
            "live_ic": 0.5,
            "trade_count": 100,
        }
