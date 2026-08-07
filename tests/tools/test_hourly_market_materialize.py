import json
import os
from pathlib import Path

import pytest

from services.data.hourly_universe import UniversePolicy
from tools import hourly_market_materialize as mm
from tools.hourly_market_materialize import load_materialized_partition, materialize_hourly_market


def _raw(tmp_path: Path, *, bars=48, gap=None, schema=None):
    root = tmp_path / "raw"
    root.mkdir(parents=True)
    rows = []
    for i in range(bars):
        if i == gap:
            continue
        t = i * 300
        rows.append({"schema_version": schema or mm.INPUT_SCHEMA, "kind": "bar", "source_digest": "a" * 64,
                     "instrument": "BTC", "event_time": t, "knowledge_time": t, "open": 1.0,
                     "high": 2.0, "low": 1.0, "close": 1.5, "quote_volume": 1.0, "interval_seconds": 300})
    rows += [
        {"schema_version": mm.INPUT_SCHEMA, "kind": "funding", "source_digest": "a" * 64, "instrument": "BTC", "event_time": 0, "knowledge_time": 0, "rate": .1},
        {"schema_version": mm.INPUT_SCHEMA, "kind": "open_interest", "source_digest": "a" * 64, "instrument": "BTC", "event_time": 0, "knowledge_time": 0, "value": 3.0},
        {"schema_version": mm.INPUT_SCHEMA, "kind": "universe", "source_digest": "a" * 64, "instrument": "BTC", "event_time": 0, "knowledge_time": 0, "quote_volume": 1.0, "spread_bps": 1.0, "listed_at": 0, "data_coverage": 1.0},
    ]
    (root / "records.jsonl").write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows), encoding="utf-8")
    return root


def _policy():
    return UniversePolicy(min_size=1, max_size=1)


def test_materializes_both_intervals_and_is_byte_deterministic(tmp_path):
    raw, out = _raw(tmp_path), tmp_path / "out"
    first = materialize_hourly_market(raw_root=raw, output_root=out, as_of=14100, policy=_policy())
    before = [(p.read_bytes(), p.with_name("data.jsonl").read_bytes()) for p in first.manifests]
    second = materialize_hourly_market(raw_root=raw, output_root=out, as_of=14100, policy=_policy())
    assert first.manifest_digests == second.manifest_digests
    assert before == [(p.read_bytes(), p.with_name("data.jsonl").read_bytes()) for p in second.manifests]
    assert len(load_materialized_partition(first.manifests[0].parent)) == 6


def test_intervals_are_explicit_and_validated(tmp_path):
    raw = _raw(tmp_path)
    with pytest.raises(ValueError):
        materialize_hourly_market(raw_root=raw, output_root=tmp_path / "o", as_of=14100, policy=_policy(), intervals=(60,))
    receipt = materialize_hourly_market(raw_root=raw, output_root=tmp_path / "o", as_of=14100, policy=_policy(), intervals=(3600,))
    assert len(receipt.manifests) == 1


def test_excluded_incomplete_instrument_does_not_abort_selected_materialization(tmp_path):
    raw = _raw(tmp_path, bars=48)
    path = next(raw.glob("*.jsonl"))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows = [row for row in rows if row["instrument"] == "BTC"]
    rows.extend(
        {
            "schema_version": mm.INPUT_SCHEMA,
            "kind": "bar",
            "source_digest": "b" * 64,
            "instrument": "ALT",
            "event_time": i * 300,
            "knowledge_time": i * 300,
            "open": 1.0,
            "high": 2.0,
            "low": 1.0,
            "close": 1.5,
            "quote_volume": 1.0,
            "interval_seconds": 300,
        }
        for i in range(48)
        if i != 7
    )
    rows.extend([
        {"schema_version": mm.INPUT_SCHEMA, "kind": "funding", "source_digest": "b" * 64,
         "instrument": "ALT", "event_time": 0, "knowledge_time": 0, "rate": .1},
        {"schema_version": mm.INPUT_SCHEMA, "kind": "open_interest", "source_digest": "b" * 64,
         "instrument": "ALT", "event_time": 0, "knowledge_time": 0, "value": 3.0},
        {"schema_version": mm.INPUT_SCHEMA, "kind": "universe", "source_digest": "b" * 64,
         "instrument": "ALT", "event_time": 0, "knowledge_time": 0, "quote_volume": .1,
         "spread_bps": 1.0, "listed_at": 0, "data_coverage": 1.0},
    ])
    path.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows), encoding="utf-8")

    receipt = materialize_hourly_market(
        raw_root=raw, output_root=tmp_path / "out", as_of=14100,
        policy=_policy(), intervals=(3600,)
    )
    output = load_materialized_partition(receipt.manifests[0].parent)
    assert output and {row["instrument"] for row in output} == {"BTC"}


