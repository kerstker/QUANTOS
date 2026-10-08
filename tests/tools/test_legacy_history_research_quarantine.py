"""Fail-closed boundary for V1 historical profitability regenerators."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    LEGACY_INVALIDATION_CODE,
)
from tools import (
    crypto_alpha_research,
    crypto_daily_diagnostics,
    crypto_daily_research,
    crypto_gp_run,
    crypto_history_research,
    crypto_oos_validation,
    crypto_research_offline,
    crypto_research_run,
)


def _unexpected_side_effect(*_args, **_kwargs):
    pytest.fail("legacy entrypoint reached IO or registry mutation before quarantine")


@pytest.mark.parametrize(
    ("module", "first_effect"),
    (
        (crypto_history_research, "registry"),
        (crypto_alpha_research, "registry"),
        (crypto_daily_research, "clock"),
        (crypto_research_run, "clock"),
        (crypto_gp_run, "path_read"),
        (crypto_research_offline, "clock"),
        (crypto_oos_validation, "path_read"),
        (crypto_daily_diagnostics, "path_read"),
    ),
    ids=lambda value: getattr(value, "__name__", str(value)).removeprefix("tools."),
)
def test_public_main_quarantines_before_io_or_registry_mutation(
    module,
    first_effect: str,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    if first_effect == "registry":
        monkeypatch.setattr(
            module,
            "production_experiment_registry",
            _unexpected_side_effect,
        )
    elif first_effect == "clock":
        monkeypatch.setattr(module, "time", SimpleNamespace(time=_unexpected_side_effect))
    else:
        monkeypatch.setattr(
            module,
            "os",
            SimpleNamespace(
                path=SimpleNamespace(exists=_unexpected_side_effect),
            ),
        )

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        module.main()

    assert tuple(tmp_path.iterdir()) == ()


def test_offline_public_regenerate_cannot_bypass_quarantine_with_arguments(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        crypto_research_offline,
        "os",
        SimpleNamespace(
            path=SimpleNamespace(exists=_unexpected_side_effect),
        ),
    )

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        crypto_research_offline.regenerate(123456789)

    assert tuple(tmp_path.iterdir()) == ()
