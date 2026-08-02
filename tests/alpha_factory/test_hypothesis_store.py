"""Append-only SqliteHypothesisStore (727 §8.1 pre-registration ledger)."""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from services.alpha_factory.hypothesis import create_hypothesis
from services.alpha_factory.hypothesis_store import (
    HypothesisConflictError,
    HypothesisNotFoundError,
    HypothesisStoreError,
    SqliteHypothesisStore,
)


def _hypothesis(mechanism_suffix: str = ""):
    return create_hypothesis(
        economic_mechanism=(
            "Crowded retail long positioning mean-reverts once funding flips sign"
            + mechanism_suffix
        ),
        expected_direction="short crowded / long uncrowded cross-section",
        expected_horizon="7d",
        required_factors=("global_ls",),
        market_scope="binance usdt perp majors",
        expected_cost_sensitivity="edge survives 10-20bps taker",
        capacity_assumptions="participation capped at 10% ADV",
        falsification_criteria=("IC t-stat < 2.0 over 100 weekly periods",),
        creator_model="human",
        created_at_ms=1000,
    )


def test_register_get_all_count_round_trip() -> None:
    store = SqliteHypothesisStore(":memory:", create=True)
    first, second = _hypothesis(), _hypothesis(" v2")
    store.register(first, registered_at_ms=100)
    store.register(second, registered_at_ms=200)

    assert store.count() == 2
    assert store.has(first.hypothesis_id) and store.has(second.hypothesis_id)
    assert store.get(first.hypothesis_id) == first
    # registration order is deterministic
    assert [h.hypothesis_id for h in store.all()] == [
        first.hypothesis_id,
        second.hypothesis_id,
    ]
    assert store.chain_valid() is True


def test_register_is_idempotent_on_identical_content() -> None:
    store = SqliteHypothesisStore(":memory:", create=True)
    hypothesis = _hypothesis()
    store.register(hypothesis, registered_at_ms=100)
    again = store.register(hypothesis, registered_at_ms=999)

    assert again == hypothesis
    assert store.count() == 1
    assert store.chain_valid() is True


def test_same_id_different_content_raises_conflict() -> None:
    store = SqliteHypothesisStore(":memory:", create=True)
    hypothesis = _hypothesis()
    store.register(hypothesis, registered_at_ms=100)
    forged = dataclasses.replace(
        _hypothesis(" v2"), hypothesis_id=hypothesis.hypothesis_id
    )

    with pytest.raises(HypothesisConflictError):
        store.register(forged, registered_at_ms=101)


def test_missing_hypothesis_raises_not_found() -> None:
    store = SqliteHypothesisStore(":memory:", create=True)
    assert store.has("hyp_absent") is False
    with pytest.raises(HypothesisNotFoundError):
        store.get("hyp_absent")


def test_append_only_triggers_block_update_and_delete() -> None:
    store = SqliteHypothesisStore(":memory:", create=True)
    hypothesis = _hypothesis()
    store.register(hypothesis, registered_at_ms=100)

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute(
            "UPDATE hypotheses SET content_hash='x' WHERE hypothesis_id=?",
            (hypothesis.hypothesis_id,),
        )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute(
            "DELETE FROM hypotheses WHERE hypothesis_id=?",
            (hypothesis.hypothesis_id,),
        )


def test_tampered_payload_breaks_the_chain() -> None:
    store = SqliteHypothesisStore(":memory:", create=True)
    hypothesis = _hypothesis()
    store.register(hypothesis, registered_at_ms=100)
    assert store.chain_valid() is True

    store._conn.execute("DROP TRIGGER hypotheses_no_update")
    store._conn.execute(
        "UPDATE hypotheses SET registered_at_ms=4242 WHERE hypothesis_id=?",
        (hypothesis.hypothesis_id,),
    )
    store._conn.commit()
    assert store.chain_valid() is False


def test_persistence_across_reopen(tmp_path) -> None:
    path = str(tmp_path / "hypotheses.db")
    hypothesis = _hypothesis()
    first = SqliteHypothesisStore(path, create=True)
    first.register(hypothesis, registered_at_ms=100)
    first.close()

    reopened = SqliteHypothesisStore(path, create=False)
    assert reopened.count() == 1
    assert reopened.get(hypothesis.hypothesis_id) == hypothesis
    assert reopened.chain_valid() is True
    reopened.close()


def test_open_without_create_on_missing_path_fails(tmp_path) -> None:
    with pytest.raises(HypothesisStoreError, match="unavailable"):
        SqliteHypothesisStore(str(tmp_path / "absent.db"), create=False)
    with pytest.raises(HypothesisStoreError, match="unavailable"):
        SqliteHypothesisStore(":memory:", create=False)


def test_open_verifies_append_only_guards(tmp_path) -> None:
    path = str(tmp_path / "unguarded.db")
    store = SqliteHypothesisStore(path, create=True)
    store._conn.execute("DROP TRIGGER hypotheses_no_delete")
    store._conn.commit()
    store.close()

    with pytest.raises(HypothesisStoreError, match="append-only guard"):
        SqliteHypothesisStore(path, create=False)
