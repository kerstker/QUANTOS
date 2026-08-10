from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from services.research.alpha_expression import FieldPanel
from tools import positioning_composite_evaluation as common
from tools import positioning_fast_composite_evaluation as driver


def _panel(n_periods: int = 64, n_instruments: int = 12) -> FieldPanel:
    instruments = tuple(f"COIN{index}" for index in range(n_instruments))
    event_times = tuple(
        1_700_000_000_000 + row * 86_400_000 for row in range(n_periods)
    )
    return FieldPanel(
        instruments,
        event_times,
        {
            "close": [
                [100.0 + row * 0.2 + column * 0.4 for column in range(n_instruments)]
                for row in range(n_periods)
            ],
            "global_ls": [
                [1.0 + 0.1 * ((row + column) % 7) for column in range(n_instruments)]
                for row in range(n_periods)
            ],
            "top_ls": [
                [0.8 + 0.08 * ((2 * row + column) % 9) for column in range(n_instruments)]
                for row in range(n_periods)
            ],
        },
    )


def test_fast_family_is_fixed_three_day_and_distinct_from_weekly_identity() -> None:
    panel = _panel()
    for row in range(5, 11):
        panel.matrices["top_ls"][row] = [None] * len(panel.instruments)
    fast, evidence, mappings = driver.build_candidate_batch(panel)
    weekly, _weekly_evidence, weekly_mappings = common.build_candidate_batch(panel)

    assert driver.FWD_HORIZON == 3
    assert driver.PERIODS_PER_YEAR == 122
    assert len(fast) == len(weekly) == 3
    assert all(periods for _definition, periods in fast)
    assert all(
        len(fast_periods) > len(weekly_periods)
        for (_fast_definition, fast_periods), (_weekly_definition, weekly_periods)
        in zip(fast, weekly, strict=True)
    )
    assert set(evidence) == {
        mapping["research_alpha_id"] for mapping in mappings.values()
    }
    assert {
        mapping["definition_hash"] for mapping in mappings.values()
    }.isdisjoint(
        mapping["definition_hash"] for mapping in weekly_mappings.values()
    )
    fast_calendars = [
        tuple(period.event_time for period in periods)
        for _definition, periods in fast
    ]
    assert len(set(fast_calendars)) == 1
    assert all(
        later - earlier >= driver.FWD_HORIZON * 86_400_000
        for earlier, later in zip(
            fast_calendars[0], fast_calendars[0][1:]
        )
    )


def test_fast_run_registers_complete_family_and_never_grants_testnet(
    tmp_path, monkeypatch
) -> None:
    captured = {}
    fake_registry = SimpleNamespace(get_evaluation=lambda _run_id: None)

    def run_batch(candidates, **kwargs):
        captured["candidates"] = tuple(candidates)
        captured.update(kwargs)
        return {"recorded_run_ids": [], "registered_candidate_count": 3}

    monkeypatch.setattr(common, "run_registered_candidate_batch", run_batch)
    monkeypatch.setattr(
        common,
        "production_experiment_registry",
        lambda: nullcontext(fake_registry),
    )

    document = driver.run(
        output=tmp_path / "fast.json",
        recorded_at=123,
        source_commit="a" * 40,
        panel=_panel(),
    )

    assert len(captured["candidates"]) == 3
    assert captured["periods_per_year"] == 122
    assert captured["budget"].max_trials == 3
    assert captured["data_source"] == driver.DATA_SOURCE
    assert captured["code_hash"] == "a" * 40
    assert document["schema_version"] == "positioning_fast_composite_evaluation.v1"
    assert document["decision"]["forward_admission_allowed"] is False
    assert document["decision"]["testnet_eligible"] is False
    assert document["safety"]["order_submission_allowed"] is False
