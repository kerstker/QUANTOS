#!/usr/bin/env python
"""Generate JSON Schemas from the canonical pydantic contracts.

The contracts in quantos.contracts are the source of truth; the files under
schemas/ are generated from them (deterministically, sorted) so they never
drift. Run `python scripts/gen_schemas.py` to refresh; the drift guard in
tests/contracts/test_schema_drift.py fails if a committed schema is stale.
"""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)  # make `quantos` importable when run as a script

from quantos.contracts.events import Event
from quantos.contracts.risk import RiskDecision
from quantos.contracts.trading import OrderIntent

SCHEMAS_DIR = os.path.join(REPO, "schemas")

# relative path under schemas/ -> pydantic model
TARGETS = {
    "events/event.schema.json": Event,
    "risk/risk_decision.schema.json": RiskDecision,
    "trading/order_intent.schema.json": OrderIntent,
}


def render(model) -> str:
    return json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"


def generate(write: bool = True) -> dict[str, str]:
    out = {}
    for rel, model in TARGETS.items():
        text = render(model)
        out[rel] = text
        if write:
            path = os.path.join(SCHEMAS_DIR, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
    return out


if __name__ == "__main__":
    generate(write=True)
    print(f"generated {len(TARGETS)} schemas under {SCHEMAS_DIR}")
