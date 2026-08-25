"""Audit blind forward maturity for the frozen liquidation mechanism study.

This worker verifies the append-once preregistration and the public Bybit event
hash chain, then counts only closed, independent forced-liquidation episodes.
It never reads prices or sizes from liquidation rows and never constructs
labels, returns, evaluator evidence, lifecycle state, or trading authority.
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any

from quantos.contracts.hashing import stable_hash
from services.data.public_market_event_store import PublicMarketEventStore
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.liquidation_mechanism import (
    LiquidationPrint,
    group_closed_liquidation_bursts,
    select_independent_bursts,
)
from tools.cache_io import atomic_write_json


DEFAULT_PREREGISTRATION = os.path.join(
    "evidence", "cross_venue_liquidation_preregistration_764e5b5.json"
)
DEFAULT_STORE = os.path.join("data", "cache", "bybit_linear_events.db")
DEFAULT_CHECKPOINT = os.path.join(
    "data", "cache", "bybit_linear_events_chain.json"
)
DEFAULT_OUTPUT = os.path.join(
    "data", "cache", "cross_venue_liquidation_health.json"
)
MAX_CHECKPOINT_AGE_MS = 30_000
MAX_FUTURE_SKEW_MS = 30_000
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXPECTED_CANDIDATE_ROLES = frozenset(
    {"PRIMARY", "SIGN_CONTROL", "VENUE_CONTROL", "PAYER_CONTROL", "CLOCK_CONTROL"}
)


class LiquidationHealthError(RuntimeError):
    """Blind maturity evidence is ambiguous or cannot be verified."""


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LiquidationHealthError("document is not canonical JSON") from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _integer(value: object, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LiquidationHealthError(f"{name} is invalid")
    if value < (1 if positive else 0):
        raise LiquidationHealthError(f"{name} is invalid")
    return value


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LiquidationHealthError(f"{name} is invalid")
    return dict(value)


def _load_mapping(path: str | Path, name: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
        value = load_json_without_duplicate_keys(raw)
    except (OSError, TypeError, ValueError) as exc:
        raise LiquidationHealthError(f"{name} is unreadable") from exc
    return _mapping(value, name), raw


def _normalized_relative(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise LiquidationHealthError(f"{name} is invalid")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise LiquidationHealthError(f"{name} is invalid")
    return path.as_posix()


def verify_preregistration(
    document: Mapping[str, Any],
    *,
    configured_store_path: str,
) -> dict[str, Any]:
    """Verify the frozen artifact using only identities contained in the artifact."""

    item = dict(document)
    if (
        item.get("schema_version")
        != "cross_venue_liquidation_preregistration.v1"
        or item.get("purpose") != "APPEND_ONCE_FORWARD_PREREGISTRATION"
        or item.get("readiness") != "L2_DRY_RUN_ONLY"
    ):
        raise LiquidationHealthError("preregistration identity is invalid")
    source_commit = item.get("source_commit")
    contract_hash = item.get("contract_hash")
    preregistration_id = item.get("preregistration_id")
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise LiquidationHealthError("preregistration source_commit is invalid")
    if not isinstance(contract_hash, str) or _SHA256.fullmatch(contract_hash) is None:
        raise LiquidationHealthError("preregistration contract_hash is invalid")
    if (
        not isinstance(preregistration_id, str)
        or _SHA256.fullmatch(preregistration_id) is None
    ):
        raise LiquidationHealthError("preregistration_id is invalid")
    preregistered_at_ms = _integer(
        item.get("preregistered_at_ms"), "preregistered_at_ms"
    )
    earliest_unblind_at_ms = _integer(
        item.get("earliest_unblind_at_ms"), "earliest_unblind_at_ms"
    )
    contract = _mapping(item.get("evaluation_contract"), "evaluation_contract")
    if (
        contract.get("schema")
        != "cross_venue_liquidation_recovery_forward_contract.v1"
        or contract.get("purpose")
        != "MECHANISM_FORWARD_PREREGISTRATION_NOT_ALPHA_ADMISSION"
        or stable_hash(contract) != contract_hash
    ):
        raise LiquidationHealthError("preregistration contract binding is invalid")
    data = _mapping(contract.get("data"), "evaluation_contract.data")
    configured_relative = _normalized_relative(
        configured_store_path, "configured_store_path"
    )
    if (
        _normalized_relative(data.get("bybit_authority"), "bybit_authority")
        != configured_relative
    ):
        raise LiquidationHealthError("configured store differs from frozen authority")
    universe_raw = data.get("universe")
    if not isinstance(universe_raw, list) or not universe_raw:
        raise LiquidationHealthError("preregistered universe is invalid")
    universe = tuple(universe_raw)
    if (
        len(set(universe)) != len(universe)
        or any(not isinstance(value, str) for value in universe)
    ):
        raise LiquidationHealthError("preregistered universe is invalid")
    unblinding = _mapping(contract.get("unblinding"), "unblinding")
    thresholds = {
        name: _integer(unblinding.get(name), name, positive=True)
        for name in (
            "minimum_collection_ms",
            "minimum_eligible_event_days",
            "minimum_independent_episodes",
            "minimum_episodes_per_instrument",
        )
    }
    if earliest_unblind_at_ms != (
        preregistered_at_ms + thresholds["minimum_collection_ms"]
    ):
        raise LiquidationHealthError("earliest unblind time is inconsistent")
    episode = _mapping(contract.get("episode_definition"), "episode_definition")
    quiet_period_ms = _integer(
        episode.get("quiet_period_ms"), "quiet_period_ms", positive=True
    )
    lockout_ms = _integer(
        episode.get("instrument_lockout_ms"),
        "instrument_lockout_ms",
        positive=True,
    )
    if episode.get("future_event_access") is not False or episode.get(
        "outcome_in_episode_selection"
    ) is not False:
        raise LiquidationHealthError("episode selection is not outcome blind")
    candidates_raw = item.get("candidates")
    aliases_raw = contract.get("candidate_aliases")
    if (
        not isinstance(candidates_raw, list)
        or len(candidates_raw) != 5
        or not isinstance(aliases_raw, list)
        or len(aliases_raw) != 5
    ):
        raise LiquidationHealthError("candidate preregistration is invalid")
    candidates = [_mapping(value, "candidate") for value in candidates_raw]
    aliases = [candidate.get("alias") for candidate in candidates]
    definition_hashes = [candidate.get("definition_hash") for candidate in candidates]
    if (
        aliases != aliases_raw
        or len(set(aliases)) != 5
        or {candidate.get("role") for candidate in candidates}
        != _EXPECTED_CANDIDATE_ROLES
        or any(
            not isinstance(value, str) or _SHA256.fullmatch(value) is None
            for value in definition_hashes
        )
        or len(set(definition_hashes)) != 5
    ):
        raise LiquidationHealthError("candidate identities are invalid")
    for candidate in candidates:
        params = _mapping(candidate.get("params"), "candidate.params")
        if params.get("contract_hash") != contract_hash:
            raise LiquidationHealthError("candidate is not bound to the contract")
    identity = {
        "contract_hash": contract_hash,
        "source_commit": source_commit,
        "preregistered_at_ms": preregistered_at_ms,
        "earliest_unblind_at_ms": earliest_unblind_at_ms,
        "candidate_definition_hashes": definition_hashes,
    }
    if stable_hash(identity) != preregistration_id:
        raise LiquidationHealthError("preregistration identity hash is invalid")
    if (
        item.get("outcomes_accessed") is not False
        or item.get("return_statistics_computed") is not False
        or item.get("trial_registry_mutated") is not False
        or item.get("alpha_eligible_count") != 0
        or contract.get("outcome_access_before_unblind") is not False
        or contract.get("testnet_or_live_authority") is not False
    ):
        raise LiquidationHealthError("preregistration overclaims authority")
    safety = _mapping(item.get("safety"), "safety")
    if any(
        safety.get(name) is not False
        for name in (
            "credentials",
            "private_stream",
            "broker_connectivity",
            "order_submission",
            "testnet_or_live_authority",
        )
    ) or safety.get("human_review_required_for_l2_paper_admission") is not True:
        raise LiquidationHealthError("preregistration safety boundary is invalid")
    return {
        "contract_hash": contract_hash,
        "preregistration_id": preregistration_id,
        "source_commit": source_commit,
        "preregistered_at_ms": preregistered_at_ms,
        "earliest_unblind_at_ms": earliest_unblind_at_ms,
        "universe": universe,
        "thresholds": thresholds,
        "quiet_period_ms": quiet_period_ms,
        "lockout_ms": lockout_ms,
    }


def verify_checkpoint(
    document: Mapping[str, Any],
    *,
    store_path: str,
    now_ms: int,
    max_age_ms: int = MAX_CHECKPOINT_AGE_MS,
) -> dict[str, Any]:
    item = dict(document)
    body = {
        "schema_version": item.get("schema_version"),
        "store_path": item.get("store_path"),
        "full_verified_at_ms": item.get("full_verified_at_ms"),
        "verified_at_ms": item.get("verified_at_ms"),
        "verified_seq": item.get("verified_seq"),
        "verified_row_hash": item.get("verified_row_hash"),
    }
    if body["schema_version"] != 1 or item.get("checkpoint_hash") != _hash(body):
        raise LiquidationHealthError("chain checkpoint binding is invalid")
    resolved_store = str(Path(store_path).resolve())
    if body["store_path"] != resolved_store:
        raise LiquidationHealthError("chain checkpoint names a different store")
    full_verified_at_ms = _integer(
        body["full_verified_at_ms"], "full_verified_at_ms"
    )
    verified_at_ms = _integer(body["verified_at_ms"], "verified_at_ms")
    verified_seq = _integer(body["verified_seq"], "verified_seq")
    verified_row_hash = body["verified_row_hash"]
    if (
        full_verified_at_ms > verified_at_ms
        or verified_at_ms > now_ms + MAX_FUTURE_SKEW_MS
        or now_ms - verified_at_ms > max_age_ms
        or (
            verified_seq == 0
            and verified_row_hash != ""
        )
        or (
            verified_seq > 0
            and (
                not isinstance(verified_row_hash, str)
                or _SHA256.fullmatch(verified_row_hash) is None
            )
        )
    ):
        raise LiquidationHealthError("chain checkpoint is stale or invalid")
    return {
        **body,
        "full_verified_at_ms": full_verified_at_ms,
        "verified_at_ms": verified_at_ms,
        "verified_seq": verified_seq,
        "verified_row_hash": verified_row_hash,
        "checkpoint_hash": item["checkpoint_hash"],
        "age_ms": max(0, now_ms - verified_at_ms),
    }


def _prints_from_events(
    events: Sequence[Mapping[str, Any]],
    *,
    preregistered_at_ms: int,
) -> tuple[tuple[LiquidationPrint, ...], int]:
    prints: list[LiquidationPrint] = []
    excluded = 0
    for event in events:
        payload = _mapping(event.get("payload"), "liquidation payload")
        rows = payload.get("liquidations")
        if not isinstance(rows, list) or not rows:
            raise LiquidationHealthError("liquidation payload rows are invalid")
        event_id = event.get("event_id")
        instrument = event.get("instrument_id")
        received_at_ms = _integer(event.get("received_at_ms"), "received_at_ms")
        processed_at_ms = _integer(event.get("processed_at_ms"), "processed_at_ms")
        if not isinstance(event_id, str) or _SHA256.fullmatch(event_id) is None:
            raise LiquidationHealthError("liquidation event_id is invalid")
        if not isinstance(instrument, str):
            raise LiquidationHealthError("liquidation instrument is invalid")
        for index, raw_row in enumerate(rows):
            row = _mapping(raw_row, "liquidation row")
            event_time_ms = _integer(row.get("event_time_ms"), "event_time_ms")
            side = row.get("liquidated_position_side")
            if (
                event_time_ms <= preregistered_at_ms
                or received_at_ms <= preregistered_at_ms
                or processed_at_ms <= preregistered_at_ms
            ):
                excluded += 1
                continue
            try:
                prints.append(
                    LiquidationPrint(
                        instrument=instrument,
                        liquidated_position_side=side,
                        event_time_ms=event_time_ms,
                        received_at_ms=received_at_ms,
                        processed_at_ms=processed_at_ms,
                        print_id=f"{event_id}:{index}",
                    )
                )
            except (TypeError, ValueError) as exc:
                raise LiquidationHealthError("liquidation print is invalid") from exc
    return tuple(prints), excluded


def run_health(
    *,
    preregistration_path: str = DEFAULT_PREREGISTRATION,
    store_path: str = DEFAULT_STORE,
    checkpoint_path: str = DEFAULT_CHECKPOINT,
    output_path: str = DEFAULT_OUTPUT,
    now_ms: int | None = None,
    max_checkpoint_age_ms: int = MAX_CHECKPOINT_AGE_MS,
) -> dict[str, Any]:
    generated_at_ms = int(time.time() * 1000) if now_ms is None else now_ms
    _integer(generated_at_ms, "now_ms")
    _integer(max_checkpoint_age_ms, "max_checkpoint_age_ms", positive=True)
    preregistration, preregistration_raw = _load_mapping(
        preregistration_path, "preregistration"
    )
    frozen = verify_preregistration(
        preregistration,
        configured_store_path=store_path,
    )
    checkpoint_document, _checkpoint_raw = _load_mapping(
        checkpoint_path, "chain checkpoint"
    )
    checkpoint = verify_checkpoint(
        checkpoint_document,
        store_path=store_path,
        now_ms=generated_at_ms,
        max_age_ms=max_checkpoint_age_ms,
    )
    store = PublicMarketEventStore(store_path)
    try:
        chain = store.verify_chain_from(
            checkpoint_seq=checkpoint["verified_seq"],
            checkpoint_row_hash=checkpoint["verified_row_hash"],
        )
        if not chain.get("valid"):
            raise LiquidationHealthError("public event chain verification failed")
        events = store.events_by_channel_after(
            "all_liquidation",
            after_processed_at_ms=frozen["preregistered_at_ms"],
            instruments=frozen["universe"],
        )
    finally:
        store.close()
    prints, excluded = _prints_from_events(
        events,
        preregistered_at_ms=frozen["preregistered_at_ms"],
    )
    bursts = group_closed_liquidation_bursts(
        prints,
        observed_through_ms=generated_at_ms,
        max_gap_ms=5_000,
        quiet_period_ms=frozen["quiet_period_ms"],
    )
    independent = select_independent_bursts(
        bursts,
        lockout_ms=frozen["lockout_ms"],
    )
    per_instrument = Counter(row.instrument for row in independent)
    for instrument in frozen["universe"]:
        per_instrument.setdefault(instrument, 0)
    event_days = sorted(
        {
            datetime.fromtimestamp(
                row.start_received_at_ms / 1000,
                tz=timezone.utc,
            ).date().isoformat()
            for row in independent
        }
    )
    elapsed_ms = max(0, generated_at_ms - frozen["preregistered_at_ms"])
    thresholds = frozen["thresholds"]
    gates = {
        "minimum_collection_ms": elapsed_ms >= thresholds["minimum_collection_ms"],
        "minimum_eligible_event_days": len(event_days)
        >= thresholds["minimum_eligible_event_days"],
        "minimum_independent_episodes": len(independent)
        >= thresholds["minimum_independent_episodes"],
        "minimum_episodes_per_instrument": all(
            per_instrument[instrument]
            >= thresholds["minimum_episodes_per_instrument"]
            for instrument in frozen["universe"]
        ),
    }
    raw_coverage_met = all(gates.values())
    report = {
        "schema_version": "cross_venue_liquidation_health.v1",
        "generated_at_ms": generated_at_ms,
        "operational_verdict": "PASS",
        "readiness": (
            "RAW_COVERAGE_THRESHOLDS_MET" if raw_coverage_met else "COLLECTING"
        ),
        "purpose": "OUTCOME_BLIND_FORWARD_SAMPLE_MATURITY_ONLY",
        "preregistration": {
            "path": _normalized_relative(
                preregistration_path, "preregistration_path"
            ),
            "artifact_sha256": hashlib.sha256(preregistration_raw).hexdigest(),
            "preregistration_id": frozen["preregistration_id"],
            "contract_hash": frozen["contract_hash"],
            "source_commit": frozen["source_commit"],
            "preregistered_at_ms": frozen["preregistered_at_ms"],
            "earliest_unblind_at_ms": frozen["earliest_unblind_at_ms"],
            "verified": True,
        },
        "store_authority": {
            "path": _normalized_relative(store_path, "store_path"),
            "checkpoint_hash": checkpoint["checkpoint_hash"],
            "checkpoint_age_ms": checkpoint["age_ms"],
            "checkpoint_seq": checkpoint["verified_seq"],
            "verified_suffix_events": chain["verified_events"],
            "verified_head_seq": chain["verified_seq"],
            "hash_chain_valid": True,
        },
        "sample_maturity": {
            "elapsed_collection_ms": elapsed_ms,
            "raw_forward_liquidation_prints": len(prints),
            "excluded_non_forward_clock_prints": excluded,
            "closed_bursts": len(bursts),
            "independent_episodes": len(independent),
            "eligible_utc_event_days": len(event_days),
            "per_instrument_independent_episodes": dict(sorted(per_instrument.items())),
            "thresholds": thresholds,
            "gates": gates,
            "raw_coverage_thresholds_met": raw_coverage_met,
        },
        "unblinding": {
            "unblind_allowed": False,
            "reason": "TERMINAL_EVALUATOR_AND_HUMAN_L2_REVIEW_REQUIRED",
            "interim_outcome_access_allowed": False,
        },
        "outcomes_accessed": False,
        "labels_computed": False,
        "returns_computed": False,
        "trial_registry_mutated": False,
        "alpha_eligible_count": 0,
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
            "private_stream": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
            "human_review_required": True,
        },
    }
    atomic_write_json(output_path, report, indent=2)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", default=DEFAULT_PREREGISTRATION)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = run_health(
        preregistration_path=args.preregistration,
        store_path=args.store,
        checkpoint_path=args.checkpoint,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "independent_episodes": report["sample_maturity"][
                    "independent_episodes"
                ],
                "operational_verdict": report["operational_verdict"],
                "readiness": report["readiness"],
                "unblind_allowed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CHECKPOINT",
    "DEFAULT_OUTPUT",
    "DEFAULT_PREREGISTRATION",
    "DEFAULT_STORE",
    "LiquidationHealthError",
    "main",
    "run_health",
    "verify_checkpoint",
    "verify_preregistration",
]
