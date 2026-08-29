"""Authority-boundary tests for historical selection-bearing CLI drivers.

The V1 execution pipeline remains available only to pure synthetic diagnostic
functions.  Filesystem-backed CLI entrypoints must not regenerate/reuse scores,
register trials, select candidates, publish profitability artifacts, or advance
paper state while its timing and perpetual-ledger semantics are invalidated.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    LEGACY_INVALIDATION_CODE,
)


ROOT = Path(__file__).resolve().parents[2]

LEGACY_MULTI_PERIOD_SELECTION_DRIVERS = frozenset(
    {
        "alpha_family_discovery",
        "ashare_alpha_research",
        "crowd_contra_variants",
        "crypto_robust_optimization",
        "dry_run_btc_spot_multi",
        "dry_run_carry_authoritative_multi",
        "dry_run_carry_multi",
        "dry_run_combo_multi",
        "dry_run_combo_riskparity_multi",
        "dry_run_crowd_contra_multi",
        "dry_run_crowd_contra_signal_adapter_multi",
        "dry_run_top_contra_multi",
        "dry_run_trend_multi",
        "mean_reversion_cluster",
        "promotion_gate_crowd_contra",
    }
)

LEGACY_MULTI_PERIOD_DIAGNOSTICS = frozenset(
    {
        "top_contra_dry_run_diagnostic",
    }
)

# The exit lab no longer imports a production runner.  Its synthetic algorithm
# is reachable only through an explicitly injected private diagnostic seam.
INJECTED_LEGACY_MULTI_PERIOD_DIAGNOSTICS = frozenset({"exit_policy_lab"})

INDIRECT_LEGACY_SELECTION_DRIVERS = frozenset(
    {
        "cftc_positioning_research",
        "cross_venue_research",
        "hyperliquid_funding_research",
        "positioning_fast_composite_evaluation",
    }
)

LEGACY_PROFITABILITY_REPUBLISHERS = frozenset(
    {
        "alpha_paper_cycle",
        "all_alpha_realistic_paper",
        "alpha_candidate_admission",
        "allocator_research",
        "alpha_lifecycle_cockpit",
        "alpha_maintenance_sweep",
        "ashare_register_alpha",
        "ashare_paper_track",
        "binance_usdc_alpha_retest",
        "binance_usdt_continuous_paper",
        "build_evidence_digests",
        "common_risk_research",
        "crypto_paper_track",
        "fundraise_deck_charts",
        "investor_report_charts",
        "three_alpha_paper_flow",
    }
)

# These drivers consume a daily Binance panel whose decision is at t+1d but
# then score/register/write evidence from close[t] -> close[t+1] without an
# explicit post-decision executable event.  The cache clock is now truthful;
# the custom return converters remain invalid and therefore fail closed.
DELAYED_DAILY_CUSTOM_LABEL_DRIVERS = frozenset(
    {
        "capacity_research",
        "carry_binance_validate",
        "combo_book_research",
        "crypto_alpha_search",
        "crypto_binance_research",
        "crypto_carry_capacity",
        "crypto_carry_strategy",
        "crypto_carry_stress",
        "crypto_carry_voltarget",
        "crypto_carry_walkforward",
        "crypto_combo_book",
        "crypto_factory_intraday",
        "crypto_factory_loop",
        "crypto_factory_orthogonal",
        "crypto_factory_overfit_ab",
        "crypto_factory_run",
        "crypto_funding_interactions",
        "crypto_funding_research",
        "crypto_funding_verify",
        "crypto_multidim_retest",
        "crypto_oi_research",
        "crypto_oi_survivorship",
        "crypto_oi_top_verify",
        "crypto_oi_verify",
        "crypto_pipeline_run",
        "crypto_positioning_divergence",
        "dry_run_carry",
        "dry_run_carry_authoritative_multi",
        "dry_run_carry_multi",
        "ic_panel_research",
        "scenario_tail_research",
        "vol_research",
        "wide_factor_gauntlet",
    }
)

LEGACY_PANEL_SCORE_DRIVERS = frozenset(
    {
        "crypto_daily_composite",
        "crypto_daily_regime",
        "crypto_daily_verify",
        "crypto_smallcap_research",
        "funding_settlement_evaluation",
        "funding_weekly_carry_evaluation",
        "fx_gauntlet",
        "fx_research",
        "ml_stack_research",
    }
)

# Complete AST-discovered population of tools that call the shared legacy
# registered-candidate batch boundary.  Keep this explicit so a new caller
# cannot inherit the quarantine only after loading data/opening a registry.
REGISTERED_CANDIDATE_BATCH_DRIVERS = frozenset(
    {
        "cross_venue_positioning_evaluation",
        "crypto_alpha_research",
        "crypto_alpha_search",
        "crypto_binance_research",
        "crypto_funding_research",
        "crypto_history_research",
        "crypto_smallcap_research",
        "crypto_volume_research",
        "defillama_tvl_evaluation",
        "funding_settlement_evaluation",
        "funding_weekly_carry_evaluation",
        "fx_research",
        "macro_regime_switch_evaluation",
        "onchain_alpha_evaluation",
        "paper_alpha_evaluation",
        "positioning_composite_evaluation",
        "positioning_divergence_evaluation",
        "register_alpha_catalog",
        "volume_acceleration_evaluation",
    }
)


def _imports_legacy_multi_period(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "services.execution.dry_run_pipeline"
        and any(alias.name == "run_multi_period_dry_run" for alias in node.names)
        for node in ast.walk(tree)
    )


def _calls_registered_candidate_batch(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "services.intelligence.registered_candidate_batch"
        for alias in node.names
        if alias.name == "run_registered_candidate_batch"
    }
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in imported_names
        for node in ast.walk(tree)
    )


def _first_executable_statement(
    tree: ast.Module, function_name: str
) -> ast.stmt:
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    assert len(functions) == 1, function_name
    body = list(functions[0].body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    assert body, function_name
    return body[0]


def test_every_legacy_multi_period_tool_is_explicitly_classified() -> None:
    discovered = {
        path.stem
        for path in (ROOT / "tools").glob("*.py")
        if _imports_legacy_multi_period(path)
    }
    assert discovered == (
        LEGACY_MULTI_PERIOD_SELECTION_DRIVERS | LEGACY_MULTI_PERIOD_DIAGNOSTICS
    )


def test_every_registered_candidate_batch_tool_is_explicitly_classified() -> None:
    discovered = {
        path.stem
        for path in (ROOT / "tools").glob("*.py")
        if _calls_registered_candidate_batch(path)
    }
    assert discovered == REGISTERED_CANDIDATE_BATCH_DRIVERS


def test_production_code_cannot_import_private_legacy_diagnostic_runners() -> None:
    forbidden_by_module = {
        "services.execution.dry_run_pipeline": {
            "_run_dry_run_pipeline_legacy_diagnostic",
            "_run_multi_period_dry_run_legacy_diagnostic",
        },
        "services.intelligence.registered_candidate_batch": {
            "_run_registered_candidate_batch_legacy_diagnostic",
        },
    }
    violations: list[str] = []
    for root_name in ("services", "tools", "live"):
        for path in (ROOT / root_name).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                forbidden = forbidden_by_module.get(node.module or "")
                if forbidden is None:
                    continue
                provider = ROOT / Path(*(node.module or "").split(".")).with_suffix(".py")
                if path == provider:
                    continue
                imported = forbidden & {alias.name for alias in node.names}
                if imported:
                    violations.append(f"{path.relative_to(ROOT)}:{sorted(imported)}")
    assert violations == []


@pytest.mark.parametrize(
    ("module_name", "function_name"),
    (
        ("exit_policy_lab", "run_book"),
        ("paper_order_preview", "build_alpha_preview"),
        ("cross_venue_research", "register_family_trials"),
        ("cftc_positioning_research", "register_family_trials"),
        ("crowd_contra_variants", "register_family_trials"),
        ("hyperliquid_funding_research", "register_family_trials"),
        ("mean_reversion_cluster", "register_family_trials"),
        ("promotion_gate_crowd_contra", "register_grid_trials"),
    ),
)
def test_public_tool_api_first_action_is_unconditional_authority_quarantine(
    module_name: str,
    function_name: str,
) -> None:
    path = ROOT / "tools" / f"{module_name}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    first = _first_executable_statement(tree, function_name)
    assert isinstance(first, ast.Expr)
    assert isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name)
    assert first.value.func.id == "quarantine_legacy_backtest"


@pytest.mark.parametrize(
    ("module_name", "function_name"),
    (
        ("cftc_positioning_research", "register_family_trials"),
        ("cross_venue_research", "register_family_trials"),
        ("crowd_contra_variants", "register_family_trials"),
        ("hyperliquid_funding_research", "register_family_trials"),
        ("mean_reversion_cluster", "register_family_trials"),
        ("promotion_gate_crowd_contra", "register_grid_trials"),
    ),
)
def test_public_registration_api_blocks_without_arguments(
    module_name: str,
    function_name: str,
) -> None:
    module = importlib.import_module(f"tools.{module_name}")
    entry = getattr(module, function_name)

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        entry()


def test_explicit_diagnostics_are_quarantined() -> None:
    for module_name in (
        LEGACY_MULTI_PERIOD_DIAGNOSTICS
        | INJECTED_LEGACY_MULTI_PERIOD_DIAGNOSTICS
    ):
        source = (ROOT / "tools" / f"{module_name}.py").read_text(encoding="utf-8")
        assert "quarantine_legacy_backtest" in source, module_name


def test_every_classified_selection_driver_has_an_authority_quarantine() -> None:
    selection_drivers = (
        LEGACY_MULTI_PERIOD_SELECTION_DRIVERS
        | INDIRECT_LEGACY_SELECTION_DRIVERS
        | LEGACY_PROFITABILITY_REPUBLISHERS
        | DELAYED_DAILY_CUSTOM_LABEL_DRIVERS
        | LEGACY_PANEL_SCORE_DRIVERS
        | REGISTERED_CANDIDATE_BATCH_DRIVERS
        | {"dry_run_carry"}
    )
    for module_name in selection_drivers:
        source = (ROOT / "tools" / f"{module_name}.py").read_text(encoding="utf-8")
        assert "quarantine_legacy_backtest" in source, module_name


@pytest.mark.parametrize(
    ("module_name", "entry_name", "args"),
    (
        ("all_alpha_realistic_paper", "main", ()),
        ("alpha_candidate_admission", "run", ()),
        ("allocator_research", "main", ()),
        ("alpha_lifecycle_cockpit", "main", ([],)),
        ("alpha_maintenance_sweep", "run_sweep", ()),
        ("ashare_paper_track", "main", ()),
        ("binance_usdc_alpha_retest", "run_retest", ()),
        ("binance_usdt_continuous_paper", "main", ()),
        ("common_risk_research", "main", ()),
        ("crypto_paper_track", "main", ()),
        ("three_alpha_paper_flow", "run_flow", ()),
    ),
)
def test_legacy_profitability_republisher_blocks_before_io(
    module_name: str,
    entry_name: str,
    args: tuple[object, ...],
) -> None:
    module = importlib.import_module(f"tools.{module_name}")
    entry = getattr(module, entry_name)
    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        entry(*args)


@pytest.mark.parametrize("module_name", sorted(LEGACY_PANEL_SCORE_DRIVERS))
def test_residual_panel_score_cli_blocks_before_io(module_name: str) -> None:
    module = importlib.import_module(f"tools.{module_name}")
    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        module.main()


@pytest.mark.parametrize(
    "module_name",
    ("fundraise_deck_charts", "investor_report_charts"),
)
def test_import_time_legacy_chart_is_quarantined_before_database_access(
    module_name: str,
) -> None:
    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        importlib.import_module(f"tools.{module_name}")


def test_crypto_paper_track_blocks_before_deleting_shared_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("tools.crypto_paper_track")
    ledger = tmp_path / "paper_track.db"
    original = b"existing-shared-forward-ledger"
    ledger.write_bytes(original)

    def fail_if_loaded():
        pytest.fail("legacy factor series must not be loaded before quarantine")

    monkeypatch.setattr(module, "_DB", str(ledger))
    monkeypatch.setattr(module, "_factor_series", fail_if_loaded)
    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        module.main()

    assert ledger.read_bytes() == original


@pytest.mark.parametrize(
    ("module_name", "args"),
    (
        ("mean_reversion_cluster", ()),
        ("cross_venue_research", ()),
        ("crypto_alpha_search", ()),
        ("crypto_binance_research", ()),
        ("crypto_factory_run", ()),
        ("crypto_funding_research", ()),
        ("promotion_gate_crowd_contra", ()),
        ("hyperliquid_funding_research", ([],)),
    ),
)
def test_registry_backed_cli_blocks_before_opening_registry(
    module_name: str,
    args: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(f"tools.{module_name}")

    def fail_if_registry_opened(*_args, **_kwargs):
        pytest.fail("authority quarantine must run before registry acquisition")

    monkeypatch.setattr(module, "production_experiment_registry", fail_if_registry_opened)
    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        module.main(*args)


@pytest.mark.parametrize(
    ("module_name", "entry_name", "args"),
    (
        ("cftc_positioning_research", "run_research", ()),
        ("cftc_positioning_research", "main", ([],)),
        ("positioning_divergence_evaluation", "run", ()),
        ("positioning_divergence_evaluation", "main", ([],)),
        ("volume_acceleration_evaluation", "run", ()),
        ("volume_acceleration_evaluation", "main", ([],)),
        ("macro_regime_switch_evaluation", "run", ()),
        ("macro_regime_switch_evaluation", "main", ([],)),
        ("defillama_tvl_evaluation", "run", ()),
        ("defillama_tvl_evaluation", "main", ([],)),
        ("crypto_volume_research", "_main", (object(),)),
        ("crypto_volume_research", "main", ()),
        ("positioning_composite_evaluation", "run", ()),
        ("positioning_composite_evaluation", "main", ([],)),
        ("positioning_fast_composite_evaluation", "run", ()),
        ("positioning_fast_composite_evaluation", "main", ([],)),
        ("onchain_alpha_evaluation", "run", ()),
        ("onchain_alpha_evaluation", "main", ([],)),
        ("cross_venue_positioning_evaluation", "run", ()),
        ("cross_venue_positioning_evaluation", "main", ([],)),
    ),
)
def test_new_registered_batch_drivers_block_before_registry_data_or_output(
    module_name: str,
    entry_name: str,
    args: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(f"tools.{module_name}")

    def fail_if_touched(*_args, **_kwargs):
        pytest.fail("quarantine must precede registry, data, and output collaborators")

    for collaborator in (
        "production_experiment_registry",
        "atomic_write_json",
        "build_panel",
        "load_panel",
        "fetch_volume_rows",
    ):
        if hasattr(module, collaborator):
            monkeypatch.setattr(module, collaborator, fail_if_touched)
    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        getattr(module, entry_name)(*args)


@pytest.mark.parametrize(
    ("module_name", "entry_name", "args", "kwargs"),
    (
        ("mean_reversion_cluster", "_main", (object(),), {}),
        ("cross_venue_research", "_main", (object(),), {}),
        ("crypto_alpha_search", "_main", (object(),), {}),
        ("crypto_binance_research", "_main", (object(),), {}),
        ("crypto_funding_research", "_main", (object(),), {}),
        (
            "hyperliquid_funding_research",
            "run_research",
            (),
            {"registry": object()},
        ),
        ("promotion_gate_crowd_contra", "_main", (object(),), {}),
        ("dry_run_carry", "main", (), {}),
        ("dry_run_carry_multi", "main", (), {}),
        ("dry_run_trend_multi", "main", (), {}),
        ("dry_run_combo_multi", "main", (), {}),
        ("dry_run_combo_riskparity_multi", "main", (), {}),
        ("dry_run_crowd_contra_signal_adapter_multi", "main", (), {}),
        ("dry_run_btc_spot_multi", "main", (), {}),
        ("ashare_alpha_research", "main", (), {}),
    ),
)
def test_selection_driver_blocks_before_loading_data_or_opening_stores(
    module_name: str,
    entry_name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> None:
    module = importlib.import_module(f"tools.{module_name}")
    entry = getattr(module, entry_name)
    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        entry(*args, **kwargs)


@pytest.mark.parametrize(
    "module_name",
    sorted(DELAYED_DAILY_CUSTOM_LABEL_DRIVERS),
)
def test_delayed_daily_custom_label_cli_is_quarantined(module_name: str) -> None:
    module = importlib.import_module(f"tools.{module_name}")
    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        module.main()


def test_alpha_family_discovery_blocks_before_creating_any_registry(tmp_path: Path) -> None:
    module = importlib.import_module("tools.alpha_family_discovery")
    family_db = tmp_path / "family.db"
    experiment_db = tmp_path / "experiment.db"
    dry_run_db = tmp_path / "dry-run.db"

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        module.discover(
            panel=object(),
            family_db=str(family_db),
            experiment_db=str(experiment_db),
            dry_run_db=str(dry_run_db),
            recorded_at=1,
        )

    assert not family_db.exists()
    assert not experiment_db.exists()
    assert not dry_run_db.exists()


def test_robust_optimization_blocks_before_trial_or_report_persistence(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("tools.crypto_robust_optimization")
    report = tmp_path / "report.json"
    cells = tmp_path / "cells.jsonl"
    registry = tmp_path / "registry.db"

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        module.run(
            report_path=str(report),
            cells_path=str(cells),
            registry_path=str(registry),
            cpu_replay=False,
        )

    assert not report.exists()
    assert not cells.exists()
    assert not registry.exists()


def test_destructive_single_period_carry_blocks_before_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("tools.dry_run_carry")
    canonical_store = tmp_path / "dry_run.db"
    canonical_store.write_text("retained", encoding="utf-8")
    monkeypatch.setattr(module, "_OUT", str(canonical_store))

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        module.main()

    assert canonical_store.read_text(encoding="utf-8") == "retained"
