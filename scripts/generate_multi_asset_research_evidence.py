"""Generate or verify the retained clean-checkout multi-asset research baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from quantos.contracts.multi_asset import RETAINED_FX_FACTOR_SPECS  # noqa: E402
from services.governance.multi_asset_research_evidence import (  # noqa: E402
    DEFAULT_BUNDLE_REF,
    DEFAULT_EVIDENCE_REF,
    DEFAULT_MANIFEST_REF,
    MULTI_ASSET_IMPLEMENTATION_PATHS,
    MULTI_ASSET_VERIFICATION_PATHS,
    assemble_multi_asset_research_evidence,
)
from tools.fx_pit_bundle import load_fx_pit_panel  # noqa: E402
from tools.ic_panel_registry import FACTOR_REGISTRY  # noqa: E402
from tools.ic_panel_research import factor_row  # noqa: E402


def _registered_fx_entries() -> list[dict]:
    by_name = {entry["name"]: entry for entry in FACTOR_REGISTRY if entry.get("universe") == "fx_tradable"}
    expected = [spec.factor_id for spec in RETAINED_FX_FACTOR_SPECS]
    if sorted(by_name) != sorted(expected):
        raise ValueError("registered FX factors do not match the retained factor contract")
    ordered = []
    for spec in RETAINED_FX_FACTOR_SPECS:
        entry = by_name[spec.factor_id]
        if entry.get("holding_periods") != list(spec.holding_periods):
            raise ValueError(f"holding-period drift for {spec.factor_id}")
        if entry.get("thesis") != spec.thesis:
            raise ValueError(f"thesis drift for {spec.factor_id}")
        ordered.append(entry)
    return ordered


def build_multi_asset_research_evidence(
    repo_root: str | Path = REPO,
    *,
    bundle_path: str | Path = DEFAULT_BUNDLE_REF,
    manifest_path: str | Path = DEFAULT_MANIFEST_REF,
) -> dict:
    """Run the production IC primitives over the retained non-crypto bundle."""

    root = Path(repo_root).resolve()
    panel, manifest = load_fx_pit_panel(
        bundle_path,
        manifest_path,
        repo_root=root,
    )
    factor_results = [factor_row(entry, panel) for entry in _registered_fx_entries()]
    return assemble_multi_asset_research_evidence(
        repo_root=root,
        bundle_ref=bundle_path,
        manifest_ref=manifest_path,
        manifest=manifest,
        event_time_count=len(panel.event_times),
        factor_results=factor_results,
        implementation_paths=MULTI_ASSET_IMPLEMENTATION_PATHS,
        verification_paths=MULTI_ASSET_VERIFICATION_PATHS,
    )


def _serialized(document: dict) -> str:
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--bundle", default=DEFAULT_BUNDLE_REF)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST_REF)
    parser.add_argument("--output", default=DEFAULT_EVIDENCE_REF)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    document = build_multi_asset_research_evidence(
        root,
        bundle_path=args.bundle,
        manifest_path=args.manifest,
    )
    rendered = _serialized(document)
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            print("multi-asset-research-evidence: stale or missing")
            return 1
        print("multi-asset-research-evidence: current")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"multi-asset-research-evidence: wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
