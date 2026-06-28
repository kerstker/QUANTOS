"""indexer: map the existing cached result JSONs into ResearchRun records, so every backtest already
run shows up in the research-ops console on day one (deterministic; timestamp injected)."""
import json

from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.indexer import index_existing, adapt_carry, adapt_combo


def test_adapt_carry_extracts_headline_and_series():
    j = {"n_instruments": 115, "n_days": 2186, "sharpe_net_5bps": 2.03, "avg_funding_bps_day": 5.43,
         "sharpe_price_plus_funding": 2.25, "sharpe_price_only": 1.39, "avg_turnover": 0.28}
    out = adapt_carry(j)
    assert out["metrics"]["net_sharpe_5bps"] == 2.03          # headline (first metric)
    assert out["verdict"] and out["caveats"]
    labels = [p["label"] for p in out["series"]["carry_buildup"]]
    assert "@5bps" in labels


def test_adapt_combo_per_year_series():
    j = {"combo_econ_sharpe": 1.5, "combo_max_drawdown": 0.12, "lift_vs_best_alone": 0.23,
         "combo_per_year": {"2021": 1.62, "2022": 2.2}, "combo_net_sharpe_by_fee": {"10": 1.02},
         "verdict": "diversification helps", "universe": ["BTC", "ETH"], "n_days": 2199}
    out = adapt_combo(j)
    assert out["metrics"]["combo_sharpe"] == 1.5
    yrs = {p["label"]: p["value"] for p in out["series"]["per_year"]}
    assert yrs["22"] == 2.2


def test_index_existing_writes_records(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "crypto_carry_strategy.json").write_text(json.dumps(
        {"sharpe_net_5bps": 2.03, "avg_funding_bps_day": 5.43, "n_instruments": 115, "n_days": 2186,
         "sharpe_price_plus_funding": 2.25, "sharpe_price_only": 1.39, "avg_turnover": 0.28}))
    (cache / "crypto_combo_book.json").write_text(json.dumps(
        {"combo_econ_sharpe": 1.5, "combo_max_drawdown": 0.12, "combo_per_year": {"2022": 2.2},
         "combo_net_sharpe_by_fee": {"10": 1.02}, "lift_vs_best_alone": 0.23, "verdict": "ok"}))
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    n = index_existing(str(cache), reg, now=1000)
    assert n == 2
    carry = reg.list(kind="carry_strategy")
    assert len(carry) == 1 and carry[0].status == "done" and carry[0].created_ts == 1000
    assert carry[0].metrics["net_sharpe_5bps"] == 2.03


def test_index_skips_missing_files(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()                                            # empty: no result JSONs
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    assert index_existing(str(cache), reg, now=1) == 0
