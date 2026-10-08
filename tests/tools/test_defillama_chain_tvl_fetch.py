from __future__ import annotations

import urllib.error

import pytest

from tools import defillama_chain_tvl_fetch as dl


DAY_MS = 86_400_000
T0 = (1_700_000_000_000 // DAY_MS) * DAY_MS


def test_source_contract_is_unique_and_wider_than_formal_breadth_gate() -> None:
    chains = [chain for chain, _asset in dl.CHAIN_ASSET_MAP]
    assets = [asset for _chain, asset in dl.CHAIN_ASSET_MAP]
    assert len(chains) == len(set(chains)) == 19
    assert len(assets) == len(set(assets)) == 19
    assert ("Ethereum", "ETH") in dl.CHAIN_ASSET_MAP
    assert ("BSC", "BNB") in dl.CHAIN_ASSET_MAP
    assert ("XRPL", "XRP") in dl.CHAIN_ASSET_MAP


def test_parse_rows_keeps_completed_finite_positive_tvl() -> None:
    diagnostics = {}
    result = dl.parse_tvl_rows(
        [
            {"date": T0 // 1000, "tvl": "100.5"},
            {"date": (T0 + DAY_MS) // 1000, "tvl": 0},
            {"date": (T0 + 2 * DAY_MS) // 1000, "tvl": 300},
            {"date": "bad", "tvl": 1},
        ],
        now_ms=T0 + 2 * DAY_MS,
        start_ms=T0,
        diagnostics=diagnostics,
    )
    assert result == {T0: 100.5}
    assert diagnostics == {
        "status": "ACCEPTED",
        "input_rows": 4,
        "accepted_rows": 1,
        "rejected_rows": 3,
        "duplicate_rows": 0,
    }


def test_fetch_validates_chain_catalog_and_archives_official_pages(tmp_path) -> None:
    calls = []

    def fake_get(url, _headers):
        calls.append(url)
        if url.endswith("/v2/chains"):
            return [
                {"name": "Ethereum", "tokenSymbol": "ETH"},
                {"name": "BSC", "tokenSymbol": "BNB"},
            ]
        return [
            {"date": T0 // 1000, "tvl": 100.0},
            {"date": (T0 + DAY_MS) // 1000, "tvl": 110.0},
        ]

    result = dl.fetch_history(
        chain_asset_map=(("Ethereum", "ETH"), ("BSC", "BNB")),
        days=10,
        now_ms=T0 + 2 * DAY_MS,
        http_get=fake_get,
        sleep=0,
        retry_sleep=0,
        archive_root=str(tmp_path),
        observed_at_ms=T0 + 2 * DAY_MS,
    )

    assert result["ETH"]["chain"] == "Ethereum"
    assert result["ETH"]["tvl"] == {T0: 100.0, T0 + DAY_MS: 110.0}
    assert len(calls) == 3
    assert calls[0] == "https://api.llama.fi/v2/chains"


def test_catalog_mismatch_and_http_429_fail_without_retry() -> None:
    attempts = 0

    def wrong_catalog(url, _headers):
        if url.endswith("/v2/chains"):
            return [{"name": "Ethereum", "tokenSymbol": "NOT_ETH"}]
        raise AssertionError("history must not be requested")

    with pytest.raises(dl.DefiLlamaFetchError, match="catalog mismatch"):
        dl.fetch_history(
            chain_asset_map=(("Ethereum", "ETH"),),
            days=10,
            now_ms=T0 + DAY_MS,
            http_get=wrong_catalog,
            sleep=0,
            retry_sleep=0,
        )

    def limited(url, _headers):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)

    with pytest.raises(urllib.error.HTTPError):
        dl.fetch_history(
            chain_asset_map=(("Ethereum", "ETH"),),
            days=10,
            now_ms=T0 + DAY_MS,
            http_get=limited,
            sleep=0,
            retry_sleep=0,
            max_attempts=3,
        )
    assert attempts == 1


def test_snapshot_reports_exact_chain_coverage() -> None:
    snapshot = dl.build_snapshot(
        {
            "ETH": {"chain": "Ethereum", "tvl": {T0: 100.0, T0 + DAY_MS: 110.0}},
            "BNB": {"chain": "BSC", "tvl": {}},
        },
        fetched_at_ms=T0 + 2 * DAY_MS,
    )
    assert snapshot["provenance"] == "REAL_DEFILLAMA_FREE_CHAIN_TVL_HISTORY"
    assert snapshot["assets"]["ETH"] == {
        "chain": "Ethereum",
        "days": 2,
        "first_date": "2023-11-14",
        "last_date": "2023-11-15",
    }
    assert snapshot["assets"]["BNB"]["first_date"] is None
    assert snapshot["pit_admissible_for_promotion"] is False
    assert snapshot["revision_policy"] == "RETROSPECTIVE_REVISABLE_NO_VINTAGES"


def test_forward_vintage_selects_only_latest_sufficiently_broad_day() -> None:
    snapshot = dl.build_snapshot(
        {
            "ETH": {"chain": "Ethereum", "tvl": {T0: 100.0, T0 + DAY_MS: 110.0}},
            "BNB": {"chain": "BSC", "tvl": {T0: 50.0}},
            "SOL": {"chain": "Solana", "tvl": {T0 + DAY_MS: 25.0}},
        },
        fetched_at_ms=T0 + 2 * DAY_MS,
    )

    values = dl.forward_vintage_values(snapshot, min_instruments=2)

    assert [(row.instrument_id, row.event_time, row.knowledge_time, row.value) for row in values] == [
        ("ETH", T0 + DAY_MS, T0 + 2 * DAY_MS, 110.0),
        ("SOL", T0 + DAY_MS, T0 + 2 * DAY_MS, 25.0),
    ]
    assert {row.feature_id for row in values} == {dl.FORWARD_TVL_FEATURE_ID}


def test_forward_vintage_retains_later_restatement(tmp_path) -> None:
    db_path = tmp_path / "tvl_vintages.db"
    first = dl.build_snapshot(
        {
            "ETH": {"chain": "Ethereum", "tvl": {T0: 100.0}},
            "SOL": {"chain": "Solana", "tvl": {T0: 25.0}},
        },
        fetched_at_ms=T0 + DAY_MS,
    )
    revised = dl.build_snapshot(
        {
            "ETH": {"chain": "Ethereum", "tvl": {T0: 101.0}},
            "SOL": {"chain": "Solana", "tvl": {T0: 25.0}},
        },
        fetched_at_ms=T0 + 2 * DAY_MS,
    )

    assert dl.record_forward_vintage(first, str(db_path), min_instruments=2) == 2
    assert dl.record_forward_vintage(first, str(db_path), min_instruments=2) == 0
    assert dl.record_forward_vintage(revised, str(db_path), min_instruments=2) == 2

    from services.data.fundamental_store import SqliteFundamentalStore

    store = SqliteFundamentalStore(str(db_path))
    first_known = store.latest_as_of(
        dl.FORWARD_TVL_FEATURE_ID, "ETH", T0 + DAY_MS
    )
    revised_known = store.latest_as_of(
        dl.FORWARD_TVL_FEATURE_ID, "ETH", T0 + 2 * DAY_MS
    )
    assert first_known is not None and first_known.value == 100.0
    assert revised_known is not None and revised_known.value == 101.0
    assert len(store.as_of(dl.FORWARD_TVL_FEATURE_ID, "ETH", T0 + 2 * DAY_MS)) == 2
    store.close()


def test_forward_vintage_fails_closed_on_thin_or_noncanonical_snapshot() -> None:
    snapshot = dl.build_snapshot(
        {"ETH": {"chain": "Ethereum", "tvl": {T0: 100.0}}},
        fetched_at_ms=T0 + DAY_MS,
    )
    with pytest.raises(dl.DefiLlamaFetchError, match="breadth"):
        dl.forward_vintage_values(snapshot, min_instruments=2)

    snapshot["pit_admissible_for_promotion"] = True
    with pytest.raises(dl.DefiLlamaFetchError, match="snapshot contract"):
        dl.forward_vintage_values(snapshot, min_instruments=1)


def test_main_writes_diagnostic_snapshot_and_forward_vintage(
    tmp_path, monkeypatch
) -> None:
    out = tmp_path / "defillama_chain_tvl.json"
    db_path = tmp_path / "defillama_tvl_vintages.db"
    assets = tuple(f"A{index:02d}" for index in range(10))
    series = {
        asset: {"chain": f"Chain {asset}", "tvl": {T0 + DAY_MS: 100.0 + index}}
        for index, asset in enumerate(assets)
    }
    monkeypatch.setattr(dl, "fetch_history", lambda **_kwargs: series)
    monkeypatch.setattr(dl.time, "time", lambda: (T0 + 2 * DAY_MS) / 1000)

    snapshot = dl.main(
        [
            "--out",
            str(out),
            "--archive-root",
            str(tmp_path / "raw"),
            "--vintage-db",
            str(db_path),
            "--sleep",
            "0",
        ]
    )

    assert out.exists()
    assert db_path.exists()
    assert snapshot["pit_admissible_for_promotion"] is False

    from services.data.fundamental_store import SqliteFundamentalStore

    store = SqliteFundamentalStore(str(db_path))
    assert sum(
        len(store.as_of(dl.FORWARD_TVL_FEATURE_ID, asset, T0 + 2 * DAY_MS))
        for asset in assets
    ) == 10
    store.close()
