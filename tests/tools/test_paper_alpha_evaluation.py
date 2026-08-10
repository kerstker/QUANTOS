from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from services.research.alpha_expression import FieldPanel
from tools import paper_alpha_evaluation as driver


def _panel(n_periods: int = 42, n_instruments: int = 12) -> FieldPanel:
    instruments = tuple(f"COIN{i}" for i in range(n_instruments))
    event_times = tuple(1_700_000_000_000 + row * 86_400_000 for row in range(n_periods))
    close = []
    funding = []
    global_ls = []
    top_ls = []
    for row in range(n_periods):
        close.append(
            [
                100.0 + row * 0.2 + column * 0.4 + ((row + column) % 3) * 0.1
                for column in range(n_instruments)
            ]
        )
        funding.append([0.0001 * ((column % 5) - 2) for column in range(n_instruments)])
        global_ls.append([1.0 + 0.1 * ((row + column) % 7) for column in range(n_instruments)])
        top_ls.append([0.8 + 0.08 * ((2 * row + column) % 9) for column in range(n_instruments)])
    return FieldPanel(
        instruments,
        event_times,
        {
            "close": close,
            "funding": funding,
            "global_ls": global_ls,
            "top_ls": top_ls,
        },
    )


def test_build_paper_candidates_uses_content_addressed_research_identities() -> None:
    candidates, evidence, mappings = driver.build_paper_candidates(_panel())

    assert set(mappings) == {"carry_oi_majors", "crowd_contra", "top_contra"}
    assert len(candidates) == 3
    assert set(evidence) == {
        mapping["research_alpha_id"] for mapping in mappings.values()
    }
    for definition, periods in candidates:
        deployed = next(
            name
            for name, mapping in mappings.items()
            if mapping["research_alpha_id"] == definition.alpha_id
        )
        assert definition.alpha_id.startswith(f"paper_{deployed}_")
        assert definition.definition_hash == mappings[deployed]["definition_hash"]
        assert periods
        assert "leakage_ok" in evidence[definition.alpha_id]


def test_build_paper_candidates_evaluates_carry_on_funding_adjusted_payoff() -> None:
    panel = _panel()
    candidates, _evidence, mappings = driver.build_paper_candidates(panel)
    by_research_id = {definition.alpha_id: periods for definition, periods in candidates}
    carry_periods = by_research_id[mappings["carry_oi_majors"]["research_alpha_id"]]
    crowd_periods = by_research_id[mappings["crowd_contra"]["research_alpha_id"]]
    specs = {deployed: expression for deployed, _name, expression, _fields in driver._paper_specs()}

    price_only_carry = driver.ax.panel_to_periods(
        driver.ax.evaluate(specs["carry_oi_majors"], panel),
        panel,
        fwd_horizon=driver.FWD_HORIZON,
        non_overlapping=True,
    )
    price_only_crowd = driver.ax.panel_to_periods(
        driver.ax.evaluate(specs["crowd_contra"], panel),
        panel,
        fwd_horizon=driver.FWD_HORIZON,
        non_overlapping=True,
    )

    assert mappings["carry_oi_majors"]["return_basis"] == "PRICE_PLUS_FUNDING"
    assert mappings["crowd_contra"]["return_basis"] == "PRICE"
    assert carry_periods != price_only_carry
    assert crowd_periods == price_only_crowd

    first = carry_periods[0]
    row = panel.event_times.index(first.event_time)
    for instrument, observed in zip(first.instruments, first.forward_returns, strict=True):
        column = panel.instruments.index(instrument)
        price_return = (
            panel.matrices["close"][row + driver.FWD_HORIZON][column]
            / panel.matrices["close"][row][column]
            - 1.0
        )
        accrued_funding = sum(
            panel.matrices["funding"][future_row][column]
            for future_row in range(row + 1, row + driver.FWD_HORIZON + 1)
        )
        assert observed == price_return - accrued_funding


def test_evaluation_dataset_version_binds_contract_and_panel() -> None:
    panel = _panel()
    baseline = driver.evaluation_dataset_version(panel)
    assert baseline == driver.evaluation_dataset_version(panel)

    changed = _panel()
    changed.matrices["top_ls"][0][0] += 0.5
    assert driver.evaluation_dataset_version(changed) != baseline


def test_run_freezes_search_clock_after_expensive_candidate_assembly(
    tmp_path, monkeypatch
) -> None:
    order: list[str] = []
    panel = _panel()

    def build_panel():
        order.append("panel")
        return panel, list(panel.instruments)

    def build_candidates(_panel):
        order.append("candidates")
        return [], {}, {}

    def time_ns():
        order.append("clock")
        return 123_000_000

    def run_batch(*_args, **kwargs):
        order.append("batch")
        assert kwargs["recorded_at"] == 123
        return {"recorded_run_ids": []}

    monkeypatch.setattr("tools.crypto_oi_research.build_panel", build_panel)
    monkeypatch.setattr(driver, "build_paper_candidates", build_candidates)
    monkeypatch.setattr(driver, "evaluation_dataset_version", lambda _panel: "dataset-v1")
    monkeypatch.setattr(driver.time, "time_ns", time_ns)
    monkeypatch.setattr(
        driver,
        "production_experiment_registry",
        lambda: nullcontext(SimpleNamespace()),
    )
    monkeypatch.setattr(driver, "run_registered_candidate_batch", run_batch)

    driver.run(output=tmp_path / "paper.json")

    assert order == ["panel", "candidates", "clock", "batch"]


def test_forward_run_freezes_search_clock_after_candidate_assembly(
    tmp_path, monkeypatch
) -> None:
    order: list[str] = []
    panel = _panel()

    monkeypatch.setattr(
        "tools.crypto_oi_research.build_panel",
        lambda: (order.append("panel") or (panel, list(panel.instruments))),
    )
    monkeypatch.setattr(
        driver,
        "build_forward_candidates",
        lambda _panel: (order.append("candidates") or ([], {}, {})),
    )
    monkeypatch.setattr(driver, "evaluation_dataset_version", lambda _panel: "dataset-v1")
    monkeypatch.setattr(
        driver.time,
        "time_ns",
        lambda: (order.append("clock") or 456_000_000),
    )
    monkeypatch.setattr(
        driver,
        "production_experiment_registry",
        lambda: nullcontext(SimpleNamespace()),
    )

    def run_batch(*_args, **kwargs):
        order.append("batch")
        assert kwargs["recorded_at"] == 456
        return {"recorded_run_ids": []}

    monkeypatch.setattr(driver, "run_registered_candidate_batch", run_batch)

    driver.run_forward(output=tmp_path / "forward.json")

    assert order == ["panel", "candidates", "clock", "batch"]
