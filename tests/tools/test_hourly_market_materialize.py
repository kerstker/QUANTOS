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
        rows.append({"schema_version": schema or mm.INPUT_SCHEMA, "kind": "bar", "source_digest": "src",
                     "instrument": "BTC", "event_time": t, "knowledge_time": t, "open": 1.0,
                     "high": 2.0, "low": 1.0, "close": 1.5, "quote_volume": 1.0, "interval_seconds": 300})
    rows += [
        {"schema_version": mm.INPUT_SCHEMA, "kind": "funding", "source_digest": "src", "instrument": "BTC", "event_time": 0, "knowledge_time": 0, "rate": .1},
        {"schema_version": mm.INPUT_SCHEMA, "kind": "open_interest", "source_digest": "src", "instrument": "BTC", "event_time": 0, "knowledge_time": 0, "value": 3.0},
        {"schema_version": mm.INPUT_SCHEMA, "kind": "universe", "source_digest": "src", "instrument": "BTC", "event_time": 0, "knowledge_time": 0, "quote_volume": 1.0, "spread_bps": 1.0, "listed_at": 0, "data_coverage": 1.0},
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
    lock = path.parent / ".x.lock"
    lock.touch()
    with pytest.raises(FileExistsError):
        mm._publish(path, b"same")
    lock.unlink()
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OSError):
        mm._publish(tmp_path / "y", b"data")
    assert not (tmp_path / "y").exists()
    assert not (tmp_path / "y" / "manifest.json").exists()


def test_cli_requires_explicit_as_of():
    import subprocess, sys
    result = subprocess.run([sys.executable, "-m", "tools.hourly_market_materialize", "--raw-root", ".", "--output-root", "out"], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode != 0
    assert "as-of" in (result.stderr + result.stdout)
