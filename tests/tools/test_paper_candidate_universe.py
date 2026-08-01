import json

from tools.paper_candidate_universe import STATIC_ALPHA_IDS, paper_candidate_ids


def test_candidate_universe_unions_forward_and_evaluation_artifacts(tmp_path):
    status = tmp_path / "status.json"
    evaluations = tmp_path / "evaluations.json"
    status.write_text(
        json.dumps(
            {
                "alphas": {
                    "z_variant": {"n_forward_periods": 1},
                    "crowd_contra_q20": {"n_forward_periods": 1},
                }
            }
        ),
        encoding="utf-8",
    )
    evaluations.write_text(
        json.dumps({"alphas": {"a_variant": {"status": "VERIFIED"}}}),
        encoding="utf-8",
    )

    assert paper_candidate_ids(
        forward_status=status,
        evaluations=evaluations,
    ) == STATIC_ALPHA_IDS + ("a_variant", "crowd_contra_q20", "z_variant")


def test_candidate_universe_falls_back_to_static_without_optional_artifacts(tmp_path):
    assert paper_candidate_ids(
        forward_status=tmp_path / "missing-status.json",
        evaluations=tmp_path / "missing-evaluations.json",
    ) == STATIC_ALPHA_IDS
