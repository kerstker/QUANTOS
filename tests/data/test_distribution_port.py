from quantos.contracts.data import Bar
import pytest

from services.console.data_console import DefaultDataConsolePort
from services.data.persistent_store import SqlitePITStore


def _bar(event_time: int, knowledge_time: int, close: float) -> Bar:
    return Bar(
        instrument_id="BTC",
        event_time=event_time,
        knowledge_time=knowledge_time,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100.0,
    )


def test_distribution_port_extracts_pit_close_to_close_returns(tmp_path) -> None:
    path = tmp_path / "crypto_daily.db"
    store = SqlitePITStore(str(path), create=True)
    for event_time, close in enumerate((100.0, 101.0, 103.0, 102.0), start=1):
        store.add("BTC", _bar(event_time, event_time, close))
    store.add("BTC", _bar(3, 99, 104.0))

    series = DefaultDataConsolePort(path).enumerate_distribution_series()

    values = series["crypto_daily.BTC"]
    assert len(values) == 3
    assert values[0] == pytest.approx(0.01)
    assert values[1] == pytest.approx(104.0 / 101.0 - 1.0)
    assert values[2] == pytest.approx(102.0 / 104.0 - 1.0)


def test_distribution_port_fails_closed_when_store_is_absent(tmp_path) -> None:
    assert DefaultDataConsolePort(tmp_path / "missing.db").enumerate_distribution_series() == {}
