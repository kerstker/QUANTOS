import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.data.raw_archive import (
    RawArchiveError,
    archive_json_response,
    archive_response,
    load_archived_response,
    summarize_archive,
)


def _archive(root, *, payload=b"raw-response", observed_at_ms=1000):
    return archive_response(
        root=str(root),
        payload=payload,
        source_uri="https://data.example.test/market/BTC/2026-07-11.zip",
        provider="example-public-data",
        dataset_id="perp_mark_prices",
        instrument_id="BTC",
        observed_at_ms=observed_at_ms,
        representation="HTTP_BODY_BYTES",
        parse_diagnostics={
            "status": "ACCEPTED",
            "input_rows": 1,
            "accepted_rows": 1,
            "rejected_rows": 0,
            "duplicate_rows": 0,
        },
    )


def test_raw_response_is_content_addressed_and_idempotent(tmp_path):
    first = _archive(tmp_path)
    second = _archive(tmp_path)

    assert first == second
    object_path = tmp_path / first["object_path"]
    observation_path = tmp_path / first["observation_path"]
    assert object_path.read_bytes() == b"raw-response"
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    assert observation["artifact_sha256"] == first["artifact_sha256"]
    assert observation["parse_diagnostics"]["rejected_rows"] == 0

    summary = summarize_archive(str(tmp_path))
    assert summary["status"] == "PASS"
    assert summary["observation_count"] == 1
    assert summary["object_count"] == 1
    assert summary["datasets"] == {"perp_mark_prices": 1}


def test_canonical_json_response_is_order_stable(tmp_path):
    common = {
        "root": str(tmp_path),
        "source_uri": "https://fapi.example.test/funding?symbol=BTCUSDT",
        "provider": "example-public-data",
        "dataset_id": "funding",
        "instrument_id": "BTC",
        "observed_at_ms": 1000,
        "parse_diagnostics": {"status": "ACCEPTED", "input_rows": 1},
    }
    left = archive_json_response(payload={"b": 2, "a": 1}, **common)
    right = archive_json_response(payload={"a": 1, "b": 2}, **common)

    assert left == right
    assert left["representation"] == "CANONICAL_JSON_UTF8"


def test_parallel_observations_share_one_immutable_object(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as executor:
        refs = list(executor.map(
            lambda at: _archive(tmp_path, observed_at_ms=at),
            range(1000, 1008),
        ))

    assert len({ref["artifact_sha256"] for ref in refs}) == 1
    summary = summarize_archive(str(tmp_path))
    assert summary["status"] == "PASS"
    assert summary["observation_count"] == 8
    assert summary["object_count"] == 1


def test_scoped_summary_is_stable_under_unrelated_archive_growth(tmp_path):
    _archive(tmp_path)
    before = summarize_archive(
        str(tmp_path),
        dataset_ids={"perp_mark_prices"},
        instrument_ids={"BTC"},
    )
    archive_json_response(
        root=str(tmp_path),
        payload={"depth": [[1, 2]]},
        source_uri="https://data.example.test/depth/ETH",
        provider="example-public-data",
        dataset_id="binance_realtime_depth",
        instrument_id="ETH",
        observed_at_ms=2000,
        parse_diagnostics={"status": "ACCEPTED", "input_rows": 1},
    )
    after = summarize_archive(
        str(tmp_path),
        dataset_ids={"perp_mark_prices"},
        instrument_ids={"BTC"},
    )

    assert before == after
    assert before["observation_count"] == 1
    assert before["datasets"] == {"perp_mark_prices": 1}
    assert summarize_archive(str(tmp_path))["observation_count"] == 2


def test_load_archived_response_returns_latest_verified_event(tmp_path):
    common = {
        "root": str(tmp_path),
        "source_uri": "https://data.example.test/market/BTC/day.zip",
        "provider": "example-public-data",
        "dataset_id": "perp_mark_prices",
        "instrument_id": "BTC",
        "representation": "HTTP_BODY_BYTES",
    }
    archive_response(
        payload=b"older", observed_at_ms=1000,
        parse_diagnostics={"status": "ACCEPTED", "event_time_ms": 500},
        **common,
    )
    archive_response(
        payload=b"newer", observed_at_ms=2000,
        parse_diagnostics={"status": "ACCEPTED", "event_time_ms": 500},
        **common,
    )

    replay = load_archived_response(
        str(tmp_path), dataset_id="perp_mark_prices",
        instrument_id="BTC", event_time_ms=500,
    )

    assert replay["payload"] == b"newer"
    assert replay["observation"]["observed_at_ms"] == 2000
    assert load_archived_response(
        str(tmp_path), dataset_id="perp_mark_prices",
        instrument_id="ETH", event_time_ms=500,
    ) is None


def test_archive_summary_blocks_tampered_object(tmp_path):
    ref = _archive(tmp_path)
    (tmp_path / ref["object_path"]).write_bytes(b"tampered")

    summary = summarize_archive(str(tmp_path))

    assert summary["status"] == "BLOCK"
    assert summary["invalid_observation_count"] == 1
    assert summary["errors"][0]["kind"] == "OBJECT_DIGEST_MISMATCH"


def test_archive_summary_blocks_rehashed_invalid_observation(tmp_path):
    ref = _archive(tmp_path)
    original = tmp_path / ref["observation_path"]
    observation = json.loads(original.read_text(encoding="utf-8"))
    observation["representation"] = "FORGED_WIRE_BYTES"
    raw = json.dumps(
        observation,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    replacement = tmp_path / "observations" / "sha256" / digest[:2] / f"{digest}.json"
    replacement.parent.mkdir(parents=True)
    replacement.write_bytes(raw)
    original.unlink()

    summary = summarize_archive(str(tmp_path))

    assert summary["status"] == "BLOCK"
    assert summary["invalid_observation_count"] == 1
    assert summary["errors"][0]["kind"] == "OBSERVATION_SCHEMA_INVALID"


def test_archive_rejects_sensitive_uri_and_nonfinite_diagnostics(tmp_path):
    with pytest.raises(RawArchiveError, match="sensitive query parameter"):
        archive_response(
            root=str(tmp_path),
            payload=b"x",
            source_uri="https://example.test/data?api_key=secret",
            provider="example",
            dataset_id="data",
            instrument_id="BTC",
            observed_at_ms=1,
            representation="HTTP_BODY_BYTES",
        )

    with pytest.raises(RawArchiveError, match="JSON serializable"):
        archive_response(
            root=str(tmp_path),
            payload=b"x",
            source_uri="https://example.test/data",
            provider="example",
            dataset_id="data",
            instrument_id="BTC",
            observed_at_ms=1,
            representation="HTTP_BODY_BYTES",
            parse_diagnostics={"status": "ACCEPTED", "ratio": float("nan")},
        )
