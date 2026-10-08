"""Full retained results, original identities and original cash-availability math."""

from collections.abc import Mapping, Sequence
import hashlib
import json

import pytest

from quantos.contracts.backtest_identity import exact_identity_hash
from services.execution.perpetual_simulation import LinearPerpetualSimulationSession
from tests.execution.test_perpetual_simulation import frame
from tests.execution.test_perpetual_simulation_session import configuration


def plain(value):
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [plain(item) for item in value]
    return value


def original():
    s = LinearPerpetualSimulationSession(**configuration())
    s.advance((frame(0), frame(10)))
    return s.report()


def sealed(root, result, *, raw=None, part_bytes=97):
    root.mkdir(mode=0o700)
    (root / "blobs").mkdir(mode=0o700)
    raw = (
        raw
        if raw is not None
        else json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    )
    parts = []
    for offset in range(0, len(raw), part_bytes):
        value = raw[offset : offset + part_bytes]
        sha = hashlib.sha256(value).hexdigest()
        p = root / "blobs" / sha
        p.write_bytes(value)
        p.chmod(0o600)
        parts.append(dict(sha256=sha, bytes=len(value)))
    manifest = dict(
        schema="PERPETUAL_SIMULATION_CHUNKED_RESULT_V1",
        status="TECHNICAL_VALIDATION_ONLY",
        parts=parts,
        bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        input_hash=result["input_hash"],
        result_hash=result["result_hash"],
        authoritative=False,
        economic_eligible=False,
        submission_enabled=False,
        native_registered=False,
        input_archive=dict(file="history.sqlite3", sha256="a" * 64, frame_count=2, batch_count=1),
    )
    value = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (root / "manifest.json").write_bytes(value)
    (root / "manifest.json").chmod(0o600)
    return hashlib.sha256(value).hexdigest()


def reader(root, scratch, pin, **kwargs):
    from services.research.perpetual_chunked_result_reader import open_result

    return open_result(
        root,
        scratch,
        expected_manifest_sha256=pin,
        maximum_source_bytes=32_000_000,
        maximum_index_bytes=64_000_000,
        **kwargs,
    )


def test_original_account_all_fields_hashes_and_repeatable_views_survive_chunk_boundaries(tmp_path):
    from tools.perpetual_stream_history import SimulationHistory, write_result

    root = tmp_path / "result"
    config = configuration()
    config["dataset_identity"]["unicode"] = '中文🙂\n"\\'
    with SimulationHistory(tmp_path / "history", maximum_bytes=32_000_000) as history:
        s = LinearPerpetualSimulationSession(history_store=history, **config)
        s.advance((frame(0), frame(10)))
        expected = s.report()
        write_result(s, root, maximum_bytes=32_000_000, chunk_bytes=97)
    pin = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    with reader(root, tmp_path / "index", pin) as report:
        assert plain(report) == expected
        assert list(report["equity_history"]) == list(report["equity_history"])
        assert plain(report["equity_history"][-1]) == expected["equity_history"][-1]
        assert len(report["equity_history"]) == len(expected["equity_history"])
        from quantos.streaming_identity import exact_stream_identity_hash

        assert exact_stream_identity_hash(report) == exact_identity_hash(expected)
    with pytest.raises(ValueError, match="CLOSED"):
        report["result_hash"]


@pytest.mark.parametrize("damage", ["missing", "extra", "mode", "manifest", "hash", "authority", "duplicate", "syntax"])
def test_missing_tampered_unbound_or_invalid_complete_results_are_rejected(tmp_path, damage):
    result = original()
    raw = None
    if damage == "hash":
        result["reconciliation"]["ending_equity"] = "999"
    elif damage == "authority":
        result["authoritative"] = True
        result["result_hash"] = exact_identity_hash({k: v for k, v in result.items() if k != "result_hash"})
    elif damage == "duplicate":
        raw = b'{"duplicate":0,"duplicate":1}'
    elif damage == "syntax":
        raw = b'{"value":[1,]}'
    root = tmp_path / "result"
    pin = sealed(root, result, raw=raw)
    names = sorted((root / "blobs").iterdir())
    if damage == "missing":
        names[0].unlink()
    elif damage == "extra":
        (root / "blobs" / "extra").write_bytes(b"x")
    elif damage == "mode":
        names[0].chmod(0o644)
    elif damage == "manifest":
        (root / "manifest.json").write_bytes((root / "manifest.json").read_bytes() + b" ")
    with pytest.raises((ValueError, UnicodeError)):
        with reader(root, tmp_path / "index", pin):
            pytest.fail("invalid result exposed to consumer")


@pytest.mark.parametrize("target", ["source", "index"])
def test_same_byte_rewrite_during_consumer_read_invalidates_the_cut(tmp_path, target):
    root = tmp_path / "result"
    pin = sealed(root, original())
    scratch = tmp_path / "index"
    with pytest.raises(ValueError, match="CHANGED"):
        with reader(root, scratch, pin) as report:
            assert report["authoritative"] is False
            path = next((root / "blobs").iterdir()) if target == "source" else scratch / "index.sqlite3"
            path.write_bytes(path.read_bytes())


