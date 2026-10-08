from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DAY_MS = 86_400_000

# Every direct alpha-expression panel reader of the two Binance 1d OHLCV
# stores.  crypto_funding.db is populated by binance_klines_fetch just like
# crypto_binance.db; the different filename does not change its clock.
BINANCE_DAILY_PANEL_TOOLS = frozenset(
    {
        "tools/capacity_research.py",
        "tools/carry_binance_validate.py",
        "tools/crypto_alpha_search.py",
        "tools/crypto_binance_research.py",
        "tools/crypto_carry_capacity.py",
        "tools/crypto_carry_strategy.py",
        "tools/crypto_carry_stress.py",
        "tools/crypto_carry_walkforward.py",
        "tools/crypto_factory_run.py",
        "tools/crypto_funding_research.py",
        "tools/crypto_funding_verify.py",
        "tools/crypto_oi_research.py",
        "tools/crypto_pipeline_run.py",
        "tools/dry_run_carry.py",
        "tools/dry_run_carry_authoritative_multi.py",
        "tools/dry_run_carry_multi.py",
    }
)

# These use the same expression API, but their stores are CryptoCompare daily
# datasets.  A Binance-specific cache-epoch rule must not silently rewrite
# their source semantics.  Equity panels use ic_panel_registry rather than
# build_field_panel and are likewise intentionally outside this list.
NON_BINANCE_PANEL_TOOLS = frozenset(
    {
        "tools/crypto_alpha_research.py",
        "tools/crypto_smallcap_research.py",
    }
)

BINANCE_DAILY_BULK_AVAILABLE_TIME_TOOLS = frozenset(
    {
        "tools/allocator_research.py",
        "tools/candidate_portfolio_evidence.py",
        "tools/cftc_positioning_research.py",
        "tools/common_risk_research.py",
        "tools/crypto_robust_optimization.py",
        "tools/exit_policy_lab.py",
        "tools/ic_panel_registry.py",
        "tools/stablecoin_liquidity_research.py",
        "tools/vol_research.py",
    }
)


def _panel_calls(relative_path: str) -> tuple[ast.Call, ...]:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    return tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "build_field_panel"
    )


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _named_calls(relative_path: str, function_name: str) -> tuple[ast.Call, ...]:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    return tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == function_name)
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == function_name
            )
        )
    )


def test_build_field_panel_tool_inventory_is_fully_classified() -> None:
    actual = {
        str(path.relative_to(ROOT))
        for path in (ROOT / "tools").glob("*.py")
        if _panel_calls(str(path.relative_to(ROOT)))
    }

    assert actual == BINANCE_DAILY_PANEL_TOOLS | NON_BINANCE_PANEL_TOOLS


def test_every_binance_daily_panel_cli_blocks_custom_same_close_labels() -> None:
    for relative_path in BINANCE_DAILY_PANEL_TOOLS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "quarantine_legacy_backtest" in source, relative_path


@pytest.mark.parametrize("relative_path", sorted(BINANCE_DAILY_PANEL_TOOLS))
def test_binance_daily_panel_readers_reject_legacy_open_time_rows(
    relative_path: str,
) -> None:
    calls = _panel_calls(relative_path)
    assert calls
    for call in calls:
        minimum_lag = _keyword(call, "minimum_knowledge_lag")
        assert isinstance(minimum_lag, ast.Constant)
        assert minimum_lag.value == DAY_MS
        assert _keyword(call, "decision_times") is not None


@pytest.mark.parametrize("relative_path", sorted(NON_BINANCE_PANEL_TOOLS))
def test_binance_cache_epoch_is_not_applied_to_other_providers(
    relative_path: str,
) -> None:
    calls = _panel_calls(relative_path)
    assert calls
    assert all(_keyword(call, "minimum_knowledge_lag") is None for call in calls)


def test_binance_bulk_available_time_tool_inventory_is_fully_classified() -> None:
    actual = {
        str(path.relative_to(ROOT))
        for path in (ROOT / "tools").glob("*.py")
        if _named_calls(
            str(path.relative_to(ROOT)),
            "fetch_bars_for_event_decisions",
        )
    }

    assert actual == BINANCE_DAILY_BULK_AVAILABLE_TIME_TOOLS


@pytest.mark.parametrize(
    "relative_path",
    sorted(BINANCE_DAILY_BULK_AVAILABLE_TIME_TOOLS),
)
def test_binance_bulk_readers_declare_one_day_decision_and_cache_epoch(
    relative_path: str,
) -> None:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    literal_lag_keywords = {
        keyword.arg
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call))
        for keyword in call.keywords
        if keyword.arg in {"decision_lag", "minimum_knowledge_lag"}
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == DAY_MS
    }

    assert literal_lag_keywords == {"decision_lag", "minimum_knowledge_lag"}


def test_mixed_provider_bulk_pit_fallbacks_are_not_binance_price_reads() -> None:
    allowed_fallbacks = {
        "tools/allocator_research.py": "dvol_store.fetch_bars_pit",
        "tools/cross_venue_research.py": "store.fetch_bars_pit",
        "tools/exit_policy_lab.py": "store.fetch_bars_pit",
        "tools/ic_panel_registry.py": "store.fetch_bars_pit",
        "tools/vol_research.py": "store.fetch_bars_pit",
    }
    actual = {}
    for path in (ROOT / "tools").glob("*.py"):
        relative = str(path.relative_to(ROOT))
        if "crypto_funding.db" not in path.read_text(encoding="utf-8"):
            continue
        calls = _named_calls(relative, "fetch_bars_pit")
        if calls:
            actual[relative] = {ast.unparse(call.func) for call in calls}

    assert set(actual) == set(allowed_fallbacks)
    for relative, expected in allowed_fallbacks.items():
        assert actual[relative] == {expected}
