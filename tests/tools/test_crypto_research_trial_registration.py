from __future__ import annotations

import ast
from pathlib import Path

import pytest

from quantos.contracts.data import Bar
from services.alpha.panel_backtest import PanelPeriod
from services.data.persistent_store import SqlitePITStore
from services.intelligence import registered_candidate_batch as batch_module
from services.research.alpha_expression import FieldPanel
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from tools import crypto_alpha_research, crypto_binance_research, crypto_history_research
from tools.search_trial_registry import field_panel_dataset_version


ROOT = Path(__file__).resolve().parents[2]
TOOLS = (
    "tools/crypto_alpha_research.py",
    "tools/crypto_binance_research.py",
    "tools/crypto_history_research.py",
)


def _registry() -> ExperimentRegistry:
    return ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))


def _panel(*, close_shift: float = 0.0) -> FieldPanel:
    event_times = tuple(range(1, 33))
    close = [
        [100.0 + index + close_shift, 100.0 - index / 4.0]
        for index, _event_time in enumerate(event_times)
    ]
    volume = [[1_000.0 + index, 2_000.0 - index] for index in range(32)]
    return FieldPanel(("A", "B"), event_times, {"close": close, "volume": volume})


def _periods(count: int = 25) -> tuple[PanelPeriod, ...]:
    return tuple(
        PanelPeriod(
            instruments=("A", "B"),
            signals=(0.25, -0.25),
            forward_returns=(0.01, -0.01),
            event_time=index + 1,
        )
        for index in range(count)
    )


def _assert_agent_sees_complete_pool(monkeypatch, registry, expected_count: int) -> None:
    def assert_pre_registered(self, candidates, **_kwargs):
        materialized = tuple(candidates)
        assert len(materialized) == expected_count
        assert registry._store.trial_count() == expected_count
        attempts = registry._store._conn.execute(
            "SELECT search_id,status FROM search_attempt_events ORDER BY event_seq"
        ).fetchall()
        assert [row["status"] for row in attempts] == ["STARTED"]
        assert len(registry.search_candidates(attempts[0]["search_id"])) == expected_count
        return {
            "report": {
                definition.alpha_id: {"state": None}
                for definition, _periods in materialized
            },
            "human_review_queue": [],
        }

    monkeypatch.setattr(
        batch_module.ResearchAgentLoop,
        "run",
        assert_pre_registered,
    )


