from services.console.api import data_catalog


def test_data_channel_payload_is_explicit_when_inventory_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(data_catalog, "_DATA_CHANNEL_INVENTORY",
                        str(tmp_path / "missing.json"))
    payload = data_catalog._data_channel_payload()
    assert payload["status"] == "UNAVAILABLE"
    assert payload["channels"] == []


def test_data_channel_payload_reads_real_inventory(tmp_path, monkeypatch):
    path = tmp_path / "inventory.json"
    path.write_text(
        '{"generated_at_ms": 1, "channels": [{"channel_id": "x"}], '
        '"summary": {"channel_count": 1}, "honesty": ["no synthetic"]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(data_catalog, "_DATA_CHANNEL_INVENTORY", str(path))
    payload = data_catalog._data_channel_payload()
    assert payload["status"] == "REAL_COVERAGE_INVENTORY"
    assert payload["channels"][0]["channel_id"] == "x"
