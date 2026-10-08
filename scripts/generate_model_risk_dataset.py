"""Generate a deterministic PIT source subset for model-risk evidence."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import os
import sqlite3
from typing import Iterable, Sequence


REPO = Path(__file__).resolve().parents[1]
TRADABLE_KEYS = (
    "AUD", "BRL", "CAD", "CHF", "CZK", "EUR", "GBP", "HUF", "ILS",
    "JPY", "KRW", "MXN", "NOK", "NZD", "PLN", "SEK", "SGD", "ZAR",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_row(row: tuple[str, int, int, str]) -> bytes:
    key, event_time, knowledge_time, payload_json = row
    return (
        json.dumps(
            {
                "event_time": event_time,
                "key": key,
                "knowledge_time": knowledge_time,
                "payload_json": payload_json,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")


def generate_bundle(
    source_db: str | Path,
    bundle_path: str | Path,
    manifest_path: str | Path,
    *,
    keys: Iterable[str] = TRADABLE_KEYS,
    repo_root: str | Path = REPO,
) -> dict:
    root = Path(repo_root).resolve()
    source = Path(source_db).resolve()
    bundle = Path(bundle_path).resolve()
    manifest_file = Path(manifest_path).resolve()
    selected = tuple(sorted(set(keys)))
    if not selected or not source.is_file():
        raise ValueError("source database and non-empty keys are required")
    bundle.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = bundle.with_name(bundle.name + ".tmp")
    rows = 0
    observed_keys: set[str] = set()
    min_event: int | None = None
    max_event: int | None = None
    placeholders = ",".join("?" for _ in selected)
    query = (
        "SELECT key, event_time, knowledge_time, payload_json FROM bars "
        f"WHERE key IN ({placeholders}) ORDER BY key, event_time, knowledge_time"
    )
    with sqlite3.connect(str(source)) as connection:
        cursor = connection.execute(query, selected)
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, mtime=0
            ) as compressed:
                for row in cursor:
                    encoded = _canonical_row(row)
                    compressed.write(encoded)
                    key, event_time, _knowledge_time, _payload = row
                    observed_keys.add(key)
                    min_event = event_time if min_event is None else min(min_event, event_time)
                    max_event = event_time if max_event is None else max(max_event, event_time)
                    rows += 1
    if rows == 0 or sorted(observed_keys) != list(selected):
        temporary.unlink(missing_ok=True)
        raise ValueError("source database does not contain the requested tradable keys")
    os.replace(temporary, bundle)
    manifest = {
        "bundle_ref": bundle.relative_to(root).as_posix(),
        "bundle_sha256": _sha256(bundle),
        "keys": list(selected),
        "max_event_time": max_event,
        "min_event_time": min_event,
        "row_count": rows,
        "schema_version": 1,
        "source_db_ref": source.relative_to(root).as_posix(),
        "source_db_sha256": _sha256(source),
        "source_query": query,
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    temporary_manifest = manifest_file.with_name(manifest_file.name + ".tmp")
    temporary_manifest.write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_manifest, manifest_file)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(REPO / "data/cache/fx.db"))
    parser.add_argument("--bundle", default=str(REPO / "evidence/datasets/fx_pit_tradable_v1.jsonl.gz"))
    parser.add_argument("--manifest", default=str(REPO / "evidence/datasets/fx_pit_tradable_v1.manifest.json"))
    args = parser.parse_args(argv)
    manifest = generate_bundle(args.source, args.bundle, args.manifest)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
