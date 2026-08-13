from services.console.api import data_catalog


def test_data_channel_payload_is_explicit_when_inventory_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTOS_STATE_ROOT", str(tmp_path))
    payload = data_catalog._data_channel_payload()
    assert payload["status"] == "UNAVAILABLE"
    assert payload["channels"] == []


def test_data_channel_payload_reads_real_inventory(tmp_path, monkeypatch):
    path = tmp_path / "data" / "cache" / "data_channel_inventory.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"generated_at_ms": 1, "channels": [{"channel_id": "x"}], '
        '"summary": {"channel_count": 1}, "honesty": ["no synthetic"]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANTOS_STATE_ROOT", str(tmp_path))
    payload = data_catalog._data_channel_payload()
    assert payload["status"] == "REAL_COVERAGE_INVENTORY"
    assert payload["channels"][0]["channel_id"] == "x"
