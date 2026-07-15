"""Regression lock for the 2026-07-09 contamination bug: tools/crypto_research_offline.py's
demo-panel regenerator used to share data/cache/experiment_registry.db — the SAME file the real
AlphaCatalog persists to and services/promotion_api reads for actual human promotion
decisions — and its full-rebuild behavior silently wrote 60 unrelated demo/UI-fixture trials into
it, contaminating global_trial_count()/catalog_var_sr() for the real registered alphas (confirmed:
this flipped crowd_contra's recorded DSR from an honest 1.0000 to a contaminated 0.6087). Fixed
forward by giving the demo regenerator its own dedicated file; this test pins that separation so
the bug class cannot silently reappear if crypto_research_offline.py is ever edited again."""
import re
from pathlib import Path

_PRODUCTION_DB = "experiment_registry.db"


def test_demo_regenerator_does_not_reference_the_production_registry_path():
    src = Path("tools/crypto_research_offline.py").read_text(encoding="utf-8")
    # every literal db-path construction in this file must resolve to something OTHER than the
    # exact production filename (a prefixed/demo-suffixed name is fine and expected).
    paths = re.findall(r'os\.path\.join\([^)]*\)', src)
    assert paths, "expected at least one os.path.join(...) db-path construction in this file"
    for p in paths:
        if "cache" in p:
            assert _PRODUCTION_DB not in p or "demo" in p.lower(), (
                f"tools/crypto_research_offline.py constructs a path that could resolve to the "
                f"production experiment_registry.db: {p!r} — this is exactly the 2026-07-09 "
                f"contamination bug class; give it a dedicated demo-suffixed filename instead.")


_ALLOWED_PRODUCTION_PATH_REFS = frozenset({
    "search_trial_registry.py",               # fail-closed shared composition for selection-bearing tools
    "purge_demo_registry_contamination.py",   # the 2026-07-09 one-off contamination-purge tool;
                                               # its default target IS the production path by design
})


def test_catalog_registration_uses_the_shared_fail_closed_registry_helper():
    source = Path("tools/register_alpha_catalog.py").read_text(encoding="utf-8")
    assert "production_experiment_registry" in source
    assert "run_registered_candidate_batch" in source
    assert "SqliteExperimentStore" not in source


def test_only_known_tools_write_the_production_registry_path():
    """The production data/cache/experiment_registry.db path (bare, unsuffixed) must appear as a
    literal path construction ONLY in tools/*.py drivers explicitly reviewed to need it. Any other
    tools/*.py referencing it (outside a comment/docstring) is a new instance of the same
    contamination risk and should be caught here before it ships."""
    writers = []
    for path in Path("tools").glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r'os\.path\.join\(\s*["\']data["\']\s*,\s*["\']cache["\']\s*,\s*'
                        r'["\']experiment_registry\.db["\']\s*\)', line):
                writers.append(path.name)
    assert set(writers) <= _ALLOWED_PRODUCTION_PATH_REFS, (
        f"a tools/*.py driver not on the reviewed allowlist references the production "
        f"experiment_registry.db path — this is exactly the 2026-07-09 contamination bug class; "
        f"found: {set(writers) - _ALLOWED_PRODUCTION_PATH_REFS}")
    assert set(writers) == _ALLOWED_PRODUCTION_PATH_REFS, (
        f"expected all of {_ALLOWED_PRODUCTION_PATH_REFS} to reference the production path; "
        f"found only: {writers} — update the allowlist if a driver was intentionally removed")