def test_future_gap_schema_and_missing_kinds_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="gap"):
        materialize_hourly_market(raw_root=_raw(tmp_path, gap=3), output_root=tmp_path / "o", as_of=14100, policy=_policy())
    bad = _raw(tmp_path / "schema", schema="wrong")
    with pytest.raises(ValueError):
        materialize_hourly_market(raw_root=bad, output_root=tmp_path / "o2", as_of=14100, policy=_policy())
    missing = _raw(tmp_path / "missing")
    path = missing / "records.jsonl"
    path.write_text("\n".join(line for line in path.read_text().splitlines() if '"kind":"funding"' not in line))
    with pytest.raises(ValueError, match="nonempty"):
        materialize_hourly_market(raw_root=missing, output_root=tmp_path / "o3", as_of=14100, policy=_policy())


def test_duplicate_and_future_records_fail_closed(tmp_path):
    raw = _raw(tmp_path)
    path = raw / "records.jsonl"
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines + [lines[0]]))
    with pytest.raises(ValueError, match="duplicate"):
        materialize_hourly_market(raw_root=raw, output_root=tmp_path / "o", as_of=14100, policy=_policy())
    raw2 = _raw(tmp_path / "future")
    lines = [json.loads(x) for x in (raw2 / "records.jsonl").read_text().splitlines()]
    lines[0]["knowledge_time"] = 14101
    (raw2 / "records.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    with pytest.raises(ValueError, match="future"):
        materialize_hourly_market(raw_root=raw2, output_root=tmp_path / "o2", as_of=14100, policy=_policy())


def test_universe_admission_and_loader_tamper_checks(tmp_path):
    raw = _raw(tmp_path)
    with pytest.raises(ValueError):
        materialize_hourly_market(raw_root=raw, output_root=tmp_path / "o", as_of=14100, policy=UniversePolicy(min_size=2, max_size=2))
    out = tmp_path / "out"
    receipt = materialize_hourly_market(raw_root=raw, output_root=out, as_of=14100, policy=_policy())
    partition = receipt.manifests[0].parent
    manifest = partition / "manifest.json"
    payload = json.loads(manifest.read_text())
    payload["manifest_digest"] = "0" * 64
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_materialized_partition(partition)


def test_publish_immutable_lock_and_replace_failure(tmp_path, monkeypatch):
    path = tmp_path / "x"
    mm._publish(path, b"same")
    mm._publish(path, b"same")
    with pytest.raises(FileExistsError):
        mm._publish(path, b"different")
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OSError):
        mm._publish(tmp_path / "y", b"data")
    assert not (tmp_path / "y").exists()
    assert not (tmp_path / "y" / "manifest.json").exists()




def _rewrite_rows(root: Path, mutate):
    path = next(root.glob("*.jsonl"))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    mutate(rows)
    path.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")


