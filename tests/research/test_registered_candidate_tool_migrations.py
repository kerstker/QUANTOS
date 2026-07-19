from __future__ import annotations

from pathlib import Path

import pytest

from quantos.contracts.alpha import AlphaDefinition
from services.intelligence import registered_candidate_batch as batch_module
from services.research import alpha_expression as ax
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from tools import crypto_smallcap_research, fx_research


def _registry() -> ExperimentRegistry:
    return ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))


def _candidates(count: int):
    return tuple(
        (
            AlphaDefinition(
                alpha_id=f"tool-candidate-{index}",
                name=f"Tool candidate {index}",
                factor_ids=("close",),
                params={"family": "tool-migration", "index": str(index)},
            ),
            (),
        )
        for index in range(count)
    )


@pytest.mark.parametrize(
    ("module", "search_id"),
    (
        (crypto_smallcap_research, "smallcap-complete-pool"),
        (fx_research, "fx-complete-pool"),
    ),
)
def test_migrated_tool_registers_complete_pool_before_agent(
    module,
    search_id,
    monkeypatch,
) -> None:
    registry = _registry()
    candidates = _candidates(18)

    def assert_complete_pool(self, received, **_kwargs):
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

    monkeypatch.setattr(
        batch_module.ResearchAgentLoop,
        "run",
        assert_complete_pool,
    )
    result = module._run_registered_pool(
        candidates,
        registry=registry,
        search_id=search_id,
        dataset_version="dataset-v1",
        recorded_at=100,
        evidence={},
    )

    assert result["registered_candidate_count"] == len(candidates)
    assert [
        row["status"] for row in registry.search_attempt_events(search_id)
    ] == ["STARTED", "COMPLETED"]


@pytest.mark.parametrize(
    "path",
    (
        Path("tools/crypto_smallcap_research.py"),
        Path("tools/fx_research.py"),
    ),
)
def test_migrated_tool_has_no_direct_agent_or_catalog_construction(path) -> None:
    source = path.read_text(encoding="utf-8")
    assert "services.intelligence.agent_loop" not in source
    assert "from services.alpha.catalog import AlphaCatalog" not in source
    assert "ResearchAgentLoop(" not in source
    assert "AlphaCatalog(" not in source


def test_smallcap_registers_before_backtest_rank_and_selection() -> None:
    source = Path("tools/crypto_smallcap_research.py").read_text(encoding="utf-8")
    body = source[source.index("def _main(") :]
    registered_at = body.index("batch_result = _run_registered_pool(")
    assert registered_at < body.index("run_panel_backtest(")
    assert registered_at < body.index("walk_forward_selection_oos(")
    assert registered_at < body.index("rows_out.sort(")


def test_fx_registers_before_backtest_rank_and_selection() -> None:
    source = Path("tools/fx_research.py").read_text(encoding="utf-8")
    body = source[source.index("def _run_research(") :]
    registered_at = body.index("batch_result = _run_registered_pool(")
    assert registered_at < body.index("_net(periods)")
    assert registered_at < body.index("walk_forward_selection_oos(")
    assert registered_at < body.index("full_sample_best(")


def _panel(close_shift: float = 0.0) -> ax.FieldPanel:
    return ax.FieldPanel(
        ["A", "B"],
        [1, 2, 3],
        {
            "close": [
                [100.0 + close_shift, 101.0],
                [102.0, 103.0],
                [104.0, 105.0],
            ],
            "volume": [[10.0, 11.0], [12.0, 13.0], [14.0, 15.0]],
        },
    )


def test_smallcap_dataset_version_binds_panel_and_source_inputs() -> None:
    first = crypto_smallcap_research._smallcap_dataset_version(
        _panel(),
        n_uni=175,
        drop_top=25,
    )
    assert first == crypto_smallcap_research._smallcap_dataset_version(
        _panel(),
        n_uni=175,
        drop_top=25,
    )
    assert first != crypto_smallcap_research._smallcap_dataset_version(
        _panel(close_shift=1.0),
        n_uni=175,
        drop_top=25,
    )
    assert first != crypto_smallcap_research._smallcap_dataset_version(
        _panel(),
        n_uni=200,
        drop_top=25,
    )


class _VersionedStore:
    def __init__(self, version: str) -> None:
        self.version = version

    def dataset_version(self, instruments, event_times) -> str:
        assert tuple(instruments) == ("EUR", "JPY")
        assert tuple(event_times) == (1, 2, 3)
        return self.version


def test_fx_dataset_version_binds_bar_content_version_and_search_contract() -> None:
    first = fx_research._fx_dataset_version(
        _VersionedStore("bars-v1"),
        ("EUR", "JPY"),
        (1, 2, 3),
    )
    assert first == fx_research._fx_dataset_version(
        _VersionedStore("bars-v1"),
        ("EUR", "JPY"),
        (1, 2, 3),
    )
    assert first != fx_research._fx_dataset_version(
        _VersionedStore("bars-v2"),
        ("EUR", "JPY"),
        (1, 2, 3),
    )
