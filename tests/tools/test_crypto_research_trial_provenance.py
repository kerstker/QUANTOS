from __future__ import annotations

from contextlib import contextmanager
import inspect

import pytest

from quantos.contracts.alpha import AlphaDefinition
from services.intelligence import registered_candidate_batch as batch_module
from services.research import alpha_expression as ax
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from tools import crypto_funding_research, crypto_volume_research


def _registry() -> ExperimentRegistry:
    return ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))


def _candidates():
    return tuple(
        (
            AlphaDefinition(
                alpha_id=f"candidate-{index}",
                name=f"Candidate {index}",
                factor_ids=("close",),
                params={"index": str(index)},
            ),
            (),
        )
        for index in range(3)
    )


@pytest.mark.parametrize(
    "module",
    (crypto_funding_research, crypto_volume_research),
)
def test_legacy_entrypoint_has_no_direct_catalog_or_agent_loop(module) -> None:
    source = inspect.getsource(module)
    assert "services.alpha.catalog" not in source
    assert "services.intelligence.agent_loop" not in source
    assert "AlphaCatalog" not in source
    assert "ResearchAgentLoop" not in source


@pytest.mark.parametrize(
    ("module", "search_id", "search_kind", "search_source"),
    (
        (
            crypto_funding_research,
            "funding-search",
            "CRYPTO_FUNDING_RESEARCH",
            "tools.crypto_funding_research",
        ),
        (
            crypto_volume_research,
            "volume-search",
            "CRYPTO_VOLUME_RESEARCH",
            "tools.crypto_volume_research",
        ),
    ),
)
def test_complete_candidate_pool_is_registered_before_scoring(
    module,
    search_id,
    search_kind,
    search_source,
    monkeypatch,
) -> None:
    registry = _registry()
    candidates = _candidates()

    def assert_registered(self, received, **_kwargs):
        assert tuple(received) == candidates
        assert registry._store.trial_count() == len(candidates)
        assert len(registry.search_candidates(search_id)) == len(candidates)
        return {
            "report": {
                definition.alpha_id: {"state": None}
                for definition, _periods in candidates
            },
            "human_review_queue": [],
        }

    monkeypatch.setattr(batch_module.ResearchAgentLoop, "run", assert_registered)
    module._run_registered_pool(
        candidates,
        registry=registry,
        search_id=search_id,
        dataset_version="dataset-v1",
        recorded_at=100,
    )

    events = registry.search_attempt_events(search_id)
    assert [row["status"] for row in events] == ["STARTED", "COMPLETED"]
    assert {row["search_kind"] for row in events} == {search_kind}
    assert {row["source"] for row in events} == {search_source}


def test_candidate_selection_and_ranking_follow_durable_registration() -> None:
    funding_source = inspect.getsource(crypto_funding_research._main)
    funding_registration = funding_source.index("batch_result = _run_registered_pool")
    for marker in (
        "run_panel_backtest",
        "walk_forward_selection_oos",
        "full_sample_best",
        "rows_out.sort",
    ):
        assert funding_registration < funding_source.index(marker)

    volume_source = inspect.getsource(crypto_volume_research._main)
    volume_registration = volume_source.index("out = _run_registered_pool")
    assert volume_registration < volume_source.index("rows_rep.sort")


def test_funding_dataset_version_binds_panel_and_both_sources() -> None:
    panel = ax.FieldPanel(
        ("BTC", "ETH"),
        (1, 2),
        {
            "close": [[100.0, 200.0], [101.0, 201.0]],
            "volume": [[10.0, 20.0], [11.0, 21.0]],
            "funding": [[0.001, -0.001], [0.002, -0.002]],
        },
    )
    baseline = crypto_funding_research._funding_dataset_version(
        panel,
        bar_source_version="bars-v1",
        funding_source_version="funding-v1",
    )
    changed_panel = ax.FieldPanel(
        panel.instruments,
        panel.event_times,
        {**panel.matrices, "funding": [[0.001, -0.001], [0.003, -0.002]]},
    )
    assert baseline != crypto_funding_research._funding_dataset_version(
        changed_panel,
        bar_source_version="bars-v1",
        funding_source_version="funding-v1",
    )
    assert baseline != crypto_funding_research._funding_dataset_version(
        panel,
        bar_source_version="bars-v2",
        funding_source_version="funding-v1",
    )
    assert baseline != crypto_funding_research._funding_dataset_version(
        panel,
        bar_source_version="bars-v1",
        funding_source_version="funding-v2",
    )


def test_volume_dataset_version_binds_source_instruments_and_times() -> None:
    baseline = crypto_volume_research._volume_dataset_version(
        bar_source_version="bars-v1",
        instruments=("BTC", "ETH"),
        event_times=(1, 2),
    )
    assert baseline != crypto_volume_research._volume_dataset_version(
        bar_source_version="bars-v2",
        instruments=("BTC", "ETH"),
        event_times=(1, 2),
    )
    assert baseline != crypto_volume_research._volume_dataset_version(
        bar_source_version="bars-v1",
        instruments=("BTC", "SOL"),
        event_times=(1, 2),
    )
    assert baseline != crypto_volume_research._volume_dataset_version(
        bar_source_version="bars-v1",
        instruments=("BTC", "ETH"),
        event_times=(1, 3),
    )


@pytest.mark.parametrize(
    "module",
    (crypto_funding_research, crypto_volume_research),
)
def test_main_injects_production_registry_without_running_research(
    module,
    monkeypatch,
) -> None:
    registry = object()
    observed = []

    @contextmanager
    def registry_context():
        yield registry

    monkeypatch.setattr(module, "production_experiment_registry", registry_context)
    monkeypatch.setattr(module, "_main", observed.append)

    module.main()

    assert observed == [registry]