@pytest.mark.parametrize("relative", TOOLS)
def test_research_tools_use_only_registered_candidate_composition(relative: str) -> None:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "ResearchAgentLoop" not in imported_names
    assert "AlphaCatalog" not in imported_names
    assert "run_registered_candidate_batch" in imported_names
    assert "production_experiment_registry" in imported_names

    main_function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    main_calls = {
        node.func.id: node.lineno
        for node in ast.walk(main_function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert main_calls["production_experiment_registry"] < main_calls["_main"]


@pytest.mark.parametrize(
    "relative,ordered_calls",
    (
        (
            "tools/crypto_alpha_research.py",
            ("_run_registered_panel", "run_panel_backtest", "rows.sort"),
        ),
        (
            "tools/crypto_binance_research.py",
            (
                "_run_registered_panel",
                "run_panel_backtest",
                "walk_forward_selection_oos",
                "rows_out.sort",
            ),
        ),
        (
            "tools/crypto_history_research.py",
            (
                "_run_registered_history_pool",
                "_net",
                "walk_forward_selection_oos",
                "verdicts.sort",
            ),
        ),
    ),
)
def test_registration_precedes_scoring_oos_and_ranking(
    relative: str,
    ordered_calls: tuple[str, ...],
) -> None:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    main_function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_main"
    )

    def call_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return None

    first_lines = {}
    for node in ast.walk(main_function):
        if isinstance(node, ast.Call):
            name = call_name(node.func)
            if name is not None:
                first_lines.setdefault(name, node.lineno)

    assert [first_lines[name] for name in ordered_calls] == sorted(
        first_lines[name] for name in ordered_calls
    )


@pytest.mark.parametrize(
    "module,source",
    (
        (crypto_alpha_research, "tools.crypto_alpha_research"),
        (crypto_binance_research, "tools.crypto_binance_research"),
    ),
)
def test_panel_tools_register_complete_pool_before_agent_score(
    monkeypatch,
    module,
    source: str,
) -> None:
    registry = _registry()
    panel = _panel()
    alphas = {
        "momentum": ("cs_rank", ("ts_delta", ("field", "close"), 2)),
        "reversal": ("cs_rank", ("neg", ("ts_delta", ("field", "close"), 2))),
    }
    monkeypatch.setattr(module, "ALPHAS", alphas)
    _assert_agent_sees_complete_pool(monkeypatch, registry, expected_count=2)

    candidate_rows, result, dataset_version = module._run_registered_panel(
        panel,
        registry=registry,
        recorded_at=100,
    )

    assert len(candidate_rows) == 2
    assert result["registered_candidate_count"] == 2
    assert dataset_version == field_panel_dataset_version(
        panel,
        source_versions={"forward_horizon": module.FWD},
    )
    attempt = registry._store._conn.execute(
        "SELECT source,dataset_version,status FROM search_attempt_events "
        "ORDER BY event_seq DESC LIMIT 1"
    ).fetchone()
    assert tuple(attempt) == (source, dataset_version, "COMPLETED")


def test_panel_dataset_version_changes_with_consumed_matrix() -> None:
    first = crypto_alpha_research._panel_dataset_version(_panel())
    second = crypto_alpha_research._panel_dataset_version(_panel(close_shift=0.5))

    assert first != second


def _history_store() -> SqlitePITStore:
    store = SqlitePITStore(":memory:", create=True)
    for instrument, offset in (("A", 0.0), ("B", 10.0)):
        for event_time in (1, 2, 3):
            close = 100.0 + offset + event_time
            store.add(
                instrument,
                Bar(
                    instrument_id=instrument,
                    event_time=event_time,
                    knowledge_time=event_time,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1_000.0 + event_time,
                ),
            )
    return store


def test_history_tool_registers_complete_pool_before_agent_score(
    monkeypatch,
) -> None:
    registry = _registry()
    store = _history_store()
    pool = (("dist_from_high", 3), ("momentum", 2))
    monkeypatch.setattr(crypto_history_research, "POOL", pool)
    monkeypatch.setattr(
        crypto_history_research,
        "_periods_with_times",
        lambda *_args: (_periods(), tuple(range(1, 26))),
    )
    monkeypatch.setattr(
        crypto_history_research,
        "signal_leakage_ok",
        lambda *_args: True,
    )
    _assert_agent_sees_complete_pool(monkeypatch, registry, expected_count=2)

    result = crypto_history_research._run_registered_history_pool(
        store,
        ("A", "B"),
        (1, 2, 3),
        registry=registry,
        recorded_at=200,
    )

    assert result["batch_result"]["registered_candidate_count"] == 2
    assert set(result["pool_periods"]) == set(pool)
    assert set(result["evidence"]) == {"h_dist_from_high_3", "h_momentum_2"}
    attempt = registry._store._conn.execute(
        "SELECT source,dataset_version,status FROM search_attempt_events "
        "ORDER BY event_seq DESC LIMIT 1"
    ).fetchone()
    assert tuple(attempt) == (
        "tools.crypto_history_research",
        result["dataset_version"],
        "COMPLETED",
    )


def test_history_dataset_version_changes_with_consumed_bars() -> None:
    store = _history_store()
    first = crypto_history_research._history_dataset_version(
        store,
        ("A", "B"),
        (1, 2, 3),
    )
    store.add(
        "A",
        Bar(
            instrument_id="A",
            event_time=3,
            knowledge_time=4,
            open=110.0,
            high=110.0,
            low=110.0,
            close=110.0,
            volume=2_000.0,
        ),
    )
    second = crypto_history_research._history_dataset_version(
        store,
        ("A", "B"),
        (1, 2, 3),
    )

    assert first != second
