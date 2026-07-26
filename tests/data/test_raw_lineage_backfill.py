import io
import json
import zipfile

from services.data.raw_archive import (archive_json_response,
                                       archive_response)
from tools.raw_data_lineage_backfill import run_backfill

_DAY = 86_400_000
_NOW = 21 * _DAY
_EXPECTED = 20 * _DAY


def _seed(tmp_path):
    mark = tmp_path / "mark.json"
    mark.write_text(json.dumps({
        "_meta": {"BTC": {"through": _EXPECTED}},
        "data": {"BTC": {str(_EXPECTED): 100.0}},
    }), encoding="utf-8")
    funding = tmp_path / "funding.json"
    funding.write_text(json.dumps({
        "BTC": {str(_EXPECTED): 0.001},
    }), encoding="utf-8")
    oi = tmp_path / "oi.json"
    oi_record = {
        "oi": 100.0, "oi_value": 1000.0, "top_ls": 1.1,
        "global_ls": 1.2, "taker_ls": 0.9,
    }
    oi.write_text(json.dumps({
        "_meta": {}, "data": {"BTC": {str(_EXPECTED): oi_record}},
    }), encoding="utf-8")
    return mark, funding, oi, oi_record


def _archive_bytes(root, dataset, payload, observed_at_ms):
    archive_response(
        root=root, payload=payload,
        source_uri=f"https://data.example.test/{dataset}/BTC.bin",
        provider="example-public-data", dataset_id=dataset,
        instrument_id="BTC", observed_at_ms=observed_at_ms,
        representation="HTTP_BODY_BYTES",
        parse_diagnostics={
            "status": "ACCEPTED", "input_rows": 1, "accepted_rows": 1,
            "rejected_rows": 0, "duplicate_rows": 0,
        },
    )


def _mark_zip(day_ms, close=100.0):
    text = (
        "open_time,open,high,low,close,volume,close_time\n"
        f"{day_ms},99,101,98,{close},0,{day_ms + _DAY - 1}\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT.csv", text)
    return buffer.getvalue()


def test_backfill_archives_and_reconciles_all_required_sources(tmp_path):
    mark_path, funding_path, oi_path, oi_record = _seed(tmp_path)
    archive = str(tmp_path / "raw")

    def mark_fetch(ticker, days, *, archive_root, observed_at_ms, failure_sink):
        _archive_bytes(archive_root, "perp_mark_prices", b"mark", observed_at_ms)
        return {_EXPECTED: 100.0}

    def oi_fetch(ticker, start_ms, end_ms, present, *, archive_root,
                 observed_at_ms, failure_sink):
        _archive_bytes(archive_root, "oi_metrics", b"oi", observed_at_ms)
        return {_EXPECTED: oi_record}

    def funding_fetch(ticker, *, days, now_ms, archive_root, observed_at_ms):
        archive_json_response(
            root=archive_root, payload=[{"funding": "raw"}],
            source_uri="https://api.example.test/funding?symbol=BTCUSDT",
            provider="example-public-data", dataset_id="funding",
            instrument_id=ticker, observed_at_ms=observed_at_ms,
            parse_diagnostics={"status": "ACCEPTED", "input_rows": 1},
        )
        return {_EXPECTED: 0.001}

    report = run_backfill(
        now_ms=_NOW,
        instruments=["BTC"],
        lookback_days=1,
        archive_root=archive,
        mark_path=str(mark_path),
        funding_path=str(funding_path),
        oi_path=str(oi_path),
        mark_fetch=mark_fetch,
        oi_fetch=oi_fetch,
        funding_fetch=funding_fetch,
        funding_workers=1,
    )

    assert report["verdict"] == "PASS"
    assert {source: value["matched_records"] for source, value in report["sources"].items()} == {
        "funding": 1, "oi_metrics": 1, "perp_mark_prices": 1,
    }
    assert report["raw_lineage"]["status"] == "PASS"
    assert report["raw_lineage"]["datasets"] == {
        "funding": 1, "oi_metrics": 1, "perp_mark_prices": 1,
    }


