import gzip
import json
import sqlite3

import pytest

from scripts.generate_model_risk_dataset import generate_bundle
from services.governance.model_risk_dataset import DatasetBundleError, verify_pit_bundle


def _source(tmp_path):
    path = tmp_path / "fx.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE bars (key TEXT NOT NULL, event_time INTEGER NOT NULL, "
        "knowledge_time INTEGER NOT NULL, payload_json TEXT NOT NULL, "
        "PRIMARY KEY (key, event_time, knowledge_time))"
    )
    connection.executemany(
        "INSERT INTO bars VALUES (?, ?, ?, ?)",
        [
            ("AUD", 1, 1, '{"close":1}'),
            ("AUD", 2, 2, '{"close":2}'),
            ("EUR", 1, 1, '{"close":3}'),
        ],
    )
    connection.commit()
    connection.close()
    return path


def test_bundle_is_deterministic_and_reconstructable(tmp_path):
    source = _source(tmp_path)
    bundle = tmp_path / "bundle.jsonl.gz"
    manifest = tmp_path / "bundle.manifest.json"
    first = generate_bundle(source, bundle, manifest, keys=("AUD", "EUR"), repo_root=tmp_path)
    first_bytes = bundle.read_bytes()
    second = generate_bundle(source, bundle, manifest, keys=("AUD", "EUR"), repo_root=tmp_path)
    assert first == second
    assert first_bytes == bundle.read_bytes()
    assert verify_pit_bundle(bundle, manifest, repo_root=tmp_path, source_db_path=source)["row_count"] == 3
    with gzip.open(bundle, "rt", encoding="utf-8") as stream:
        assert len(stream.readlines()) == 3


def test_bundle_verifier_rejects_manifest_or_row_tampering(tmp_path):
    source = _source(tmp_path)
    bundle = tmp_path / "bundle.jsonl.gz"
    manifest = tmp_path / "bundle.manifest.json"
    generate_bundle(source, bundle, manifest, keys=("AUD", "EUR"), repo_root=tmp_path)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["row_count"] = 99
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(DatasetBundleError):
        verify_pit_bundle(bundle, manifest, repo_root=tmp_path)


def test_bundle_verifier_rejects_out_of_order_rows(tmp_path):
    source = _source(tmp_path)
    bundle = tmp_path / "bundle.jsonl.gz"
    manifest = tmp_path / "bundle.manifest.json"
    generate_bundle(source, bundle, manifest, keys=("AUD", "EUR"), repo_root=tmp_path)
    with gzip.open(bundle, "rt", encoding="utf-8") as stream:
        rows = stream.readlines()
    import io
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as stream:
        stream.write("".join(reversed(rows)).encode("utf-8"))
    bundle.write_bytes(output.getvalue())
    with pytest.raises(DatasetBundleError):
        verify_pit_bundle(bundle, manifest, repo_root=tmp_path)
