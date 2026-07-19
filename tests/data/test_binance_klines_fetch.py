import json

from services.data.raw_archive import summarize_archive
from tools.binance_klines_fetch import fetch_daily_rows

_DAY = 86_400_000


def test_daily_klines_archive_canonical_response_and_parse_diagnostics(tmp_path):
    day = 8 * _DAY
    batch = [
        [day, "100", "110", "90", "105", "10"],
        [day + 1, "101", "111", "91", "106", "11"],
        [day + _DAY, "0", "1", "0", "0", "1"],
        ["malformed"],
    ]

    rows = fetch_daily_rows(
        "BTC",
        days=5,
        http_get=lambda _url, _headers: batch,
        now_ms=10 * _DAY,
        archive_root=str(tmp_path),
        observed_at_ms=10 * _DAY,
    )

    assert len(rows) == 1
    lineage = summarize_archive(str(tmp_path))
    assert lineage["status"] == "PASS"
    assert lineage["datasets"] == {"spot_klines": 1}
    assert lineage["representations"] == ["CANONICAL_JSON_UTF8"]
    observation = json.loads(next(tmp_path.glob("observations/sha256/*/*.json")).read_text())
    assert observation["parse_diagnostics"] == {
        "accepted_rows": 1,
        "duplicate_rows": 1,
        "input_rows": 4,
        "rejected_rows": 2,
        "status": "ACCEPTED",
    }
