"""Rank #19 — reproducibility gate: the committed evidence/*.json digests must match the live
load-bearing artifacts. When the gitignored data/cache/ artifact is absent (CI / fresh checkout)
the check SKIPS honestly (reported as skipped, never as passed); when it is present it VERIFIES
each digested number is within tolerance and the reconciliation identity still holds. This is how
the repo catches silent drift in the numbers it cites. Regenerate digests via
tools.build_evidence_digests after a driver refresh."""
import json
import os
from pathlib import Path
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_EVIDENCE = os.path.join(_ROOT, "evidence")


def _digests():
    if not os.path.isdir(_EVIDENCE):
        return []
    digests = []
    for filename in sorted(os.listdir(_EVIDENCE)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(_EVIDENCE, filename)
        document = json.load(open(path, encoding="utf-8-sig"))
        if isinstance(document, dict) and {"artifact", "kind"} <= document.keys():
            digests.append(path)
    return digests


def test_readiness_artifacts_bind_one_commit_and_validation_run():
    from scripts.evidence_binding import validation_binding

    binding = validation_binding(generated_at=123, run_id="a" * 64, source_commit="b" * 40)
    payloads = [
        {"commit": "b" * 40, "checks_manifest_sha256": "legacy-run", "validation": binding},
        {"commit": "b" * 40, "validation": binding},
        {"validation": binding},
    ]
    from scripts.evidence_binding import validation_binding_from_payload

    bindings = [validation_binding_from_payload(payload) for payload in payloads]
    assert len({binding["source_commit"] for binding in bindings}) == 1
    assert len({binding["run_id"] for binding in bindings}) == 1


def test_evidence_dir_is_committed_and_nonempty():
    # the whole point of Rank #19 is that these digests live IN the repo (not just gitignored cache)
    assert _digests(), "no committed evidence/*.json digests found"


def test_non_digest_evidence_documents_are_not_discovered():
    assert os.path.join(_EVIDENCE, "top_tier_assessment.json") not in _digests()


@pytest.mark.parametrize("digest_path", _digests(), ids=lambda p: os.path.basename(p))
def test_digest_matches_live_artifact_or_skips(digest_path):
    digest = json.load(open(digest_path, encoding="utf-8"))
    artifact = os.path.join(_ROOT, digest["artifact"])
    if not os.path.exists(artifact):
        pytest.skip(f"artifact {digest['artifact']} absent (CI/fresh checkout) — digest unverifiable here")

    if digest["kind"] == "dry_run_db":
        _verify_dry_run(digest, artifact)
    elif digest["kind"] == "json_numbers":
        _verify_json_numbers(digest, artifact)
    else:
        pytest.skip(f"no verifier for digest kind {digest['kind']!r}")


def _verify_json_numbers(digest, artifact):
    import json as _json
    from tools.build_evidence_digests import EXTRACTORS
    snap = _json.load(open(artifact, encoding="utf-8"))
    live = EXTRACTORS[digest["extractor"]](snap)          # re-extract the SAME way the digest was built
    for key, spec in digest["numbers"].items():
        assert key in live, f"digested number {key!r} missing from the live artifact (drift)"
        want, got = float(spec["value"]), float(live[key])
        tol = max(float(spec["abs_tol"]), float(spec["rel_tol"]) * abs(want))
        assert abs(got - want) <= tol + 1e-9, (
            f"{key} drifted: live {got} vs digest {want} (tol {tol}) — "
            f"regenerate evidence via tools.build_evidence_digests if the change is intended")




def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _temp_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    (repo / "source.txt").write_text("source\n", encoding="utf-8")
    _git(repo, "add", "source.txt")
    _git(repo, "commit", "-qm", "source")
    return repo, _git(repo, "rev-parse", "HEAD")


def _commit_artifact(repo: Path, name: str = "evidence/validation_report.json") -> str:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"artifact": name}) + "\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", "artifact")
    return _git(repo, "rev-parse", "HEAD")


def test_source_anchor_resolver_skips_only_consecutive_allowlisted_artifacts(tmp_path):
    from scripts.evidence_binding import resolve_canonical_source_anchor

    repo, source = _temp_repo(tmp_path)
    assert resolve_canonical_source_anchor(repo) == source
    _commit_artifact(repo)
    _commit_artifact(repo, "evidence/sbom.cdx.json")
    assert resolve_canonical_source_anchor(repo) == source


