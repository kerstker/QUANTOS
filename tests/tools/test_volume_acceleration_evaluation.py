from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from services.research.alpha_expression import FieldPanel
from tools import volume_acceleration_evaluation as driver


def _panel(n_periods: int = 80, n_instruments: int = 12) -> FieldPanel:
    instruments = tuple(f"COIN{index}" for index in range(n_instruments))
    event_times = tuple(
        1_700_000_000_000 + row * driver.DAY_MS for row in range(n_periods)
    )
    close = [
        [
            100.0 + row * 0.1 + column * 0.3 + ((row + column) % 5) * 0.05
            for column in range(n_instruments)
        ]
        for row in range(n_periods)
    ]
    volume = [
        [
            1_000_000.0
            + row * (100.0 + column * 5.0)
            + ((row * (column + 1)) % 11) * 1_000.0
            for column in range(n_instruments)
        ]
        for row in range(n_periods)
    ]
    return FieldPanel(instruments, event_times, {"close": close, "volume": volume})


def test_exact_legacy_finalist_family_is_shared_calendar_and_preselects_subject() -> None:
    candidates, evidence, mappings = driver.build_candidate_batch(_panel())

    assert len(candidates) == len(driver.FINALIST_EXPRESSIONS) == 14
    assert driver.SUBJECT_ALIAS == "volume_acceleration_30_7"
    assert driver.FINALIST_EXPRESSIONS[driver.SUBJECT_ALIAS] == (
        "cs_zscore",
        ("cs_scale", ("ts_delta", ("ts_delta", ("field", "volume"), 30), 7)),
    )
    calendars = [
        tuple(period.event_time for period in periods)
        for _definition, periods in candidates
    ]
    assert len(set(calendars)) == 1
    assert calendars[0]
    assert all(
        later - earlier >= driver.DAY_MS
        for earlier, later in zip(calendars[0], calendars[0][1:])
    )
    assert set(evidence) == {
        mapping["research_alpha_id"] for mapping in mappings.values()
    }
    assert all(item["leakage_ok"] is True for item in evidence.values())
    subject = next(
        definition
        for definition, _periods in candidates
        if definition.name == driver.SUBJECT_ALIAS
    )
    assert subject.factor_ids == ("volume",)


def test_confirmatory_run_registers_closed_family_and_never_grants_testnet(
    tmp_path, monkeypatch
) -> None:
    captured = {}
    fake_registry = SimpleNamespace(get_evaluation=lambda _run_id: None)

    def run_batch(candidates, **kwargs):
        captured["candidates"] = tuple(candidates)
        captured.update(kwargs)
        return {"recorded_run_ids": [], "registered_candidate_count": 14}

    monkeypatch.setattr(driver, "run_registered_candidate_batch", run_batch)
    monkeypatch.setattr(
        driver,
        "production_experiment_registry",
        lambda: nullcontext(fake_registry),
    )

    document = driver._run_legacy_diagnostic(
        output=tmp_path / "volume.json",
        recorded_at=123,
        source_commit="a" * 40,
        panel=_panel(),
    )

    assert len(captured["candidates"]) == 14
    assert captured["budget"].max_trials == 14
    assert captured["quantile"] == 0.30
    assert captured["periods_per_year"] == 252
    assert captured["cost"] == driver.EVALUATION_COST
    assert captured["data_source"] == driver.DATA_SOURCE
    assert document["status"] == "PENDING"
    assert document["decision"]["formal_evaluation_pass"] is False
    assert document["decision"]["testnet_eligible"] is False
    assert document["safety"]["order_submission_allowed"] is False