def test_backfill_blocks_source_value_mismatch(tmp_path):
    mark_path, funding_path, oi_path, oi_record = _seed(tmp_path)
    archive = str(tmp_path / "raw")

    def mark_fetch(ticker, days, *, archive_root, observed_at_ms, failure_sink):
        _archive_bytes(archive_root, "perp_mark_prices", b"mark", observed_at_ms)
        return {_EXPECTED: 999.0}

    def oi_fetch(ticker, start_ms, end_ms, present, *, archive_root,
                 observed_at_ms, failure_sink):
        _archive_bytes(archive_root, "oi_metrics", b"oi", observed_at_ms)
        return {_EXPECTED: oi_record}

    def funding_fetch(ticker, *, days, now_ms, archive_root, observed_at_ms):
        archive_json_response(
            root=archive_root, payload=[],
            source_uri="https://api.example.test/funding?symbol=BTCUSDT",
            provider="example-public-data", dataset_id="funding",
            instrument_id=ticker, observed_at_ms=observed_at_ms,
            parse_diagnostics={"status": "ACCEPTED", "input_rows": 0},
        )
        return {_EXPECTED: 0.001}

    report = run_backfill(
        now_ms=_NOW,
        instruments=["BTC"],
        lookback_days=1,
        archive_root=archive,
        mark_path=str(mark_path),
        funding_path=str(funding_path),
        oi_path=str(oi_path),
        mark_fetch=mark_fetch,
        oi_fetch=oi_fetch,
        funding_fetch=funding_fetch,
        funding_workers=1,
    )

    assert report["verdict"] == "BLOCK"
    assert report["sources"]["perp_mark_prices"]["matched_records"] == 0
    assert report["sources"]["perp_mark_prices"]["failures"][0]["kind"] == "VALUE_MISMATCH"


def test_backfill_replays_verified_archive_after_transient_refetch(tmp_path):
    mark_path, funding_path, oi_path, oi_record = _seed(tmp_path)
    archive = str(tmp_path / "raw")
    archive_response(
        root=archive, payload=_mark_zip(_EXPECTED),
        source_uri="https://data.example.test/mark/BTC.zip",
        provider="example-public-data", dataset_id="perp_mark_prices",
        instrument_id="BTC", observed_at_ms=_NOW - 1,
        representation="HTTP_BODY_BYTES",
        parse_diagnostics={
            "status": "ACCEPTED", "input_rows": 1, "accepted_rows": 1,
            "rejected_rows": 0, "duplicate_rows": 0,
            "event_time_ms": _EXPECTED,
        },
    )

    def mark_fetch(ticker, days, *, archive_root, observed_at_ms, failure_sink):
        failure_sink.append({
            "ticker": ticker, "day_ms": _EXPECTED, "kind": "TRANSIENT",
            "error_type": "URLError", "message": "connection reset",
        })
        return {}

    def oi_fetch(ticker, start_ms, end_ms, present, *, archive_root,
                 observed_at_ms, failure_sink):
        _archive_bytes(archive_root, "oi_metrics", b"oi", observed_at_ms)
        return {_EXPECTED: oi_record}

    def funding_fetch(ticker, *, days, now_ms, archive_root, observed_at_ms):
        archive_json_response(
            root=archive_root, payload=[],
            source_uri="https://api.example.test/funding?symbol=BTCUSDT",
            provider="example-public-data", dataset_id="funding",
            instrument_id=ticker, observed_at_ms=observed_at_ms,
            parse_diagnostics={"status": "ACCEPTED", "input_rows": 0},
        )
        return {_EXPECTED: 0.001}

    report = run_backfill(
        now_ms=_NOW,
        instruments=["BTC"],
        lookback_days=1,
        archive_root=archive,
        mark_path=str(mark_path),
        funding_path=str(funding_path),
        oi_path=str(oi_path),
        mark_fetch=mark_fetch,
        oi_fetch=oi_fetch,
        funding_fetch=funding_fetch,
        funding_workers=1,
    )

    mark = report["sources"]["perp_mark_prices"]
    assert mark["passed"] is True
    assert mark["matched_records"] == 1
    assert mark["refetched_records"] == 0
    assert mark["replayed_records"] == 1
    assert mark["failures"] == []
    assert mark["warnings"][0]["kind"] == "TRANSIENT"


_REST_MARK = "REST_GAP_FILL"
_REST_DATASET_MARK = "perp_mark_prices_rest_gap_fill"
_REST_DATASET_OI = "oi_metrics_rest_gap_fill"


def _rest_mark_row(day_ms, close=100.0):
    return [day_ms, str(close - 1), str(close + 1), str(close - 2), str(close),
            "0", day_ms + _DAY - 1]


def _rest_oi_rows(record):
    return {
        "openInterestHist": {"symbol": "BTCUSDT",
                             "sumOpenInterest": str(record["oi"]),
                             "sumOpenInterestValue": str(record["oi_value"]),
                             "timestamp": 0},
        "globalLongShortAccountRatio": {"longShortRatio": str(record["global_ls"]),
                                        "timestamp": 0},
        "topLongShortPositionRatio": {"longShortRatio": str(record["top_ls"]),
                                      "timestamp": 0},
        "takerlongshortRatio": {"buySellRatio": str(record["taker_ls"]),
                                "timestamp": 0},
    }


