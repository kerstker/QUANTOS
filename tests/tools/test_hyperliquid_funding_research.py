from services.intelligence import registered_candidate_batch as batch_module
from services.research.alpha_expression import FieldPanel, evaluate
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from tools import hyperliquid_funding_research as hfr


DAY_MS = 86_400_000


def _base_panel(days=40, instruments=("BTC", "ETH", "SOL")):
    event_times = tuple((index + 1) * DAY_MS for index in range(days))
    close = [
        [100.0 + 20 * column + index * (0.2 + column * 0.01)
         for column in range(len(instruments))]
        for index in range(days)
    ]
    volume = [[1_000_000.0 + column * 10_000 for column in range(len(instruments))]
              for _ in range(days)]
    funding = [[0.0001 * ((index + column) % 3 - 1)
                for column in range(len(instruments))] for index in range(days)]
    xfunding = [[0.00008 * ((index - column) % 3 - 1)
                 for column in range(len(instruments))] for index in range(days)]
    return FieldPanel(instruments, event_times, {
        "close": close, "volume": volume,
        "funding": funding, "xfunding": xfunding,
    })


def _snapshot(panel):
    series = {}
    for column, coin in enumerate(panel.instruments):
        series[coin] = {
            str(event_time): {
                "funding_sum": 0.00005 * ((row + column) % 5 - 2),
                "premium_mean": 0.00003 * ((2 * row + column) % 5 - 2),
                "observation_count": 24,
            }
            for row, event_time in enumerate(panel.event_times)
        }
    return {
        "provenance": "REAL_HYPERLIQUID_PUBLIC_MARKET_HISTORY",
        "pit_admissible_for_research": True,
        "series": series,
        "errors": {},
    }


def test_preregistered_family_has_six_fixed_new_angle_hypotheses():
    specs = hfr.build_variant_specs()
    assert len(specs) == 6 == hfr.PRE_REGISTRATION["trial_count_planned"]
    assert len({spec["id"] for spec in specs}) == 6
    assert {spec["id"] for spec in specs} >= {
        "hl_cex_spread_z_rev", "hl_cex_spread_z_mom",
        "three_venue_consensus_carry", "hl_premium_z_rev",
    }
    assert all(spec["rationale"].strip() for spec in specs)
    assert all(spec["quantile"] == 0.30 and spec["cadence_days"] == 7
               for spec in specs)


def test_enriched_panel_aligns_complete_daily_hyperliquid_fields():
    base = _base_panel(days=3, instruments=("BTC", "ETH"))
    panel, coverage = hfr.enrich_panel_with_hyperliquid(
        base, _snapshot(base), min_instruments=2,
    )

    assert panel.event_times == base.event_times
    assert panel.matrices["hlfunding"][0] == [-0.0001, -0.00005]
    assert panel.matrices["hlpremium"][0] == [-0.00006, -0.00003]
    assert coverage["eligible_days"] == 3
    assert coverage["minimum_cross_section"] == 2


def test_subset_universes_exclude_base_assets_without_hyperliquid_history():
    base = _base_panel(instruments=("AAVE", "BTC", "ETH", "SOL"))
    source_panel = _base_panel(instruments=("BTC", "ETH", "SOL"))
    snapshot = _snapshot(source_panel)
    panel, _coverage = hfr.enrich_panel_with_hyperliquid(
        base, snapshot, min_instruments=3,
    )

    assert hfr.hyperliquid_instruments(panel, snapshot) == ("BTC", "ETH", "SOL")


def test_all_preregistered_expressions_evaluate_on_enriched_panel():
    base = _base_panel()
    panel, _coverage = hfr.enrich_panel_with_hyperliquid(
        base, _snapshot(base), min_instruments=3,
    )
    for spec in hfr.build_variant_specs():
        signal = evaluate(spec["expr"], panel)
        assert len(signal) == len(panel.event_times)
        assert len(signal[0]) == len(panel.instruments)


def test_formal_periods_share_one_calendar_anchor_for_batch_pbo():
    base = _base_panel(days=100)
    panel, _coverage = hfr.enrich_panel_with_hyperliquid(
        base, _snapshot(base), min_instruments=3,
    )
    specs = hfr.build_variant_specs()
    signals = {spec["id"]: evaluate(spec["expr"], panel) for spec in specs}

    periods_by_id = hfr.fixed_weekly_periods(specs, signals, panel)
    common = set.intersection(*(
        {period.event_time for period in periods}
        for periods in periods_by_id.values()
    ))

    assert len(common) >= 4
    assert all(
        all(b.event_time - a.event_time >= 7 * DAY_MS for a, b in zip(periods, periods[1:]))
        for periods in periods_by_id.values()
    )


def test_declared_budget_matches_family_and_third_venue_source():
    specs = hfr.build_variant_specs()
    assert hfr.RESEARCH_BUDGET.max_trials == len(specs)
    assert hfr.RESEARCH_BUDGET.max_feature_count == 3
    assert hfr.RESEARCH_BUDGET.allowed_data_sources == (
        "crypto_funding.db+bybit_perp.db+hyperliquid_public_market.json",
    )


def test_registration_path_records_six_trials_without_promotion(monkeypatch):
    monkeypatch.setattr(
        batch_module.ResearchAgentLoop,
        "run",
        lambda _self, received, **_kwargs: {
            "report": {
                definition.alpha_id: {"state": None}
                for definition, _periods in received
            },
            "human_review_queue": [],
        },
    )
    base = _base_panel()
    panel, _coverage = hfr.enrich_panel_with_hyperliquid(
        base, _snapshot(base), min_instruments=3,
    )
    specs = hfr.build_variant_specs()
    signals = {spec["id"]: evaluate(spec["expr"], panel) for spec in specs}
    registry = ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))

    result = hfr.register_family_trials(
        specs, signals, panel, registry=registry, recorded_at=1_000,
    )

    assert result["trial_count_before"] == 0
    assert result["trial_count_after"] == 6
    assert result["budget_declaration"]["data_source"] == (
        "crypto_funding.db+bybit_perp.db+hyperliquid_public_market.json"
    )
    assert result["promotion_eligible"] is False
    assert result["live_allowed"] is False
