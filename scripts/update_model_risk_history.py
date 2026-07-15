#!/usr/bin/env python
"""Append or verify the offline model-risk revision history."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.governance.model_risk_history import (
    ModelRiskHistoryError,
    append_model_risk_revision,
    history_matches_evidence,
    validate_model_risk_history,
)


DEFAULT_EVIDENCE = REPO / "evidence/model_risk_baseline.json"
DEFAULT_HISTORY = REPO / "evidence/model_risk_history.json"


def _read_object(path: Path, label: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ModelRiskHistoryError(f"{label} must be a JSON object")
    return document


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    evidence_path = Path(args.evidence)
    history_path = Path(args.history)
    try:
        evidence = _read_object(evidence_path, "model-risk evidence")
        if args.check:
            history = _read_object(history_path, "model-risk history")
            summary = validate_model_risk_history(history)
            if not history_matches_evidence(history, evidence):
                raise ModelRiskHistoryError(
                    "model-risk history does not end at the current evidence revision"
                )
            print(
                "model-risk history: current; "
                f"revisions={summary['revision_count']}; status={summary['status']}"
            )
            return 0

        existing = (
            _read_object(history_path, "model-risk history")
            if history_path.is_file()
            else None
        )
        history, appended = append_model_risk_revision(existing, evidence)
        if appended or not history_path.is_file():
            _write(history_path, history)
        summary = validate_model_risk_history(history)
        print(
            "model-risk history: "
            f"{'appended' if appended else 'current'}; "
            f"revisions={summary['revision_count']}; status={summary['status']}"
        )
        return 0
    except (OSError, json.JSONDecodeError, ModelRiskHistoryError, TypeError, ValueError) as exc:
        print(f"model-risk history failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