@pytest.mark.parametrize("damage", ["manifest_mode", "scratch_member", "scratch_mode"])
def test_original_manifest_and_index_directory_custody_are_verified(tmp_path, damage):
    root = tmp_path / "result"
    pin = sealed(root, original())
    scratch = tmp_path / "index"
    if damage == "manifest_mode":
        (root / "manifest.json").chmod(0o644)
    with pytest.raises(ValueError):
        with reader(root, scratch, pin):
            if damage == "scratch_member":
                (scratch / "extra").write_bytes(b"changed cut")
            elif damage == "scratch_mode":
                scratch.chmod(0o777)


def test_missing_original_result_fields_raise_a_structural_rejection(tmp_path):
    root = tmp_path / "result"
    pin = sealed(root, original(), raw=b'{"unrelated":0}')
    with pytest.raises(ValueError):
        with reader(root, tmp_path / "index", pin):
            pytest.fail("missing original fields admitted")


@pytest.mark.parametrize("budget", ["bytes", "nodes", "scalar"])
def test_index_budgets_fail_before_exposing_a_partial_result(tmp_path, budget):
    root = tmp_path / "result"
    pin = sealed(root, original())
    options = {"maximum_nodes": 3} if budget == "nodes" else {"maximum_scalar_bytes": 3} if budget == "scalar" else {}
    if budget == "bytes":
        from services.research.perpetual_chunked_result_reader import open_result

        port = open_result(
            root,
            tmp_path / "index",
            expected_manifest_sha256=pin,
            maximum_source_bytes=32_000_000,
            maximum_index_bytes=4096,
        )
    else:
        port = reader(root, tmp_path / "index", pin, **options)
    with pytest.raises(ValueError):
        with port:
            pytest.fail("partial result exposed")


def test_original_gold_receipt_transform_consumes_complete_disk_result(tmp_path, monkeypatch):
    from services.validation.gold_native_statistics import (
        funding_settlements,
        receipt_available_history,
        receipt_available_history_stream,
    )
    from tools.gold_bar_experiment import _execute, _encode
    from tests.tools.test_gold_segmented_replay import funding_arguments

    args = funding_arguments(tmp_path, monkeypatch)
    comparison = _execute(args)
    simulation = comparison["trials"][0]["simulation"]
    settlements = funding_settlements(_encode(args), comparison["dataset_identity"])
    expected = receipt_available_history(simulation, settlements)
    root = tmp_path / "result"
    pin = sealed(root, simulation)
    with reader(root, tmp_path / "index", pin) as report:
        stream = receipt_available_history_stream(report, settlements, maximum_points=100_000, maximum_events=100_000)
        assert list(stream.items()) == expected == list(stream.items())
        assert report["reconciliation"]["residual"] == "0"
        from quantos.contracts.research_statistics import fixed_frequency_equity

        start, end = expected[0]["timestamp"], expected[-1]["timestamp"]
        assert fixed_frequency_equity(
            stream.items(), start_ms=start, end_ms=end, frequency_ms=1000
        ) == fixed_frequency_equity(expected, start_ms=start, end_ms=end, frequency_ms=1000)


def test_original_fixed_frequency_sampler_keeps_bounded_source_state_and_checks_late_rows():
    import tracemalloc
    from quantos.contracts.research_statistics import fixed_frequency_equity

    def rows():
        for index in range(50_000):
            yield dict(timestamp=index, sequence=index, equity="100")

    tracing = tracemalloc.is_tracing()
    if not tracing:
        tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    try:
        grid = fixed_frequency_equity(rows(), start_ms=0, end_ms=10_000, frequency_ms=1000)
        peak = tracemalloc.get_traced_memory()[1] - before
    finally:
        if not tracing:
            tracemalloc.stop()
    assert len(grid["points"]) == 11 and peak < 2_000_000, peak

    def damaged():
        yield from rows()
        yield dict(timestamp=50_000, sequence=50_000, equity="NaN")

    with pytest.raises(ValueError, match="finite"):
        fixed_frequency_equity(damaged(), start_ms=0, end_ms=1000, frequency_ms=1000)


def test_large_receipt_series_requires_explicit_new_budget_without_raising_legacy_limit():
    from services.validation.gold_native_statistics import receipt_available_history_stream

    class Points(Sequence):
        def __len__(self):
            return 100_001

        def __getitem__(self, index):
            if not 0 <= index < len(self):
                raise IndexError(index)
            return dict(timestamp=index, sequence=index, equity="100", phase="INITIAL" if index == 0 else "MARKET")

    simulation = dict(equity_history=Points(), ledger_events=[])
    with pytest.raises(ValueError):
        receipt_available_history_stream(simulation, [], maximum_points=100_000, maximum_events=1)
    stream = receipt_available_history_stream(simulation, [], maximum_points=100_001, maximum_events=1)
    count = 0
    for point in stream.items():
        count += 1
        assert point["equity"] == "100"
    assert count == 100_001
