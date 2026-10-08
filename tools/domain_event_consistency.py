"""Run a read-only domain-event authority/projection consistency check."""
from __future__ import annotations

import argparse
import json
import os

from services.monitoring.domain_event_runtime import (
    DOMAIN_EVENT_DB,
    DOMAIN_EVENT_PROJECTION_DB,
)
from services.monitoring.event_authority_consistency import (
    inspect_event_authority_consistency,
)
from tools.cache_io import atomic_write_json


DEFAULT_OUTPUT = os.path.join("data", "cache", "domain_event_consistency.json")


def run_domain_event_consistency(
    *,
    event_path: str = DOMAIN_EVENT_DB,
    projection_path: str = DOMAIN_EVENT_PROJECTION_DB,
    output_path: str = DEFAULT_OUTPUT,
) -> dict:
    document = inspect_event_authority_consistency(
        event_path=event_path,
        projection_path=projection_path,
    )
    atomic_write_json(output_path, document, indent=2)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only event authority/projection consistency check"
    )
    parser.add_argument("--event-path", default=DOMAIN_EVENT_DB)
    parser.add_argument("--projection-path", default=DOMAIN_EVENT_PROJECTION_DB)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    document = run_domain_event_consistency(
        event_path=args.event_path,
        projection_path=args.projection_path,
        output_path=args.output,
    )
    print(json.dumps(document, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")
    return 0 if document["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEFAULT_OUTPUT", "run_domain_event_consistency"]