def test_event_buckets_and_auxiliary_knowledge_time_are_pit_correct(tmp_path):
    raw, out = _raw(tmp_path, bars=48), tmp_path / "out"
    path = next(raw.glob("*.jsonl"))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows.extend([
        {"schema_version": mm.INPUT_SCHEMA, "kind": "funding", "source_digest": "b" * 64,
         "instrument": "BTC", "event_time": 1800, "knowledge_time": 1900, "rate": 0.2},
        {"schema_version": mm.INPUT_SCHEMA, "kind": "funding", "source_digest": "b" * 64,
         "instrument": "BTC", "event_time": 3599, "knowledge_time": 3599, "rate": 0.3},
        {"schema_version": mm.INPUT_SCHEMA, "kind": "open_interest", "source_digest": "b" * 64,
         "instrument": "BTC", "event_time": 1800, "knowledge_time": 2000, "value": 4.0},
        {"schema_version": mm.INPUT_SCHEMA, "kind": "open_interest", "source_digest": "b" * 64,
         "instrument": "BTC", "event_time": 3599, "knowledge_time": 3599, "value": 5.0},
    ])
    path.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")
    receipt = materialize_hourly_market(raw_root=raw, output_root=out, as_of=14100, policy=_policy(), intervals=(3600,))
    rows = load_materialized_partition(receipt.manifests[0].parent)
    funding = [r for r in rows if r["kind"] == "funding"]
    oi = [r for r in rows if r["kind"] == "open_interest"]
    assert funding and oi
    assert max(r["knowledge_time"] for r in funding) == 3599
    assert max(r["knowledge_time"] for r in oi) == 3599


def test_four_hour_output_has_complete_aligned_bucket(tmp_path):
    raw, out = _raw(tmp_path, bars=48), tmp_path / "out"
    receipt = materialize_hourly_market(raw_root=raw, output_root=out, as_of=14100, policy=_policy(), intervals=(14400,))
    bars = [r for r in load_materialized_partition(receipt.manifests[0].parent) if r["kind"] == "bar"]
    assert [r["event_time"] for r in bars] == [0]
    assert bars[0]["open"] == 1.0 and bars[0]["close"] == 1.5


@pytest.mark.parametrize("bad_kind", ["mystery", "BAR", ""])
def test_unknown_or_noncanonical_kind_rejected(tmp_path, bad_kind):
    raw = _raw(tmp_path)
    _rewrite_rows(raw, lambda rows: rows.__setitem__(0, {**rows[0], "kind": bad_kind}))
    with pytest.raises(ValueError, match="kind"):
        materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy())


@pytest.mark.parametrize("field", ["instrument", "event_time", "knowledge_time", "open", "high", "low", "close", "quote_volume", "interval_seconds"])
def test_missing_required_bar_field_rejected(tmp_path, field):
    raw = _raw(tmp_path)
    def mutate(rows):
        rows[0].pop(field)
    _rewrite_rows(raw, mutate)
    with pytest.raises(ValueError, match="missing"):
        materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_numeric_input_rejected(tmp_path, value):
    raw = _raw(tmp_path)
    _rewrite_rows(raw, lambda rows: rows[0].__setitem__("close", value))
    with pytest.raises(ValueError):
        materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy())


def test_huge_integer_and_overflow_are_value_errors(tmp_path):
    raw = _raw(tmp_path)
    _rewrite_rows(raw, lambda rows: rows[0].__setitem__("event_time", 1 << 200))
    with pytest.raises(ValueError):
        materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy())


def test_sparse_auxiliary_timestamps_are_legal_but_each_selected_instrument_needs_both(tmp_path):
    raw = _raw(tmp_path, bars=48)
    receipt = materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy(), intervals=(3600,))
    assert load_materialized_partition(receipt.manifests[0].parent)
    raw2 = _raw(tmp_path / "missing-oi", bars=48)
    _rewrite_rows(raw2, lambda rows: rows.__setitem__(1, {**rows[1], "kind": "funding"}))
    with pytest.raises(ValueError):
        materialize_hourly_market(raw_root=raw2, output_root=tmp_path / "out2", as_of=14100, policy=_policy())


def test_future_event_time_is_rejected_even_when_knowledge_time_is_old(tmp_path):
    raw = _raw(tmp_path)
    _rewrite_rows(raw, lambda rows: rows[0].update(event_time=14101, knowledge_time=14101))
    with pytest.raises(ValueError, match="future"):
        materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy())


@pytest.mark.parametrize("field,value", [("source_digest", "A" * 64), ("source_digest", "0" * 63), ("source_digest", ""), ("source_digest", "g" * 64)])
def test_input_source_digest_must_be_lowercase_hex64(tmp_path, field, value):
    raw = _raw(tmp_path)
    _rewrite_rows(raw, lambda rows: rows[0].__setitem__(field, value))
    with pytest.raises(ValueError, match="digest"):
        materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy())


