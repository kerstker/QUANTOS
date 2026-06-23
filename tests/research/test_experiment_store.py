"""Audit roadmap — sqlite experiment store: dedup'd trials, write-once evaluations,
fail-closed on an uninitialized/corrupt store (never reads as empty)."""
import sqlite3
import pytest
from services.research.experiment_registry.store import (
    SqliteExperimentStore, ExperimentStoreError, ImmutableArtifactError,
)


def _store():
    return SqliteExperimentStore(":memory:", create=True)


def test_trials_dedup_by_hash_and_count():
    s = _store()
    s.add_trials(["h1", "h2"], first_seen_ts=1)
    s.add_trials(["h2", "h3"], first_seen_ts=2)   # h2 already present
    assert s.trial_count() == 3
    assert sorted(s.all_trial_hashes()) == ["h1", "h2", "h3"]


def test_evaluation_is_write_once():
    s = _store()
    s.insert_evaluation(run_id="r1", alpha_id="a1", dataset_version="v1",
                        recorded_ts=1, verdict="PASS", content_hash="c1",
                        evaluation_json='{"x":1}')
    s.insert_evaluation(run_id="r1", alpha_id="a1", dataset_version="v1",
                        recorded_ts=1, verdict="PASS", content_hash="c1",
                        evaluation_json='{"x":1}')          # identical -> no-op
    assert s.get_evaluation("r1")["content_hash"] == "c1"
    with pytest.raises(ImmutableArtifactError):
        s.insert_evaluation(run_id="r1", alpha_id="a1", dataset_version="v1",
                            recorded_ts=2, verdict="FAIL", content_hash="c2",
                            evaluation_json='{"x":2}')       # conflicting -> raise


def test_uninitialized_store_fails_closed(tmp_path):
    # a path with no schema (create=False) must RAISE on read, never read as empty
    p = str(tmp_path / "empty.db")
    sqlite3.connect(p).close()                               # a file, but no tables
    s = SqliteExperimentStore(p, create=False)
    with pytest.raises(ExperimentStoreError):
        s.trial_count()


def test_created_empty_store_reads_zero_legitimately():
    s = _store()                                             # create=True -> schema exists
    assert s.trial_count() == 0 and s.all_alphas() == []
