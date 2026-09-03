import json
import os
import shutil

import pytest

from tools.live_backup import encrypt_file


def test_encrypted_restore_drill_roundtrip_writes_integrity_evidence(tmp_path):
    from tools.restore_drill import run_restore_drill

    source = tmp_path / "source"
    batch = tmp_path / "backup" / "20260710T120000Z"
    restore = tmp_path / "restore"
    source.mkdir()
    batch.mkdir(parents=True)
    files = {
        "live_track.db": b"ledger-bytes",
        "live_mandate.json": b'{"max_gross": 1000}',
    }
    for name, content in files.items():
        plain = source / name
        plain.write_bytes(content)
        encrypt_file(
            str(plain),
            str(batch / f"{name}.enc"),
            "restore-drill-passphrase",
            chunk_size=4,
        )

    result = run_restore_drill(
        batch_dir=str(batch),
        restore_dir=str(restore),
        passphrase="restore-drill-passphrase",
        rto_ms=30_000,
        max_rpo_ms=86_400_000,
    )

    assert result["verdict"] == "PASS"
    assert result["mode"] == "ENCRYPTED"
    assert result["file_count"] == 2
    assert result["integrity_verified"] is True
    assert result["rto_met"] is True
    assert result["rpo_met"] is True
    assert result["source_age_ms"] <= result["max_rpo_ms"]
    assert set(result["restored_files"]) == set(files)
    for name, content in files.items():
        assert (restore / name).read_bytes() == content
    json.dumps(result)


def test_encrypted_restore_reuses_authenticated_stream_digest(tmp_path, monkeypatch):
    import tools.restore_drill as restore_drill

    source = tmp_path / "source.bin"
    source.write_bytes(b"authority-bytes")
    batch = tmp_path / "batch"
    batch.mkdir()
    encrypt_file(
        str(source),
        str(batch / "source.bin.enc"),
        "restore-drill-passphrase",
    )
    calls = []
    real_hash = restore_drill._sha256_file

    def counted(path, **kwargs):
        calls.append(path)
        return real_hash(path, **kwargs)

    monkeypatch.setattr(restore_drill, "_sha256_file", counted)
    result = restore_drill.run_restore_drill(
        batch_dir=str(batch),
        restore_dir=str(tmp_path / "restore"),
        passphrase="restore-drill-passphrase",
        rto_ms=30_000,
        max_rpo_ms=86_400_000,
    )

    assert result["integrity_verified"] is True
    assert calls == []
    assert result["sha256"]["source.bin"] == real_hash(source)


def test_encrypted_zstd_restore_decompresses_and_hashes_plaintext(tmp_path, monkeypatch):
    import tools.restore_drill as restore_drill

    source = tmp_path / "authority.db.zst"
    source.write_bytes(b"compressed-authority-placeholder")
    batch = tmp_path / "batch"
    batch.mkdir()
    encrypt_file(
        str(source),
        str(batch / "authority.db.zst.enc"),
        "restore-drill-passphrase",
    )

    def fake_decompress(source_path, output_path):
        shutil.copyfile(source_path, output_path)
        return restore_drill._sha256_file(output_path)

    monkeypatch.setattr(restore_drill, "_decompress_zstd", fake_decompress)
    result = restore_drill.run_restore_drill(
        batch_dir=str(batch),
        restore_dir=str(tmp_path / "restore"),
        passphrase="restore-drill-passphrase",
        rto_ms=30_000,
        max_rpo_ms=86_400_000,
    )

    assert result["restored_files"] == ["authority.db"]
    assert result["sha256"]["authority.db"] == restore_drill._sha256_file(source)
    assert (tmp_path / "restore" / "authority.db").read_bytes() == source.read_bytes()
    assert not (tmp_path / "restore" / "authority.db.zst").exists()


def test_restore_rejects_identity_and_zstd_filename_collision(tmp_path):
    from tools.restore_drill import RestoreDrillError, run_restore_drill

    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "authority.db.enc").write_bytes(b"one")
    (batch / "authority.db.zst.enc").write_bytes(b"two")

    with pytest.raises(RestoreDrillError, match="duplicate"):
        run_restore_drill(
            batch_dir=str(batch),
            restore_dir=str(tmp_path / "restore"),
            passphrase="restore-drill-passphrase",
            rto_ms=30_000,
            max_rpo_ms=86_400_000,
        )


def test_encrypted_zstd_restore_fails_closed_without_codec(tmp_path, monkeypatch):
    import tools.restore_drill as restore_drill

    source = tmp_path / "authority.db.zst"
    source.write_bytes(b"compressed-authority-placeholder")
    batch = tmp_path / "batch"
    batch.mkdir()
    encrypt_file(
        str(source),
        str(batch / "authority.db.zst.enc"),
        "restore-drill-passphrase",
    )
    monkeypatch.setattr(restore_drill, "ZSTD_BINARY", str(tmp_path / "missing-zstd"))

    with pytest.raises(restore_drill.RestoreDrillError, match="unavailable"):
        restore_drill.run_restore_drill(
            batch_dir=str(batch),
            restore_dir=str(tmp_path / "restore"),
            passphrase="restore-drill-passphrase",
            rto_ms=30_000,
            max_rpo_ms=86_400_000,
        )
    assert not (tmp_path / "restore").exists()


