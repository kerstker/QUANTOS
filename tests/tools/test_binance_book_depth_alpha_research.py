from __future__ import annotations

import json

from services.data.binance_book_depth_archive import DATASET, DAY_MS, HOUR_MS
from services.data.binance_systemic_store import BinanceSystemicStore
from tools import binance_book_depth_alpha_research as research


def _seed(store_path, contract, *, days: int, observed_at_ms: int) -> None:
    store = BinanceSystemicStore(str(store_path), create=True)
    try:
        rows = []
        for day in range(days):
            for hour in range(1, 25):
                event_time = day * DAY_MS + hour * HOUR_MS
                for index, instrument in enumerate(contract["universe"]):
                    score = (index - 6.5) / 20.0
                    rows.append(
                        {
                            "dataset": DATASET,
                            "instrument_id": instrument,
                            "event_time_ms": event_time,
                            "knowledge_time_ms": observed_at_ms,
                            "external_price_alignment": "PASS",
                            "external_price_reference": {
                                "reference_price": 100.0 + day + hour / 24 + index
                            },
                            "levels": {
                                radius: {
                                    "bid_notional_p10": 1000 * float(radius) * (1 + score),
                                    "ask_notional_p10": 1000 * float(radius) * (1 - score),
                                }
                                for radius in ("0.2", "1", "2", "3", "4", "5")
                            },
                        }
                    )
        store.append_many(rows)
    finally:
        store.close()


def test_driver_reports_insufficient_history_without_evaluating(tmp_path, monkeypatch):
    contract = json.loads(research.DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    store_path = tmp_path / "depth.db"
    output = tmp_path / "report.json"
    _seed(store_path, contract, days=2, observed_at_ms=100 * DAY_MS)
    monkeypatch.setattr(
        research,
        "_verify_preregistration",
        lambda _path: (research.PREREGISTRATION_COMMIT, "a" * 64, contract),
    )
    monkeypatch.setattr(research, "_git", lambda *_args: "b" * 40)
    monkeypatch.setattr(research, "_require_clean_source", lambda: "b" * 40)

    result = research.run_research(
        store_path=store_path,
        output_path=output,
        generated_at_ms=100 * DAY_MS,
    )

    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert result["observed_common_days"] == 2
    assert result["formal_admission"] is False
    assert result["testnet_authorized"] is False
    assert result["input_observation_count"] == 672
    assert len(result["input_observation_sha256"]) == 64
    assert result["authority_head"]["seq"] == 672
    assert json.loads(output.read_text(encoding="utf-8")) == result


def test_real_preregistration_commit_and_worktree_contract_match() -> None:
    commit, digest, payload = research._verify_preregistration(
        research.DEFAULT_CONTRACT
    )

    assert commit == research.PREREGISTRATION_COMMIT
    assert len(digest) == 64
    assert payload["status"] == "PRE_REGISTERED_NOT_EVALUATED"
