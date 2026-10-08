from __future__ import annotations

import pytest

from services.execution.backtest_authority import BacktestIntegrityBlocked
from tools import dry_run_carry_multi


def test_default_driver_blocks_before_reusing_invalidated_run(monkeypatch, tmp_path):
    output = tmp_path / "dry_run.db"
    monkeypatch.setattr(dry_run_carry_multi, "_OUT", str(output))

    def fail_if_looked_up(*_args, **_kwargs):
        pytest.fail("invalidated V1 records must not be looked up for silent reuse")

    monkeypatch.setattr(
        dry_run_carry_multi,
        "_existing_carry_run",
        fail_if_looked_up,
    )

    def fail_if_recomputed(*_args, **_kwargs):
        pytest.fail("invalidated V1 records must not be recomputed")

    monkeypatch.setattr(dry_run_carry_multi, "run_multi_period_dry_run", fail_if_recomputed)
    with pytest.raises(BacktestIntegrityBlocked):
        dry_run_carry_multi.main()
