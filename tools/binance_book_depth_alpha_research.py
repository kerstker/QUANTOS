"""Run the fixed Binance book-depth Alpha retrospective diagnostic.

The driver verifies that the immutable pre-registration commit predates the
running source, reads only the governed v2 authority, and writes one diagnostic
report.  It never mutates the candidate catalog, lifecycle, Shadow, Testnet,
broker, credential or order state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
from collections.abc import Sequence

from services.data.binance_book_depth_archive import DATASET, DAY_MS
from services.data.binance_systemic_store import BinanceSystemicStore
from services.research.book_depth_alpha import evaluate_registered_candidates
from tools.cache_io import atomic_write_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "config" / "binance_book_depth_alpha_preregistration.json"
DEFAULT_STORE = Path("data") / "cache" / "binance_book_depth_archive.db"
DEFAULT_OUTPUT = ROOT / "evidence" / "binance_book_depth_alpha_research.json"
PREREGISTRATION_COMMIT = "08e63b5e4b6bc4e682dfb28f32d4931f45f94de6"


class BookDepthResearchError(RuntimeError):
    """The diagnostic cannot prove its pre-registration or data contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _verify_preregistration(contract_path: Path) -> tuple[str, str, dict]:
    raw = contract_path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BookDepthResearchError("pre-registration contract is invalid") from exc
    if not isinstance(payload, dict):
        raise BookDepthResearchError("pre-registration contract root is invalid")
    try:
        registered = subprocess.run(
            ["git", "show", f"{PREREGISTRATION_COMMIT}:config/{contract_path.name}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", PREREGISTRATION_COMMIT, "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BookDepthResearchError(
            "pre-registration commit is not an ancestor of the running source"
        ) from exc
    if registered != raw:
        raise BookDepthResearchError(
            "pre-registration contract differs from its immutable commit"
        )
    return PREREGISTRATION_COMMIT, _sha256(raw), payload


def run_research(
    *,
    store_path: str | Path,
    contract_path: str | Path = DEFAULT_CONTRACT,
    output_path: str | Path = DEFAULT_OUTPUT,
    generated_at_ms: int | None = None,
) -> dict:
    now_ms = int(time.time() * 1000) if generated_at_ms is None else generated_at_ms
    registration_commit, contract_sha256, contract = _verify_preregistration(
        Path(contract_path)
    )
    universe = tuple(str(value) for value in contract["universe"])
    history_days = int(contract["history_days"])
    store = BinanceSystemicStore(str(store_path))
    try:
        latest_by_instrument = {
            instrument: store.latest_event_time(DATASET, instrument)
            for instrument in universe
        }
        if any(value is None for value in latest_by_instrument.values()):
            raise BookDepthResearchError("authority lacks the fixed universe")
        common_end = min(int(value) for value in latest_by_instrument.values() if value is not None)
        start = max(0, common_end - history_days * DAY_MS)
        observations = store.range_observations(
            DATASET,
            start_event_time_ms=start + 1,
            end_event_time_ms=common_end + 1,
            instruments=universe,
        )
        if not store.chain_valid():
            raise BookDepthResearchError("book-depth authority chain is invalid")
    finally:
        store.close()
    observed_days = len(
        {
            (int(row["event_time_ms"]) - 1) // DAY_MS
            for row in observations
        }
    )
    if observed_days < int(contract["minimum_common_days"]):
        result = {
            "schema_version": 1,
            "generated_at_ms": now_ms,
            "status": "INSUFFICIENT_HISTORY",
            "mode": "RETROSPECTIVE_PRE_REGISTERED_DIAGNOSTIC",
            "dataset": DATASET,
            "observed_common_days": observed_days,
            "required_common_days": int(contract["minimum_common_days"]),
            "preregistration_commit": registration_commit,
            "preregistration_sha256": contract_sha256,
            "source_commit": _git("rev-parse", "HEAD"),
            "formal_admission": False,
            "forward_credit": False,
            "testnet_authorized": False,
            "order_submission_authorized": False,
            "safety": _safety(),
        }
    else:
        diagnostic = evaluate_registered_candidates(observations, contract)
        result = {
            "schema_version": 1,
            "generated_at_ms": now_ms,
            **diagnostic,
            "dataset": DATASET,
            "observed_common_days": observed_days,
            "preregistration_commit": registration_commit,
            "preregistration_sha256": contract_sha256,
            "source_commit": _git("rev-parse", "HEAD"),
            "safety": _safety(),
        }
    atomic_write_json(str(output_path), result, indent=2)
    return result


def _safety() -> dict[str, bool]:
    return {
        "paper_only": True,
        "credentials_accessed": False,
        "broker_connectivity": False,
        "order_submission": False,
        "live_trading": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the pre-registered book-depth curve diagnostic"
    )
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    result = run_research(
        store_path=args.store,
        contract_path=args.contract,
        output_path=args.output,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BookDepthResearchError",
    "DEFAULT_CONTRACT",
    "DEFAULT_OUTPUT",
    "DEFAULT_STORE",
    "PREREGISTRATION_COMMIT",
    "main",
    "run_research",
]