def _archive_rest_day(root, dataset, payload, day_ms, observed_at_ms):
    archive_json_response(
        root=root, payload=payload,
        source_uri=f"https://fapi.example.test/{dataset}?symbol=BTCUSDT",
        provider="example-public-rest", dataset_id=dataset,
        instrument_id="BTC", observed_at_ms=observed_at_ms,
        parse_diagnostics={
            "status": "ACCEPTED", "input_rows": 1, "accepted_rows": 1,
            "rejected_rows": 0, "duplicate_rows": 0, "event_time_ms": int(day_ms),
        },
    )


def _seed_rest_window(tmp_path, oi_record):
    """2-day window: day 19 archive-canonical, day 20 a REST_GAP_FILL placeholder."""
    older, expected = 19 * _DAY, 20 * _DAY
    mark = tmp_path / "mark.json"
    mark.write_text(json.dumps({
        "_meta": {"BTC": {"through": expected}},
        "data": {"BTC": {str(older): 100.0, str(expected): 101.0}},
        "provenance": {"BTC": {str(expected): _REST_MARK}},
    }), encoding="utf-8")
    funding = tmp_path / "funding.json"
    funding.write_text(json.dumps({
        "BTC": {str(older): 0.001, str(expected): 0.001},
    }), encoding="utf-8")
    oi = tmp_path / "oi.json"
    oi.write_text(json.dumps({
        "_meta": {},
        "data": {"BTC": {str(older): oi_record,
                         str(expected): dict(oi_record, source=_REST_MARK)}},
    }), encoding="utf-8")
    return mark, funding, oi, older, expected


def _funding_fetch_two_days(older, expected):
    def funding_fetch(ticker, *, days, now_ms, archive_root, observed_at_ms):
        archive_json_response(
            root=archive_root, payload=[{"funding": "raw"}],
            source_uri="https://api.example.test/funding?symbol=BTCUSDT",
            provider="example-public-data", dataset_id="funding",
            instrument_id=ticker, observed_at_ms=observed_at_ms,
            parse_diagnostics={"status": "ACCEPTED", "input_rows": 1},
        )
        return {older: 0.001, expected: 0.001}
    return funding_fetch


def test_backfill_verifies_rest_gap_fill_days_via_rest_source(tmp_path):
    _, _, _, oi_record = _seed(tmp_path)
    mark_path, funding_path, oi_path, older, expected = _seed_rest_window(
        tmp_path, oi_record
    )
    archive = str(tmp_path / "raw")
    seen = {}

    def mark_fetch(ticker, days, *, archive_root, observed_at_ms, failure_sink):
        seen["mark_days"] = list(days)
        _archive_bytes(archive_root, "perp_mark_prices", b"mark", observed_at_ms)
        return {older: 100.0}

    def rest_mark_fetch(ticker, days, *, now_ms, archive_root, observed_at_ms,
                        failure_sink):
        seen["rest_mark_days"] = list(days)
        _archive_rest_day(archive_root, _REST_DATASET_MARK,
                          _rest_mark_row(expected, 101.0), expected, observed_at_ms)
        return {expected: 101.0}

    def oi_fetch(ticker, start_ms, end_ms, present, *, archive_root,
                 observed_at_ms, failure_sink):
        _archive_bytes(archive_root, "oi_metrics", b"oi", observed_at_ms)
        return {older: oi_record}

    def rest_oi_fetch(ticker, days, *, now_ms, archive_root, observed_at_ms,
                      failure_sink):
        _archive_rest_day(archive_root, _REST_DATASET_OI,
                          _rest_oi_rows(oi_record), expected, observed_at_ms)
        return {expected: dict(oi_record)}

    report = run_backfill(
        now_ms=_NOW,
        instruments=["BTC"],
        lookback_days=2,
        archive_root=archive,
        mark_path=str(mark_path),
        funding_path=str(funding_path),
        oi_path=str(oi_path),
        mark_fetch=mark_fetch,
        oi_fetch=oi_fetch,
        funding_fetch=_funding_fetch_two_days(older, expected),
        rest_mark_fetch=rest_mark_fetch,
        rest_oi_fetch=rest_oi_fetch,
        funding_workers=1,
    )

    assert report["verdict"] == "PASS"
    assert seen["mark_days"] == [older]              # REST 占位日不回归档重取
    assert seen["rest_mark_days"] == [expected]
    mark = report["sources"]["perp_mark_prices"]
    assert mark["matched_records"] == 2
    assert mark["refetched_records"] == 1
    assert mark["rest_refetched_records"] == 1
    oi = report["sources"]["oi_metrics"]
    assert oi["matched_records"] == 2
    assert oi["rest_refetched_records"] == 1
    assert report["raw_lineage"]["datasets"] == {
        "funding": 1,
        "oi_metrics": 1,
        "oi_metrics_rest_gap_fill": 1,
        "perp_mark_prices": 1,
        "perp_mark_prices_rest_gap_fill": 1,
    }


