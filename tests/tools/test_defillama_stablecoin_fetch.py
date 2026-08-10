import pytest

from services.data.fundamental_store import SqliteFundamentalStore
from tools import defillama_stablecoin_fetch as stable


DAY_MS = 86_400_000
T0 = (1_700_000_000_000 // DAY_MS) * DAY_MS


def _row(day, value):
    return {
        "date": str(day // 1000),
        "totalCirculatingUSD": {"peggedUSD": value},
    }


def test_parse_chart_keeps_only_completed_positive_usd_supply():
    diagnostics = {}

    rows = stable.parse_stablecoin_chart(
        [_row(T0, 100.0), _row(T0 + DAY_MS, 110.0), _row(T0 + 2 * DAY_MS, 0),
         {"date": "bad", "totalCirculatingUSD": {"peggedUSD": 1}}],
        now_ms=T0 + 2 * DAY_MS,
        diagnostics=diagnostics,
    )

    assert rows == {T0: 100.0, T0 + DAY_MS: 110.0}
    assert diagnostics == {
        "status": "ACCEPTED", "input_rows": 4, "accepted_rows": 2,
        "rejected_rows": 2, "duplicate_rows": 0,
    }


def test_snapshot_marks_retrospective_history_non_pit_and_non_promoting():
    snapshot = stable.build_snapshot(
        {T0: 100.0, T0 + DAY_MS: 110.0}, fetched_at_ms=T0 + 2 * DAY_MS,
    )

    assert snapshot["provenance"] == "REAL_DEFILLAMA_GLOBAL_STABLECOIN_HISTORY"
    assert snapshot["revision_policy"] == "RETROSPECTIVE_REVISABLE_NO_VINTAGES"
    assert snapshot["pit_admissible_for_promotion"] is False
    assert snapshot["credentials_accessed"] is False
    assert snapshot["order_capability"] is False


def test_forward_vintage_records_only_latest_completed_supply(tmp_path):
    snapshot = stable.build_snapshot(
        {T0: 100.0, T0 + DAY_MS: 110.0}, fetched_at_ms=T0 + 2 * DAY_MS,
    )
    db_path = tmp_path / "stablecoin_vintages.db"

    assert stable.record_forward_vintage(snapshot, str(db_path)) == 1
    assert stable.record_forward_vintage(snapshot, str(db_path)) == 0

    store = SqliteFundamentalStore(str(db_path))
    row = store.latest_as_of(
        stable.FORWARD_FEATURE_ID, stable.FORWARD_INSTRUMENT_ID, T0 + 2 * DAY_MS,
    )
    assert row is not None
    assert (row.event_time, row.knowledge_time, row.value) == (
        T0 + DAY_MS, T0 + 2 * DAY_MS, 110.0,
    )
    store.close()


def test_noncanonical_snapshot_cannot_enter_forward_vintage():
    snapshot = stable.build_snapshot({T0: 100.0}, fetched_at_ms=T0 + DAY_MS)
    snapshot["pit_admissible_for_promotion"] = True
    with pytest.raises(stable.DefiLlamaStablecoinError, match="snapshot contract"):
        stable.forward_vintage_value(snapshot)

