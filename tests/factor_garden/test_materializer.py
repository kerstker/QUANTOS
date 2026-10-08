"""F3 — Factor Garden materializer: DSL expression -> PIT value store (schema v2),
kt := event_time ruling, idempotency, non-finite guard, per-factor isolation."""
import math

import pytest

from quantos.contracts.feature import FactorFamily, FeatureDefinition
from services.factor_garden import (
    CANONICAL_FACTORS,
    FactorGardenRegistry,
    build_factor_definition,
    materialize_factor,
    materialize_many,
    register_canonical_factors,
)
from services.factor_garden import materializer as mat
from services.features.value_store import SqliteFeatureValueStore
from services.research.alpha_expression import FieldPanel, deserialize, evaluate


def _panel():
    """2 instruments x 3 periods synthetic panel (close + global_ls)."""
    return FieldPanel(
        ("A", "B"),
        (100, 200, 300),
        {
            "close": [[10.0, 30.0], [20.0, 10.0], [30.0, 20.0]],
            "global_ls": [[1.1, 0.9], [1.3, 0.8], [0.7, 1.4]],
        },
    )


def _defn(expr=("cs_rank", ("field", "close")), name="rank_close"):
    return build_factor_definition(
        name=name,
        family=FactorFamily.PRICE_TREND,
        expression=expr,
        economic_hypothesis="synthetic test factor",
        required_datasets=("synthetic",),
        frequency="1d",
        universe="synthetic",
        creator_actor="quantos-research",
        created_at_ms=0,
    )


def _store(tmp_path):
    return SqliteFeatureValueStore(str(tmp_path / "fv.db"), create=True)


def test_materialize_writes_expected_rows_and_values(tmp_path):
    defn, panel = _defn(), _panel()
    out = materialize_factor(defn, panel, _store(tmp_path),
                             computed_at_ms=9000, dataset_version="ds-1")
    assert out == {"factor_id": "rank_close", "factor_version": "1.0.0",
                   "written": 6, "skipped_existing": 0, "skipped_non_finite": 0,
                   "instruments": 2, "periods": 3,
                   "dataset_version": "ds-1", "materializer_version": "1.0.0"}
    # values read back equal the direct evaluate() output, cell by cell
    matrix = evaluate(deserialize(defn.formula), panel)
    store = SqliteFeatureValueStore(str(tmp_path / "fv.db"))
    for ii, inst in enumerate(panel.instruments):
        obs = store.as_of_observations("rank_close", "1.0.0", inst, 10**15)
        assert [o["event_time"] for o in obs] == [100, 200, 300]
        assert [o["value"] for o in obs] == [matrix[ti][ii] for ti in range(3)]


def test_knowledge_time_equals_event_time_and_pit_read(tmp_path):
    store = _store(tmp_path)
    materialize_factor(_defn(), _panel(), store,
                       computed_at_ms=1, dataset_version="d")
    obs = store.as_of_observations("rank_close", "1.0.0", "A", 10**15)
    assert all(o["knowledge_time"] == o["event_time"] for o in obs)
    # PIT: values at t=200/300 are not knowable at t=150
    assert [o["event_time"] for o in store.as_of_observations("rank_close", "1.0.0", "A", 150)] == [100]
    assert len(store.as_of_observations("rank_close", "1.0.0", "A", 250)) == 2


def test_v2_metadata_columns_on_materialized_rows(tmp_path):
    store = _store(tmp_path)
    materialize_factor(_defn(), _panel(), store, computed_at_ms=4242,
                       dataset_version="ds-9", quality_verdict="STALE_SUSPECT")
    obs = store.as_of_observations("rank_close", "1.0.0", "B", 10**15)[0]
    assert obs["computed_at_ms"] == 4242
    assert obs["dataset_version"] == "ds-9"
    assert obs["materializer_version"] == "1.0.0"
    assert obs["quality_verdict"] == "STALE_SUSPECT"


def test_rerun_is_idempotent_zero_new_rows(tmp_path):
    store = _store(tmp_path)
    kw = dict(computed_at_ms=1, dataset_version="d")
    first = materialize_factor(_defn(), _panel(), store, **kw)
    second = materialize_factor(_defn(), _panel(), store, **kw)
    assert first["written"] == 6 and first["skipped_existing"] == 0
    assert second["written"] == 0 and second["skipped_existing"] == 6


def test_warmup_none_cells_not_materialized(tmp_path):
    defn = _defn(expr=("ts_mean", ("field", "close"), 2), name="sma2")
    out = materialize_factor(defn, _panel(), _store(tmp_path),
                             computed_at_ms=1, dataset_version="d")
    assert out["written"] == 4                     # row 0 is warmup None for both instruments


def test_non_finite_cells_skipped_and_counted(tmp_path, monkeypatch):
    panel = _panel()
    matrix = evaluate(deserialize(_defn().formula), panel)
    matrix[1][0] = math.nan
    matrix[2][1] = math.inf
    monkeypatch.setattr(mat, "evaluate", lambda expr, p: matrix)
    out = materialize_factor(_defn(), panel, _store(tmp_path),
                             computed_at_ms=1, dataset_version="d")
    assert out["written"] == 4 and out["skipped_non_finite"] == 2


def test_input_validation(tmp_path):
    defn, panel = _defn(), _panel()
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        materialize_factor(defn, panel, store, computed_at_ms=-1, dataset_version="d")
    with pytest.raises(ValueError):
        materialize_factor(defn, panel, store, computed_at_ms=True, dataset_version="d")
    with pytest.raises(ValueError):
        materialize_factor(defn, panel, store, computed_at_ms=1, dataset_version=" ")
    with pytest.raises(TypeError):
        materialize_factor(defn, panel, object(), computed_at_ms=1, dataset_version="d")
    with pytest.raises(TypeError):
        materialize_factor("not-a-defn", panel, store, computed_at_ms=1, dataset_version="d")


def test_materialize_many_isolates_single_factor_failure(tmp_path):
    good = _defn()
    bad = FeatureDefinition(feature_id="bad", name="bad",
                            formula="(nosuchop (field close))")   # evaluates -> ValueError
    out = materialize_many((good, bad), _panel(), _store(tmp_path),
                           computed_at_ms=1, dataset_version="d")
    assert out["rank_close"]["written"] == 6
    assert "error" in out["bad"] and "nosuchop" in out["bad"]["error"]


def test_registry_to_materializer_end_to_end(tmp_path):
    """register_canonical_factors -> defn from the registry -> materialize -> PIT read."""
    crowd = next(d for d in CANONICAL_FACTORS if d.name == "crowd_contra")
    with FactorGardenRegistry(str(tmp_path / "g.db"), create=True) as reg:
        register_canonical_factors(reg, recorded_at_ms=100)
        defn = reg.get("crowd_contra", "1.0.0").definition
    assert defn == crowd
    store = _store(tmp_path)
    out = materialize_factor(defn, _panel(), store,
                             computed_at_ms=5000, dataset_version="oi-2026-07-27")
    assert out["written"] == 6
    matrix = evaluate(deserialize(crowd.formula), _panel())
    obs = store.as_of_observations("crowd_contra", "1.0.0", "A", 10**15)
    assert [o["value"] for o in obs] == [matrix[ti][0] for ti in range(3)]
    assert obs[0]["dataset_version"] == "oi-2026-07-27"
    # and the legacy v1 read path sees the same values through the contract
    assert store.latest_as_of("crowd_contra", "1.0.0", "A", 300).value == matrix[2][0]