def test_backfill_blocks_rest_gap_fill_value_mismatch(tmp_path):
    _, _, _, oi_record = _seed(tmp_path)
    mark_path, funding_path, oi_path, older, expected = _seed_rest_window(
        tmp_path, oi_record
    )
    archive = str(tmp_path / "raw")

    def mark_fetch(ticker, days, *, archive_root, observed_at_ms, failure_sink):
        _archive_bytes(archive_root, "perp_mark_prices", b"mark", observed_at_ms)
        return {older: 100.0}

    def rest_mark_fetch(ticker, days, *, now_ms, archive_root, observed_at_ms,
                        failure_sink):
        return {expected: 999.0}                     # REST 重取与缓存占位不一致

    def oi_fetch(ticker, start_ms, end_ms, present, *, archive_root,
                 observed_at_ms, failure_sink):
        _archive_bytes(archive_root, "oi_metrics", b"oi", observed_at_ms)
        return {older: oi_record}

    def rest_oi_fetch(ticker, days, *, now_ms, archive_root, observed_at_ms,
                      failure_sink):
        return {expected: dict(oi_record)}

    report = run_backfill(
        now_ms=_NOW,
        instruments=["BTC"],
        lookback_days=2,
        archive_root=archive,
        mark_path=str(mark_path),
        funding_path=str(funding_path),
        oi_path=str(oi_path),
        mark_fetch=mark_fetch,
        oi_fetch=oi_fetch,
        funding_fetch=_funding_fetch_two_days(older, expected),
        rest_mark_fetch=rest_mark_fetch,
        rest_oi_fetch=rest_oi_fetch,
        funding_workers=1,
    )

    assert report["verdict"] == "BLOCK"
    mark = report["sources"]["perp_mark_prices"]
    assert mark["matched_records"] == 1
    assert mark["failures"][0]["kind"] == "VALUE_MISMATCH"
    assert mark["failures"][0]["day_ms"] == expected


def test_backfill_replays_archived_rest_day_after_transient_rest_refetch(tmp_path):
    _, _, _, oi_record = _seed(tmp_path)
    mark_path, funding_path, oi_path, older, expected = _seed_rest_window(
        tmp_path, oi_record
    )
    archive = str(tmp_path / "raw")
    _archive_rest_day(archive, _REST_DATASET_MARK,
                      _rest_mark_row(expected, 101.0), expected, _NOW - 1)

    def mark_fetch(ticker, days, *, archive_root, observed_at_ms, failure_sink):
        _archive_bytes(archive_root, "perp_mark_prices", b"mark", observed_at_ms)
        return {older: 100.0}

    def rest_mark_fetch(ticker, days, *, now_ms, archive_root, observed_at_ms,
                        failure_sink):
        failure_sink.append({
            "ticker": ticker, "day_ms": expected, "kind": "TRANSIENT",
            "error_type": "URLError", "message": "connection reset",
        })
        return {}

    def oi_fetch(ticker, start_ms, end_ms, present, *, archive_root,
                 observed_at_ms, failure_sink):
        _archive_bytes(archive_root, "oi_metrics", b"oi", observed_at_ms)
        return {older: oi_record}

    def rest_oi_fetch(ticker, days, *, now_ms, archive_root, observed_at_ms,
                      failure_sink):
        return {expected: dict(oi_record)}

    report = run_backfill(
        now_ms=_NOW,
        instruments=["BTC"],
        lookback_days=2,
        archive_root=archive,
        mark_path=str(mark_path),
        funding_path=str(funding_path),
        oi_path=str(oi_path),
        mark_fetch=mark_fetch,
        oi_fetch=oi_fetch,
        funding_fetch=_funding_fetch_two_days(older, expected),
        rest_mark_fetch=rest_mark_fetch,
        rest_oi_fetch=rest_oi_fetch,
        funding_workers=1,
    )

    mark = report["sources"]["perp_mark_prices"]
    assert mark["passed"] is True
    assert mark["matched_records"] == 2
    assert mark["rest_refetched_records"] == 0
    assert mark["rest_replayed_records"] == 1
    assert mark["failures"] == []
    assert mark["warnings"][0]["kind"] == "TRANSIENT"
    assert mark["warnings"][0]["resolved_by"] == "ARCHIVE_REPLAY"
