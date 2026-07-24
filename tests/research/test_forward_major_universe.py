from tools.binance_oi_metrics_fetch import DEFAULT_TICKERS, FORWARD_MAJOR_TICKERS
from tools.data_quality_admission import build_data_quality_evidence


def test_forward_scope_is_fixed_major_book_and_not_the_wider_history_tail():
    assert len(FORWARD_MAJOR_TICKERS) == 14
    assert len(set(FORWARD_MAJOR_TICKERS)) == len(FORWARD_MAJOR_TICKERS)
    assert set(FORWARD_MAJOR_TICKERS).issubset(set(DEFAULT_TICKERS))
    assert {"XLM", "ICP", "APT", "OP", "FIL", "INJ", "NEAR"}.isdisjoint(
        set(FORWARD_MAJOR_TICKERS)
    )


def test_admission_defaults_to_fixed_forward_major_scope(tmp_path):
    evidence = build_data_quality_evidence(
        now_ms=1_700_000_000_000,
        price_path=str(tmp_path / "prices.json"),
        funding_path=str(tmp_path / "funding.json"),
        oi_path=str(tmp_path / "oi.json"),
        snapshot_path=str(tmp_path / "snapshot.json"),
        refresh_status_path=str(tmp_path / "refresh.json"),
        raw_archive_path=str(tmp_path / "raw"),
        lineage_backfill_path=str(tmp_path / "lineage.json"),
    )
    assert evidence["instrument_scope"] == sorted(FORWARD_MAJOR_TICKERS)
