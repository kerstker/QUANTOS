"""Formal evaluation of one fixed three-day positioning-consensus family.

This is a new, explicitly counted temporal hypothesis, not a threshold change
or a replay of the seven-day candidate.  The complete three-member family is
registered before evaluation.  This L2 research driver has no credential,
broker, intent, promotion, or order path.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from services.research import alpha_expression as ax
from tools import positioning_composite_evaluation as common


DEFAULT_OUTPUT = Path("data/cache/positioning_fast_composite_evaluation.json")
FWD_HORIZON = 3
PERIODS_PER_YEAR = 122
DATA_SOURCE = "crypto_oi_pit_panel"
SOURCE = "tools.positioning_fast_composite_evaluation"
SEARCH_KIND = "PREREGISTERED_POSITIONING_FAST_COMPOSITE_V1"
EVALUATION_CONTRACT = {
    "driver": "positioning_fast_composite_evaluation.v1",
    "fwd_horizon": FWD_HORIZON,
    "non_overlapping": True,
    "periods_per_year": PERIODS_PER_YEAR,
    "selection_population": tuple(common.CANDIDATE_EXPRESSIONS),
    "candidate_expression": ax.serialize(
        common.POSITIONING_COMPOSITE_EXPRESSION
    ),
    "shared_calendar": True,
    "shared_calendar_rule": "first_common_then_minimum_3d_spacing",
    "shared_calendar_step_ms": FWD_HORIZON * 86_400_000,
    "parameter_scan": False,
}
FALSIFICATION_CRITERIA = (
    "The fixed three-day composite fails the immutable catalog-wide DSR threshold.",
    "The complete three-member CSCV population exceeds the PBO ceiling.",
    "The composite fails leakage, regime, or sequential walk-forward evidence.",
    "The composite does not improve robust evidence relative to both controls.",
)


def build_candidate_batch(panel):
    return common.build_candidate_batch(
        panel,
        fwd_horizon=FWD_HORIZON,
        evaluation_contract=EVALUATION_CONTRACT,
        id_prefix="positioning_fast3d",
        shared_calendar_step_ms=FWD_HORIZON * 86_400_000,
    )


def run(
    *,
    output: str | Path = DEFAULT_OUTPUT,
    recorded_at: int | None = None,
    source_commit: str,
    panel=None,
) -> dict[str, Any]:
    return common._run_positioning_spec(
        output=output,
        recorded_at=recorded_at,
        source_commit=source_commit,
        panel=panel,
        fwd_horizon=FWD_HORIZON,
        periods_per_year=PERIODS_PER_YEAR,
        evaluation_contract=EVALUATION_CONTRACT,
        source=SOURCE,
        search_kind=SEARCH_KIND,
        data_source=DATA_SOURCE,
        schema_version="positioning_fast_composite_evaluation.v1",
        id_prefix="positioning_fast3d",
        parent_hypothesis=(
            "Retail/top-trader positioning confirmation describes a short-lived "
            "cross-sectional unwind whose edge is harvested within three days."
        ),
        search_space=(
            "Exactly the two canonical controls and one fixed equal-standardized "
            "consensus expression at one pre-registered three-day horizon; no scan."
        ),
        falsification_criteria=FALSIFICATION_CRITERIA,
        shared_calendar_step_ms=FWD_HORIZON * 86_400_000,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args(argv)
    try:
        document = run(output=args.output, source_commit=args.source_commit)
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        print(
            "three-day positioning composite evaluation blocked: "
            f"{type(exc).__name__}: {exc}"
        )
        return 2
    candidate = document["family"]["positioning_consensus"]["evaluation"] or {}
    print(
        "three-day positioning composite evaluation: "
        f"status={document['status']} verdict={candidate.get('verdict')} "
        f"dsr={candidate.get('deflated_sharpe')} pbo={candidate.get('pbo')}"
    )
    return 0 if document["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATA_SOURCE",
    "DEFAULT_OUTPUT",
    "EVALUATION_CONTRACT",
    "FWD_HORIZON",
    "PERIODS_PER_YEAR",
    "build_candidate_batch",
    "run",
]