@pytest.mark.parametrize("path_value", ["", "a\\b.jsonl", "/absolute.jsonl", "../x.jsonl", "./x.jsonl"])
def test_manifest_input_provenance_rejects_unsafe_paths(tmp_path, path_value):
    raw = _raw(tmp_path)
    receipt = materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy(), intervals=(3600,))
    manifest = receipt.manifests[0]
    payload = json.loads(manifest.read_text())
    payload["input_files"][0]["path"] = path_value
    payload["manifest_digest"] = mm._sha(mm._canonical({k: v for k, v in payload.items() if k != "manifest_digest"}))
    manifest.write_bytes(mm._canonical(payload))
    with pytest.raises(ValueError):
        load_materialized_partition(manifest.parent)


def test_reader_rejects_manifest_without_input_files_and_data_without_manifest(tmp_path):
    raw = _raw(tmp_path)
    receipt = materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy(), intervals=(3600,))
    partition = receipt.manifests[0].parent
    (partition / "manifest.json").unlink()
    with pytest.raises(ValueError):
        load_materialized_partition(partition)
    (partition / "data.jsonl").write_bytes(b"{}\n")
    with pytest.raises(ValueError):
        load_materialized_partition(partition)


def test_reader_rejects_noncanonical_manifest_or_jsonl_even_with_recomputed_digests(tmp_path):
    raw = _raw(tmp_path)
    receipt = materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy(), intervals=(3600,))
    partition = receipt.manifests[0].parent
    manifest = json.loads((partition / "manifest.json").read_text())
    data = (partition / "data.jsonl").read_bytes()
    (partition / "data.jsonl").write_bytes(data.replace(b"\n", b" \n", 1))
    manifest["output_sha256"] = mm._sha((partition / "data.jsonl").read_bytes())
    manifest["manifest_digest"] = mm._sha(mm._canonical({k: v for k, v in manifest.items() if k != "manifest_digest"}))
    (partition / "manifest.json").write_bytes(mm._canonical(manifest))
    with pytest.raises(ValueError, match="canonical"):
        load_materialized_partition(partition)


def test_manifest_provenance_rejects_duplicate_paths_and_absolute_paths(tmp_path):
    raw = _raw(tmp_path)
    receipt = materialize_hourly_market(raw_root=raw, output_root=tmp_path / "out", as_of=14100, policy=_policy(), intervals=(3600,))
    partition = receipt.manifests[0].parent
    payload = json.loads((partition / "manifest.json").read_text())
    payload["input_files"] = [{"path": "records.jsonl", "sha256": "a" * 64}, {"path": "records.jsonl", "sha256": "a" * 64}]
    payload["manifest_digest"] = mm._sha(mm._canonical({k: v for k, v in payload.items() if k != "manifest_digest"}))
    (partition / "manifest.json").write_bytes(mm._canonical(payload))
    with pytest.raises(ValueError):
        load_materialized_partition(partition)


def test_os_lock_stale_file_is_reusable_and_held_lock_fails_closed(tmp_path):
    path = tmp_path / "artifact"
    lock = path.parent / ".artifact.lock"
    lock.write_bytes(b"x")
    mm._publish(path, b"stable")
    assert path.read_bytes() == b"stable"
    with mm._advisory_lock(path.parent / ".held.lock"):
        with pytest.raises(FileExistsError):
            mm._publish(tmp_path / "held", b"nope")


def test_cli_exposes_interval_and_all_universe_policy_controls(monkeypatch):
    captured = {}
    monkeypatch.setattr(mm, "materialize_hourly_market", lambda **kwargs: captured.update(kwargs) or mm.MaterializationReceipt((), ()))
    assert mm.main(["--raw-root", "r", "--output-root", "o", "--as-of", "7", "--interval-seconds", "3600",
                    "--min-size", "2", "--max-size", "4", "--min-listing-age-seconds", "9",
                    "--max-spread-bps", "3.5", "--min-data-coverage", "0.8"]) == 0
    assert captured["intervals"] == (3600,)
    assert captured["policy"].min_size == 2 and captured["policy"].max_size == 4
    assert captured["policy"].min_listing_age_seconds == 9
    assert captured["policy"].max_spread_bps == 3.5 and captured["policy"].min_data_coverage == 0.8
