"""Rank #19 — reproducibility gate: the committed evidence/*.json digests must match the live
load-bearing artifacts. When the gitignored data/cache/ artifact is absent (CI / fresh checkout)
the check SKIPS honestly (reported as skipped, never as passed); when it is present it VERIFIES
each digested number is within tolerance and the reconciliation identity still holds. This is how
the repo catches silent drift in the numbers it cites. Regenerate digests via
tools.build_evidence_digests after a driver refresh."""
import json
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_EVIDENCE = os.path.join(_ROOT, "evidence")


def _digests():
    if not os.path.isdir(_EVIDENCE):
        return []
    return [os.path.join(_EVIDENCE, f) for f in sorted(os.listdir(_EVIDENCE)) if f.endswith(".json")]


def test_evidence_dir_is_committed_and_nonempty():
    # the whole point of Rank #19 is that these digests live IN the repo (not just gitignored cache)
    assert _digests(), "no committed evidence/*.json digests found"


@pytest.mark.parametrize("digest_path", _digests(), ids=lambda p: os.path.basename(p))
def test_digest_matches_live_artifact_or_skips(digest_path):
    digest = json.load(open(digest_path, encoding="utf-8"))
    artifact = os.path.join(_ROOT, digest["artifact"])
    if not os.path.exists(artifact):
        pytest.skip(f"artifact {digest['artifact']} absent (CI/fresh checkout) — digest unverifiable here")

    if digest["kind"] == "dry_run_db":
        _verify_dry_run(digest, artifact)
    else:
        pytest.skip(f"no verifier for digest kind {digest['kind']!r}")


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
