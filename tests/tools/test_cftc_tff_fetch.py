import pytest

from tools import cftc_tff_fetch as cot


def _row(*, market=None, report_date="2024-01-02T00:00:00.000"):
    return {
        "id": "240102133741F",
        "market_and_exchange_names": market or cot.CONTRACTS["BTC"],
        "report_date_as_yyyy_mm_dd": report_date,
        "open_interest_all": "10000",
        "pct_of_oi_lev_money_long": "20.0",
        "pct_of_oi_lev_money_short": "35.0",
        "pct_of_oi_asset_mgr_long": "18.0",
        "pct_of_oi_asset_mgr_short": "7.0",
        "pct_of_oi_dealer_long_all": "3.0",
        "pct_of_oi_dealer_short_all": "8.0",
        "pct_of_oi_other_rept_long": "9.0",
        "pct_of_oi_other_rept_short": "11.0",
        "pct_of_oi_nonrept_long_all": "30.0",
        "pct_of_oi_nonrept_short_all": "22.0",
    }


def test_parse_rows_uses_conservative_report_plus_seven_day_knowledge_time():
    observed = cot.iso_date_to_ms("2024-01-20")

    rows = cot.parse_tff_rows([_row()], instrument="BTC", observed_at_ms=observed)

    assert len(rows) == 1
    assert rows[0]["event_time_ms"] == cot.iso_date_to_ms("2024-01-02")
    assert rows[0]["knowledge_time_ms"] == cot.iso_date_to_ms("2024-01-09")
    assert rows[0]["leveraged_money_net_pct"] == pytest.approx(-15.0)
    assert rows[0]["asset_manager_net_pct"] == pytest.approx(11.0)
    assert rows[0]["dealer_net_pct"] == pytest.approx(-5.0)
    assert rows[0]["source_row_id"] == "240102133741F"


def test_parse_rows_rejects_wrong_contract_and_nonfinite_values():
    with pytest.raises(cot.CftcFetchError, match="contract mismatch"):
        cot.parse_tff_rows(
            [_row(market=cot.CONTRACTS["ETH"])],
            instrument="BTC",
            observed_at_ms=cot.iso_date_to_ms("2024-01-20"),
        )
    bad = _row()
    bad["pct_of_oi_lev_money_long"] = "NaN"
    with pytest.raises(cot.CftcFetchError, match="invalid"):
        cot.parse_tff_rows(
            [bad], instrument="BTC", observed_at_ms=cot.iso_date_to_ms("2024-01-20")
        )


def test_future_knowledge_rows_are_omitted_not_backdated():
    rows = cot.parse_tff_rows(
        [_row()],
        instrument="BTC",
        observed_at_ms=cot.iso_date_to_ms("2024-01-05"),
    )
    assert rows == []


def test_fetch_pages_are_exact_contract_filtered_and_deduplicated():
    calls = []
    pages = [[_row()], [_row()]]

    def fake_get(url, _headers):
        calls.append(url)
        return pages[len(calls) - 1]

    rows = cot.fetch_contract_history(
        "BTC", http_get=fake_get, page_size=1, max_pages=3, sleep=0,
        observed_at_ms=cot.iso_date_to_ms("2024-01-20"),
    )

    assert len(rows) == 1
    assert "%24offset=0" in calls[0]
    assert "%24offset=1" in calls[1]
    assert "BITCOIN" in calls[0]


def test_snapshot_is_public_read_only_and_non_promoting():
    payload = cot.build_snapshot(
        rows_by_instrument={"BTC": cot.parse_tff_rows(
            [_row()], instrument="BTC", observed_at_ms=cot.iso_date_to_ms("2024-01-20")
        )},
        fetched_at_ms=cot.iso_date_to_ms("2024-01-20"),
    )

    assert payload["provenance"] == "REAL_CFTC_TFF_FUTURES_ONLY"
    assert payload["pit_admissible_for_research"] is True
    assert payload["credentials_accessed"] is False
    assert payload["account_data_accessed"] is False
    assert payload["order_capability"] is False
    assert payload["promotion_eligible"] is False

