import json

from tools.data_channel_inventory import build_inventory, write_inventory


def test_inventory_marks_missing_supplemental_channels_blocked(tmp_path):
    inv = build_inventory(str(tmp_path), now_ms=1_700_000_000_000)
    rows = {row["channel_id"]: row for row in inv["channels"]}
    assert rows["binance_liquidations"]["artifact"]["status"] == "PUBLIC_STREAM_REQUIRED"
    assert rows["cross_exchange_book"]["artifact"]["status"] == "BLOCKED_MISSING"


def test_inventory_reads_json_artifact_without_promoting_status(tmp_path):
    path = tmp_path / "data" / "cache"
    path.mkdir(parents=True)
    (path / "cross_exchange_snapshot.json").write_text(
        json.dumps({"snapshots": [{"observed_at_ms": 1_700_000_000_000}]})
    )
    inv = build_inventory(str(tmp_path), now_ms=1_700_000_000_001)
    row = next(item for item in inv["channels"] if item["channel_id"] == "cross_exchange_book")
    assert row["artifact"]["status"] == "REAL_SNAPSHOT"
    assert row["artifact"]["last_observed_ms"] == 1_700_000_000_000


def test_write_inventory_emits_utf8_json_and_markdown(tmp_path):
    out = tmp_path / "inventory.json"
    report = tmp_path / "report.md"
    write_inventory(root=str(tmp_path), output_path=str(out), report_path=str(report),
                    now_ms=1_700_000_000_000)
    assert "数据渠道" in report.read_text(encoding="utf-8")
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 1
