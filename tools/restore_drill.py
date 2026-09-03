"""Offline backup restore drill for QUANTOS.

The drill restores one backup batch into an isolated target, verifies every
restored byte, enforces an RTO budget, and emits machine-readable evidence.
It never restores into active application state.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import shutil
import tempfile
import time
from typing import Any

from tools.live_backup import _check_passphrase_strength, decrypt_file


# A few multi-gigabyte authorities dominate restore time. Schedule them first
# and keep three files in flight with eight authenticated chunks each: that
# matches the 20-core ARM64 deployment more closely than four files / 32 crypto
# workers, while writes and hashing remain ordered within every artifact.
MAX_PARALLEL_RESTORE_FILES = 3
MAX_PARALLEL_DECRYPT_CHUNKS = 8


class RestoreDrillError(RuntimeError):
    """The restore drill cannot prove a safe, complete recovery."""


def _sha256_file(path: str, *, block_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while block := source.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def _evidence_path(path: str) -> str:
    absolute = os.path.abspath(path)
    try:
        relative = os.path.relpath(absolute, os.getcwd())
    except ValueError:
        return absolute
    return relative.replace(os.sep, "/") if relative != ".." and not relative.startswith("../") else absolute


def _batch_files(batch_dir: str) -> list[str]:
    if not os.path.isdir(batch_dir):
        raise RestoreDrillError(f"backup batch does not exist: {batch_dir}")
    files = [entry.path for entry in os.scandir(batch_dir) if entry.is_file(follow_symlinks=False)]
    if not files:
        raise RestoreDrillError("backup batch is empty")
    return sorted(files)


def _restore_mode(files: list[str]) -> str:
    encrypted = [path.endswith(".enc") for path in files]
    if any(encrypted) and not all(encrypted):
        raise RestoreDrillError("mixed encrypted and plaintext backup batches are not allowed")
    return "ENCRYPTED" if all(encrypted) else "PLAINTEXT"


def _largest_first_restore_items(
    files: list[str],
    restored_names: list[str],
) -> list[tuple[str, str]]:
    """Schedule dominant artifacts first without changing evidence ordering."""
    return sorted(
        zip(files, restored_names),
        key=lambda item: (-os.path.getsize(item[0]), item[0]),
    )


def _prepare_target(restore_dir: str) -> tuple[str, str]:
    target = os.path.abspath(restore_dir)
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(target):
        if not os.path.isdir(target) or os.listdir(target):
            raise RestoreDrillError("restore target must be an empty directory or not exist")
    stage = tempfile.mkdtemp(prefix=".quantos-restore-drill-", dir=parent)
    return target, stage


def run_restore_drill(
    *,
    batch_dir: str,
    restore_dir: str,
    passphrase: str | None,
    rto_ms: int,
    max_rpo_ms: int,
    allow_plaintext: bool = False,
    now_ms: int | None = None,
) -> dict[str, Any]:
    if rto_ms <= 0 or max_rpo_ms <= 0:
        raise RestoreDrillError("rto_ms and max_rpo_ms must be positive")
    files = _batch_files(batch_dir)
    observed_at_ms = int(time.time() * 1000) if now_ms is None else now_ms
    newest_source_ms = max(int(os.path.getmtime(path) * 1000) for path in files)
    source_age_ms = max(0, observed_at_ms - newest_source_ms)
    if source_age_ms > max_rpo_ms:
        raise RestoreDrillError(
            f"backup batch exceeds RPO: {source_age_ms}ms > {max_rpo_ms}ms")
    mode = _restore_mode(files)
    if mode == "ENCRYPTED":
        if not passphrase:
            raise RestoreDrillError("encrypted restore requires a passphrase")
        try:
            _check_passphrase_strength(passphrase)
        except Exception as exc:
            raise RestoreDrillError(str(exc)) from exc
    elif not allow_plaintext:
        raise RestoreDrillError("plaintext restore requires allow_plaintext=True")

    restored_names: list[str] = []
    expected_hashes: dict[str, str] = {}
    for source_path in files:
        source_name = os.path.basename(source_path)
        restored_name = source_name[:-4] if mode == "ENCRYPTED" else source_name
        if not restored_name or restored_name in expected_hashes:
            raise RestoreDrillError(f"duplicate or invalid restored filename: {restored_name!r}")
        restored_names.append(restored_name)

    target, stage = _prepare_target(restore_dir)
    started = time.perf_counter()
    try:
        if mode == "ENCRYPTED":
            def restore_encrypted(item: tuple[str, str]) -> tuple[str, str]:
                source_path, restored_name = item
                output_path = os.path.join(stage, restored_name)
                return restored_name, decrypt_file(
                    source_path,
                    output_path,
                    passphrase or "",
                    parallelism=MAX_PARALLEL_DECRYPT_CHUNKS,
                )

            workers = min(MAX_PARALLEL_RESTORE_FILES, len(files))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for restored_name, restored_sha256 in executor.map(
                    restore_encrypted,
                    _largest_first_restore_items(files, restored_names),
                ):
                    expected_hashes[restored_name] = restored_sha256
        else:
            for source_path, restored_name in zip(files, restored_names):
                output_path = os.path.join(stage, restored_name)
                shutil.copyfile(source_path, output_path)
                restored_sha256 = _sha256_file(output_path)
                # Plaintext opt-in has no AEAD, so compare source and
                # destination. Encrypted restores use the digest produced in
                # the authenticated decrypt stream above.
                if _sha256_file(source_path) != restored_sha256:
                    raise RestoreDrillError(
                        f"integrity mismatch after restore: {restored_name}"
                    )
                expected_hashes[restored_name] = restored_sha256

        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        if elapsed_ms > rto_ms:
            raise RestoreDrillError(f"restore drill exceeded RTO: {elapsed_ms}ms > {rto_ms}ms")
        if os.path.isdir(target):
            os.rmdir(target)
        os.replace(stage, target)
        stage = ""
        return {
            "schema_version": 1,
            "verdict": "PASS",
            "batch_dir": _evidence_path(batch_dir),
            "restore_dir": _evidence_path(target),
            "mode": mode,
            "file_count": len(restored_names),
            "restored_files": restored_names,
            "sha256": expected_hashes,
            "elapsed_ms": elapsed_ms,
            "rto_ms": rto_ms,
            "rto_met": True,
            "observed_at_ms": observed_at_ms,
            "newest_source_ms": newest_source_ms,
            "source_age_ms": source_age_ms,
            "max_rpo_ms": max_rpo_ms,
            "rpo_met": True,
            "integrity_verified": True,
        }
    finally:
        if stage:
            shutil.rmtree(stage, ignore_errors=True)


def _write_evidence(path: str, result: dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an isolated QUANTOS backup restore drill")
    parser.add_argument("batch_dir")
    parser.add_argument("--restore-dir", required=True)
    parser.add_argument("--rto-ms", type=int, default=300_000)
    parser.add_argument("--max-rpo-ms", type=int, default=86_400_000)
    parser.add_argument("--allow-plaintext", action="store_true")
    parser.add_argument(
        "--evidence",
        default=os.path.join("evidence", "generic_restore_drill_latest.json"),
    )
    args = parser.parse_args()
    result = run_restore_drill(
        batch_dir=args.batch_dir,
        restore_dir=args.restore_dir,
        passphrase=os.environ.get("QUANTOS_BACKUP_PASSPHRASE"),
        rto_ms=args.rto_ms,
        max_rpo_ms=args.max_rpo_ms,
        allow_plaintext=args.allow_plaintext,
    )
    _write_evidence(args.evidence, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