def test_encrypted_restore_schedules_largest_files_first_without_reordering_evidence(
    tmp_path,
    monkeypatch,
):
    import tools.restore_drill as restore_drill

    batch = tmp_path / "batch"
    batch.mkdir()
    sizes = {
        "a-small.db.enc": 1,
        "b-large.db.enc": 30,
        "c-medium.db.enc": 10,
    }
    for name, size in sizes.items():
        (batch / name).write_bytes(b"x" * size)

    scheduled = []

    def fake_decrypt(source_path, output_path, passphrase, *, parallelism):
        scheduled.append(os.path.basename(source_path))
        return f"sha256-{os.path.basename(source_path)}"

    monkeypatch.setattr(restore_drill, "MAX_PARALLEL_RESTORE_FILES", 1)
    monkeypatch.setattr(restore_drill, "decrypt_file", fake_decrypt)
    result = restore_drill.run_restore_drill(
        batch_dir=str(batch),
        restore_dir=str(tmp_path / "restore"),
        passphrase="restore-drill-passphrase",
        rto_ms=30_000,
        max_rpo_ms=86_400_000,
    )

    assert scheduled == ["b-large.db.enc", "c-medium.db.enc", "a-small.db.enc"]
    assert result["restored_files"] == ["a-small.db", "b-large.db", "c-medium.db"]


def test_restore_drill_wrong_passphrase_fails_closed(tmp_path):
    from tools.live_backup import BackupDecryptionError
    from tools.restore_drill import run_restore_drill

    source = tmp_path / "live_track.db"
    source.write_bytes(b"ledger")
    batch = tmp_path / "batch"
    batch.mkdir()
    encrypt_file(str(source), str(batch / "live_track.db.enc"), "correct-passphrase")

    with pytest.raises(BackupDecryptionError):
        run_restore_drill(
            batch_dir=str(batch), restore_dir=str(tmp_path / "restore"),
            passphrase="wrong-passphrase", rto_ms=30_000, max_rpo_ms=86_400_000,
        )
    assert not (tmp_path / "restore" / "live_track.db").exists()


def test_plaintext_restore_requires_explicit_opt_in(tmp_path):
    from tools.restore_drill import RestoreDrillError, run_restore_drill

    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "live_track.db").write_bytes(b"ledger")

    with pytest.raises(RestoreDrillError, match="allow_plaintext"):
        run_restore_drill(
            batch_dir=str(batch), restore_dir=str(tmp_path / "restore"),
            passphrase=None, allow_plaintext=False, rto_ms=30_000, max_rpo_ms=86_400_000,
        )


def test_restore_drill_rejects_mixed_batch_and_nonempty_target(tmp_path):
    from tools.restore_drill import RestoreDrillError, run_restore_drill

    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "live_track.db").write_bytes(b"ledger")
    (batch / "live_audit.db.enc").write_bytes(b"not-needed-for-mixed-mode-check")
    with pytest.raises(RestoreDrillError, match="mixed"):
        run_restore_drill(
            batch_dir=str(batch), restore_dir=str(tmp_path / "restore"),
            passphrase="restore-drill-passphrase", allow_plaintext=True, rto_ms=30_000,
            max_rpo_ms=86_400_000,
        )

    clean_batch = tmp_path / "clean-batch"
    clean_batch.mkdir()
    (clean_batch / "live_track.db").write_bytes(b"ledger")
    target = tmp_path / "nonempty"
    target.mkdir()
    (target / "existing.txt").write_text("do not overwrite")
    with pytest.raises(RestoreDrillError, match="empty"):
        run_restore_drill(
            batch_dir=str(clean_batch), restore_dir=str(target),
            passphrase=None, allow_plaintext=True, rto_ms=30_000, max_rpo_ms=86_400_000,
        )


def test_restore_drill_fails_when_rto_is_exceeded(tmp_path, monkeypatch):
    import tools.restore_drill as restore_drill

    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "live_track.db").write_bytes(b"ledger")
    ticks = iter([10.0, 10.2])
    monkeypatch.setattr(restore_drill.time, "perf_counter", lambda: next(ticks))

    with pytest.raises(restore_drill.RestoreDrillError, match="RTO"):
        restore_drill.run_restore_drill(
            batch_dir=str(batch), restore_dir=str(tmp_path / "restore"),
            passphrase=None, allow_plaintext=True, rto_ms=100, max_rpo_ms=86_400_000,
        )
