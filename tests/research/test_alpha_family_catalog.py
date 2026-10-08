from services.research.alpha_family_catalog import ALPHA_FAMILY_SPECS, spec_by_candidate


def test_catalog_contains_exactly_the_ten_requested_families():
    assert len(ALPHA_FAMILY_SPECS) == 10
    assert len({s.family_id for s in ALPHA_FAMILY_SPECS}) == 10
    assert len({s.candidate_id for s in ALPHA_FAMILY_SPECS}) == 10


def test_frequency_and_data_boundaries_are_explicit():
    term = spec_by_candidate("term_structure_spread")
    assert term.expression is None
    assert term.evaluation_frequency == "unavailable"
    assert "term_structure" in term.required_fields

    mr = spec_by_candidate("mean_reversion_1d_proxy")
    assert mr.evaluation_frequency == "daily_proxy"
    assert mr.intended_frequency == "分钟/小时"


def test_expressions_are_immutable_tuples():
    for spec in ALPHA_FAMILY_SPECS:
        if spec.expression is not None:
            assert isinstance(spec.expression, tuple)
