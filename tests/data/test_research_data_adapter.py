from quantos.contracts.data import Bar
from services.data.versioning import content_hash_of


def _bar(instrument_id: str, event_time: int, knowledge_time: int) -> Bar:
    return Bar(
        instrument_id=instrument_id,
        event_time=event_time,
        knowledge_time=knowledge_time,
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=100.0,
    )


class _RawStore:
    def __init__(self) -> None:
        self.bars = {
            "A": [_bar("A", 1, 1), _bar("A", 2, 3)],
            "B": [_bar("B", 1, 1)],
        }
        self.fetch_calls = []

    def as_of(self, key, as_of_time, event_time_max=None):
        return [
            bar
            for bar in self.bars.get(key, ())
            if bar.knowledge_time <= as_of_time and (event_time_max is None or bar.event_time <= event_time_max)
        ]

    def latest_as_of(self, key, as_of_time, event_time_max=None):
        rows = self.as_of(key, as_of_time, event_time_max)
        return rows[-1] if rows else None

    def fetch_bars(self, key, start, end):
        self.fetch_calls.append((key, start, end))
        return [bar for bar in self.bars.get(key, ()) if start <= bar.event_time <= end]


def test_adapter_preserves_pit_reads_and_derives_content_version():
    from services.data.research_adapter import PITResearchDataAdapter
    from services.research.ports.market_data import VersionedMarketDataPort

    raw = _RawStore()
    port = PITResearchDataAdapter(raw)

    assert isinstance(port, VersionedMarketDataPort)
    assert port.as_of("A", 1) == [raw.bars["A"][0]]
    assert port.latest_as_of("A", 3) == raw.bars["A"][-1]
    assert port.dataset_version(["A", "B"], [1, 2]) == content_hash_of([*raw.bars["A"], *raw.bars["B"]])
    assert raw.fetch_calls == [("A", 1, 2), ("B", 1, 2)]


def test_adapter_empty_event_grid_is_empty_version_without_raw_read():
    from services.data.research_adapter import PITResearchDataAdapter

    raw = _RawStore()
    assert PITResearchDataAdapter(raw).dataset_version(["A"], []) == ""
    assert raw.fetch_calls == []


def test_adapter_fails_closed_when_raw_store_contract_is_incomplete():
    from services.data.research_adapter import PITResearchDataAdapter

    class MissingRawWindow:
        def as_of(self, *_args, **_kwargs):
            return []

        def latest_as_of(self, *_args, **_kwargs):
            return None

    try:
        PITResearchDataAdapter(MissingRawWindow())
    except TypeError as exc:
        assert "fetch_bars" in str(exc)
    else:
        raise AssertionError("incomplete market-data source must be rejected")


def test_adapter_factory_does_not_wrap_an_existing_versioned_port():
    from services.data.research_adapter import adapt_research_market_data

    class ExistingPort(_RawStore):
        def dataset_version(self, instruments, event_times):
            return "already-versioned"

    existing = ExistingPort()
    assert adapt_research_market_data(existing) is existing
