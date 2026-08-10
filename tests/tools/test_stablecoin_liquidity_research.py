from tools import stablecoin_liquidity_research as research


DAY_MS = 86_400_000


def _snapshot(days=500):
    start = 10_000 * DAY_MS
    return {
        "provenance": "REAL_DEFILLAMA_GLOBAL_STABLECOIN_HISTORY",
        "revision_policy": "RETROSPECTIVE_REVISABLE_NO_VINTAGES",
        "pit_admissible_for_promotion": False,
        "series": {
            str(start + index * DAY_MS): 100.0 + index + 0.01 * index * index
            for index in range(days)
        },
    }


def test_family_has_six_fixed_liquidity_impulse_associations():
    specs = research.build_variant_specs()
    assert len(specs) == 6
    assert len({spec["id"] for spec in specs}) == 6
    assert {spec["lookback_days"] for spec in specs} == {7, 30, 90}
    assert all(spec["z_window_days"] == 365 for spec in specs)
    assert all(spec["horizon_days"] == 7 for spec in specs)


def test_historical_observations_are_fixed_weekday_and_do_not_claim_pit():
    spec = research.build_variant_specs()[0]
    snapshot = _snapshot()

    observations = research.historical_signal_observations(spec, snapshot)

    assert len(observations) > 10
    assert all((timestamp // DAY_MS) % 7 == research.WEEKDAY_UTC for timestamp, _ in observations)
    assert all(-1.0 <= signal <= 1.0 for _timestamp, signal in observations)


def test_snapshot_contract_refuses_fabricated_pit_flag():
    snapshot = _snapshot()
    snapshot["pit_admissible_for_promotion"] = True
    try:
        research.historical_signal_observations(research.build_variant_specs()[0], snapshot)
    except ValueError as exc:
        assert "snapshot contract" in str(exc)
    else:
        raise AssertionError("fabricated PIT snapshot must fail closed")