def test_source_anchor_resolver_rejects_dirty_non_allowlisted_paths(tmp_path):
    from scripts.evidence_binding import resolve_canonical_source_anchor

    repo, _ = _temp_repo(tmp_path)
    (repo / "source.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="allowlist"):
        resolve_canonical_source_anchor(repo)
    (repo / "source.txt").write_text("source\n", encoding="utf-8")
    (repo / "evidence").mkdir()
    (repo / "evidence/validation_report.json").write_text("{}\n", encoding="utf-8")
    assert resolve_canonical_source_anchor(repo)


@pytest.mark.parametrize("status", ["rename", "delete", "copy", "merge"])
def test_source_anchor_resolver_never_skips_non_add_modify_commits(tmp_path, status):
    from scripts.evidence_binding import resolve_canonical_source_anchor

    repo, source = _temp_repo(tmp_path)
    if status == "rename":
        _git(repo, "mv", "source.txt", "renamed.txt")
        _git(repo, "commit", "-qm", status)
    elif status == "delete":
        _git(repo, "rm", "source.txt")
        _git(repo, "commit", "-qm", status)
    elif status == "copy":
        (repo / "copy.txt").write_text("source\n", encoding="utf-8")
        _git(repo, "add", "copy.txt")
        _git(repo, "commit", "-qm", status)
    else:
        branch = _git(repo, "branch", "--show-current")
        _git(repo, "checkout", "-qb", "side")
        (repo / "side.txt").write_text("side\n", encoding="utf-8")
        _git(repo, "add", "side.txt")
        _git(repo, "commit", "-qm", "side")
        _git(repo, "checkout", "-q", branch)
        (repo / "main.txt").write_text("main\n", encoding="utf-8")
        _git(repo, "add", "main.txt")
        _git(repo, "commit", "-qm", "main")
        _git(repo, "merge", "--no-ff", "side", "-m", "merge", "-q")
    assert resolve_canonical_source_anchor(repo) != source


def test_source_anchor_resolver_stops_at_mixed_commit(tmp_path):
    from scripts.evidence_binding import resolve_canonical_source_anchor

    repo, source = _temp_repo(tmp_path)
    (repo / "evidence").mkdir()
    (repo / "evidence/validation_report.json").write_text("{}\n", encoding="utf-8")
    (repo / "new-source.txt").write_text("new\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "mixed")
    assert resolve_canonical_source_anchor(repo) != source


def test_assert_current_binding_uses_resolved_anchor_and_exact_shape(tmp_path):
    from scripts.evidence_binding import assert_current_binding, resolve_canonical_source_anchor

    repo, source = _temp_repo(tmp_path)
    artifact = _commit_artifact(repo)
    anchor = resolve_canonical_source_anchor(repo)
    payload = {"validation": {"run_id": "a" * 64, "source_commit": anchor, "generated_at": 1, "current": True}}
    assert anchor == source
    assert_current_binding(payload, head=artifact, repo_root=repo)
    payload["validation"]["source_commit"] = artifact
    with pytest.raises(ValueError):
        assert_current_binding(payload, head=artifact, repo_root=repo)


def _verify_dry_run(digest, artifact):
    from services.execution.dry_run_store import DryRunStore
    live = {}
    for r in DryRunStore(artifact).all_runs():
        pnl = r["pnl"] if isinstance(r["pnl"], dict) else json.loads(r["pnl"])
        live[r["strategy_id"]] = pnl
    tol = digest["tolerances"]
    for strategy, expected in digest["books"].items():
        assert strategy in live, f"digested book {strategy!r} missing from the live artifact (drift)"
        pnl = live[strategy]
        for metric, want in expected.items():
            got = float(pnl[metric])
            assert abs(got - want) <= tol[metric] + 1e-9, (
                f"{strategy}.{metric} drifted: live {got} vs digest {want} (tol {tol[metric]}) — "
                f"regenerate evidence via tools.build_evidence_digests if the change is intended")
        # the reconciliation identity must still hold for every book
        assert abs(float(pnl["residual"])) <= digest["residual_max"], (
            f"{strategy} reconciliation residual {pnl['residual']} exceeds {digest['residual_max']}")


def test_binding_helper_rejects_malformed_values_and_environment(monkeypatch):
    from scripts.evidence_binding import assert_current_binding, validation_binding, validation_binding_from_payload

    with pytest.raises(ValueError):
        validation_binding(generated_at=False)
    with pytest.raises(ValueError):
        validation_binding(generated_at=True)

    source = "b" * 40
    run = "a" * 64
    valid = {"validation": {"run_id": run, "source_commit": source, "generated_at": 1, "current": True}}
    for key, value in [("QUANTOS_VALIDATION_SOURCE_COMMIT", ""), ("QUANTOS_VALIDATION_RUN_ID", "bad")]:
        monkeypatch.setenv(key, value)
        with pytest.raises(ValueError):
            validation_binding(generated_at=1, source_commit=source, run_id=run)
        monkeypatch.delenv(key)
    for payload in [
        {"validation": {"run_id": "bad", "source_commit": source, "generated_at": 1, "current": True}},
        {"validation": {"run_id": run, "source_commit": "bad", "generated_at": 1, "current": True}},
        {"validation": {"run_id": run, "source_commit": source, "generated_at": True, "current": True}},
        {"validation": {"run_id": run, "source_commit": source, "generated_at": 1, "current": 1}},
    ]:
        with pytest.raises(ValueError):
            validation_binding_from_payload(payload)
    monkeypatch.setenv("QUANTOS_VALIDATION_SOURCE_COMMIT", "c" * 40)
    with pytest.raises(ValueError):
        validation_binding(generated_at=1, source_commit=source, run_id=run)
    with pytest.raises(ValueError):
        assert_current_binding(valid, head=source)
    monkeypatch.setenv("QUANTOS_VALIDATION_SOURCE_COMMIT", source)
    monkeypatch.setenv("QUANTOS_VALIDATION_RUN_ID", "c" * 64)
    with pytest.raises(ValueError):
        assert_current_binding(valid, head=source)
