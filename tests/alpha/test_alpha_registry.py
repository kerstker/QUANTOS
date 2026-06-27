"""The Alpha Registry is the versioned, lineage-aware entity store for AlphaAssets. It is DUMB STORAGE:
it records immutable asset snapshots (new facts -> new versions), answers provenance/lineage/status
queries, and keeps an append-only audit log. It deliberately drives NO lifecycle transitions and has no
privileged status mutator — status reflects what the lifecycle manager decided, so the registry can
never become an agent backdoor to APPROVED (containment preserved). Deterministic (caller-supplied ts)."""
import pytest

from quantos.contracts.alpha_asset import AlphaAsset, AlphaLineage
from quantos.contracts.research import AlphaState
from services.alpha.registry import AlphaAssetRegistry


def _asset(aid, version=1, generator="cs_genome", status=AlphaState.DRAFT, parents=()):
    return AlphaAsset(alpha_id=aid, version=version, generator=generator, status=status,
                      lineage=AlphaLineage(parent_ids=tuple(parents), mutation_op="mutate"))


def test_add_and_get_latest():
    r = AlphaAssetRegistry()
    r.add(_asset("a", 1), ts=1)
    assert r.get("a").version == 1 and r.latest("a").version == 1
    assert r.all_ids() == ("a",)


def test_versioning_is_immutable_and_monotonic():
    r = AlphaAssetRegistry()
    r.add(_asset("a", 1), ts=1)
    r.add(_asset("a", 2), ts=2)
    assert r.versions("a") == (1, 2)
    assert r.get("a", version=1).version == 1
    assert r.latest("a").version == 2
    assert tuple(x.version for x in r.history("a")) == (1, 2)
    with pytest.raises(ValueError):
        r.add(_asset("a", 2), ts=3)        # re-adding an existing version is forbidden (immutable)
    with pytest.raises(ValueError):
        r.add(_asset("a", 1), ts=3)        # non-incrementing version is forbidden (no downgrade)


def test_lineage_children_query():
    r = AlphaAssetRegistry()
    r.add(_asset("p", 1), ts=1)
    r.add(_asset("c1", 1, parents=("p",)), ts=2)
    r.add(_asset("c2", 1, parents=("p", "q")), ts=3)
    kids = {a.alpha_id for a in r.children_of("p")}
    assert kids == {"c1", "c2"}
    assert r.children_of("nobody") == ()


def test_filters_by_generator_and_status():
    r = AlphaAssetRegistry()
    r.add(_asset("a", 1, generator="gp", status=AlphaState.VALIDATED), ts=1)
    r.add(_asset("b", 1, generator="cs_genome", status=AlphaState.DRAFT), ts=2)
    assert {a.alpha_id for a in r.by_generator("gp")} == {"a"}
    assert {a.alpha_id for a in r.by_status(AlphaState.VALIDATED)} == {"a"}
    # filters operate on the LATEST version of each alpha
    r.add(_asset("a", 2, generator="gp", status=AlphaState.PAPER_TRADING), ts=3)
    assert {a.alpha_id for a in r.by_status(AlphaState.VALIDATED)} == set()
    assert {a.alpha_id for a in r.by_status(AlphaState.PAPER_TRADING)} == {"a"}


def test_audit_log_records_every_add_in_order():
    r = AlphaAssetRegistry()
    r.add(_asset("a", 1), ts=5)
    r.add(_asset("a", 2), ts=9)
    log = r.audit_log()
    assert [(e["alpha_id"], e["version"], e["ts"]) for e in log] == [("a", 1, 5), ("a", 2, 9)]
    assert "owner" in log[0] and "lifecycle_event_ids" in log[0]   # caller/lifecycle context recorded


def test_registry_rejects_forged_approved_status():
    # CONTAINMENT (review P0): a directly-constructed APPROVED asset with no lifecycle proof must be
    # rejected, so the registry can never be a backdoor around the human-only promotion chokepoint.
    r = AlphaAssetRegistry()
    forged = AlphaAsset(alpha_id="rogue", status=AlphaState.APPROVED)   # no lifecycle_event_ids
    with pytest.raises(ValueError):
        r.add(forged, ts=1)
    # an APPROVED asset that carries lifecycle-transition proof IS allowed (rebuild from persisted state)
    legit = AlphaAsset(alpha_id="ok", status=AlphaState.APPROVED, lifecycle_event_ids=("t1", "t2"))
    r.add(legit, ts=2)
    assert r.latest("ok").status == AlphaState.APPROVED


def test_children_of_spans_all_versions():
    # lineage is historical fact: a child that named a parent in ANY version is its descendant even if
    # a later version drops the link (review P1).
    r = AlphaAssetRegistry()
    r.add(_asset("p", 1), ts=1)
    r.add(_asset("c", 1, parents=("p",)), ts=2)
    r.add(AlphaAsset(alpha_id="c", version=2, lineage=AlphaLineage(parent_ids=())), ts=3)  # drops parent
    assert {a.alpha_id for a in r.children_of("p")} == {"c"}


def test_get_missing_is_none_not_raise():
    r = AlphaAssetRegistry()
    assert r.get("nope") is None and r.latest("nope") is None
    assert r.versions("nope") == () and r.history("nope") == ()


def test_no_rng_or_wallclock_import():
    import inspect
    import services.alpha.registry as reg
    src = inspect.getsource(reg)
    assert "import random" not in src and "import time" not in src
    assert "datetime" not in src and "Random" not in src
