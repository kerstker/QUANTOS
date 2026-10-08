from services.data.raw_archive import archive_response, summarize_indexed_archive
from tools.raw_archive_audit import run_audit


def test_full_audit_publishes_pass_and_refreshes_index_anchor(tmp_path):
    archive_response(
        root=str(tmp_path), payload=b"payload",
        source_uri="https://data.example.test/BTC/day.zip",
        provider="example", dataset_id="perp_mark_prices",
        instrument_id="BTC", observed_at_ms=1000,
        representation="HTTP_BODY_BYTES",
        parse_diagnostics={
            "status": "ACCEPTED", "event_time_ms": 500,
            "input_rows": 1, "accepted_rows": 1,
        },
    )

    report = run_audit(root=str(tmp_path), now_ms=2000)
    indexed = summarize_indexed_archive(str(tmp_path))

    assert report["verdict"] == "PASS"
    assert report["summary"]["observation_count"] == 1
    assert indexed["status"] == "PASS"
    assert indexed["full_audit_at_ms"] is not None
