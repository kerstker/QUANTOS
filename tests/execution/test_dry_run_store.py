import pytest

from services.execution.dry_run_store import DryRunRun, DryRunStore, DryRunStoreError


def _run(rid="r1"):
    return DryRunRun(run_id=rid, strategy_id="carry", timestamp=1000, n_intents=5, n_approved=4,
                     n_rejected=1, rejected_reasons=["FAT_FINGER"], pnl={"net": 0.012},
                     audit_root="abc123", detail={"fills": 4})


def test_save_get_all_roundtrip():
    s = DryRunStore(":memory:", create=True)
    s.save_run(_run("r1"))
    s.save_run(_run("r2"))
    g = s.get_run("r1")
    assert g["strategy_id"] == "carry" and g["n_approved"] == 4 and g["pnl"]["net"] == 0.012
    assert g["rejected_reasons"] == ["FAT_FINGER"] and g["detail"]["fills"] == 4
    assert {r["run_id"] for r in s.all_runs()} == {"r1", "r2"}
    assert s.get_run("nope") is None


def test_missing_table_fails_closed():
    # 全新 in-memory 库 + create=False -> 无 dry_run 表 -> fail-closed(无文件残留)
    with pytest.raises(DryRunStoreError):
        DryRunStore(":memory:", create=False)
