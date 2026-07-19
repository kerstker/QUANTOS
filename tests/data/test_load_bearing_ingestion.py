import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_LOAD_BEARING = (
    "tools/binance_klines_fetch.py",
    "tools/binance_klines_1h_fetch.py",
    "tools/binance_klines_1m_fetch.py",
    "tools/crypto_funding_research.py",
)


def _ingestion_calls(relative_path):
    tree = ast.parse((_ROOT / relative_path).read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ingest_provider_rows"
    ]


@pytest.mark.parametrize("relative_path", _LOAD_BEARING)
def test_load_bearing_kline_ingestion_is_strict_and_cadence_aware(relative_path):
    calls = _ingestion_calls(relative_path)
    assert calls, f"no ingest_provider_rows call found in {relative_path}"
    for call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        strict = keywords.get("strict")
        assert isinstance(strict, ast.Constant) and strict.value is True, relative_path
        assert "expected_interval" in keywords, relative_path


def test_live_refresh_wires_raw_archive_to_all_four_sources():
    tree = ast.parse((_ROOT / "tools/live_data_refresh.py").read_text(encoding="utf-8"))
    expected = {"refresh_prices", "refresh_funding", "refresh_mark_prices", "refresh_oi"}
    wired = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in expected:
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        archive_root = keywords.get("archive_root")
        if isinstance(archive_root, ast.Name) and archive_root.id == "_RAW_ARCHIVE":
            wired.add(node.func.id)
    assert wired == expected


def test_funding_research_wires_raw_archive_to_price_and_funding_fetches():
    tree = ast.parse(
        (_ROOT / "tools/crypto_funding_research.py").read_text(encoding="utf-8")
    )
    expected = {"fetch_daily_rows", "fetch_daily_funding"}
    wired = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in expected:
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        archive_root = keywords.get("archive_root")
        observed_at = keywords.get("observed_at_ms")
        if (isinstance(archive_root, ast.Name)
                and archive_root.id == "DEFAULT_RAW_ARCHIVE"
                and isinstance(observed_at, ast.Name)
                and observed_at.id == "now_ms"):
            wired.add(node.func.id)
    assert wired == expected
