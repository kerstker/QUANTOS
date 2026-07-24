from __future__ import annotations

from tools import dry_run_carry_multi


def test_default_driver_reuses_existing_run_without_recomputing(monkeypatch, tmp_path, capsys):
    output = tmp_path / "dry_run.db"
    monkeypatch.setattr(dry_run_carry_multi, "_OUT", str(output))
    monkeypatch.setattr(
        dry_run_carry_multi,
        "_existing_carry_run",
        lambda path: {
            "run_id": "existing-carry",
            "pnl": {"sharpe": 0.5, "residual": 0.0},
        },
    )

    def fail_if_recomputed(*_args, **_kwargs):
        raise AssertionError("existing append-only run must be reused")

    monkeypatch.setattr(dry_run_carry_multi, "run_multi_period_dry_run", fail_if_recomputed)
    dry_run_carry_multi.main()

    assert "existing-carry" in capsys.readouterr().out

