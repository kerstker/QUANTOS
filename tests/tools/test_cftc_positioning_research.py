from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from tools import cftc_positioning_research as research


def test_preregistered_family_has_six_fixed_institutional_positioning_angles():
    specs = research.build_variant_specs()

    assert len(specs) == 6 == research.PRE_REGISTRATION["trial_count_planned"]
    assert len({spec["id"] for spec in specs}) == 6
    assert {spec["target"] for spec in specs} == {"BTC", "ETH", "ETH_BTC_RELATIVE"}
    assert all(spec["window_weeks"] == 52 for spec in specs)
    assert all(spec["horizon_weeks"] == 1 for spec in specs)


def test_dataset_to_periods_preserves_two_asset_honest_breadth():
    aligned = [
        {"entry_time_ms": 1_000 + i, "signal": (-1.0) ** i,
         "asset_return": 0.01 * ((i % 3) - 1)}
        for i in range(10)
    ]

    btc_periods = research.panel_periods_for("BTC", aligned)
    relative_periods = research.panel_periods_for("ETH_BTC_RELATIVE", aligned)

    assert btc_periods[0].instruments == ("BTC", "CASH_USD")
    assert relative_periods[0].instruments == ("ETH", "BTC")
    assert all(len(period.instruments) == 2 for period in btc_periods + relative_periods)


def test_registration_records_trials_but_never_grants_promotion(monkeypatch):
    specs = research.build_variant_specs()
    aligned_by_id = {
        spec["id"]: [
            {"entry_time_ms": 1_000 + i, "signal": (-1.0) ** i,
             "asset_return": 0.01 * ((i % 3) - 1)}
            for i in range(24)
        ]
        for spec in specs
    }
    registry = ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))

    from services.intelligence import registered_candidate_batch as batch_module
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
    result = research._register_family_trials_legacy_diagnostic(
        specs,
        aligned_by_id,
        registry=registry,
        recorded_at=10_000,
        dataset_version="dataset-v1",
        batch_runner=batch_module._run_registered_candidate_batch_legacy_diagnostic,
    )

    assert result["trial_count_before"] == 0
    assert result["trial_count_after"] == 6
    assert result["promotion_eligible"] is False
    assert result["live_allowed"] is False
