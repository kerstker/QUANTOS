from quantos.contracts.data import Bar
from services.data.persistent_store import SqlitePITStore
from tools.volume_semantics_evidence import build_volume_semantics_evidence


def _bar(instrument, event_time, close, volume):
    return Bar(
        instrument_id=instrument,
        event_time=event_time,
        knowledge_time=event_time,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
    )


def test_evidence_uses_quote_volume_without_double_multiplication(tmp_path):
    path = tmp_path / "daily.db"
    store = SqlitePITStore(str(path), create=True)
    store.add("BTC", _bar("BTC", 1, 50_000.0, 1_000_000_000.0))
    store.add("ETH", _bar("ETH", 1, 2_000.0, 500_000_000.0))

    evidence = build_volume_semantics_evidence(str(path))

    assert evidence["verdict"] == "PASS_LOCAL_SEMANTICS"
    assert evidence["source_contract"]["volume_unit"] == "QUOTE_USD"
    medians = evidence["corrected_quote_adv"]["median_by_instrument"]
    assert medians == {"BTC": 1_000_000_000.0, "ETH": 500_000_000.0}
    assert evidence["corrected_quote_adv"]["max_min_ratio"] == 2.0
    assert evidence["root_cause"]["legacy_median_max_min_ratio"] == 50.0
