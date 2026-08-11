from pathlib import Path


def test_workspace_cleaner_is_dry_run_by_default_and_preserves_state(tmp_path: Path):
    from scripts.clean_workspace import clean_workspace

    cache = tmp_path / "services" / "alpha" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.pyc").write_bytes(b"cache")
    (tmp_path / ".coverage").write_text("coverage", encoding="utf-8")

    key = tmp_path / "key"
    key.mkdir()
    (key / "secret.txt").write_text("do-not-touch", encoding="utf-8")
    protected_key_cache = key / "__pycache__"
    protected_key_cache.mkdir()
    (protected_key_cache / "secret.pyc").write_bytes(b"credential-adjacent")
    data = tmp_path / "data" / "cache"
    data.mkdir(parents=True)
    (data / "authority.db").write_bytes(b"authority")
    protected_data_cache = data / "__pycache__"
    protected_data_cache.mkdir()
    (protected_data_cache / "reader.pyc").write_bytes(b"authority-adjacent")

    report = clean_workspace(tmp_path, apply=False)

    assert report["mode"] == "DRY_RUN"
    assert cache.exists()
    assert (tmp_path / ".coverage").exists()
    assert (key / "secret.txt").exists()
    assert protected_key_cache.exists()
    assert (data / "authority.db").exists()
    assert protected_data_cache.exists()
    assert not any("key" in item["relative_path"].split("/") for item in report["items"])
    assert not any(item["relative_path"].startswith("data/cache") for item in report["items"])


def test_workspace_cleaner_removes_only_known_disposable_paths(tmp_path: Path):
    from scripts.clean_workspace import clean_workspace

    disposable = [
        tmp_path / ".pytest_cache",
        tmp_path / ".codex_tmp",
        tmp_path / "apps" / "research-console" / "dist",
        tmp_path / "tools" / "__pycache__",
    ]
    for path in disposable:
        path.mkdir(parents=True)
        (path / "artifact.bin").write_bytes(b"x")
    keep = tmp_path / "node_modules"
    keep.mkdir()
    (keep / "dependency.js").write_text("keep", encoding="utf-8")
    dependency_cache = keep / "package" / "__pycache__"
    dependency_cache.mkdir(parents=True)
    (dependency_cache / "module.pyc").write_bytes(b"dependency-cache")

    report = clean_workspace(tmp_path, apply=True)

    assert report["mode"] == "APPLY"
    assert report["removed_count"] == len(disposable)
    assert all(not path.exists() for path in disposable)
    assert (keep / "dependency.js").exists()
    assert dependency_cache.exists()
