from __future__ import annotations

import json


def test_institutional_search_route_is_read_only_diagnostic(tmp_path, monkeypatch):
    from services.console.api import ashare_views

    evidence = tmp_path / "search.json"
    evidence.write_text(
        json.dumps(
            {
                "evidence_status": "TECHNICAL_VALIDATION_ONLY",
                "candidate_usefulness": "NOT_VALIDATED",
                "generated_at": "2026-08-31T00:00:00+00:00",
                "source": {
                    "rows": 6_267_665,
                    "instruments": 5_549,
                    "first_date": 20210802,
                    "last_date": 20260828,
                    "sqlite_quick_check": "ok",
                    "future_known_rows": 0,
                    "bad_bar_count": 2_270,
                    "intake_contract": {
                        "historical_universe_semantics": (
                            "CURRENT_MASTER_ONLY_SURVIVOR_BIAS_PRESENT"
                        )
                    },
                },
                "trial_accounting": {"candidate_specs": 183},
                "requested_surface_coverage": {
                    "value": {
                        "status": "BLOCKED_MISSING_HISTORICAL_PIT",
                        "reason": "no historical valuation knowledge times",
                    }
                },
                "surface_confirmations": {
                    "capital_flow_proxy": {
                        "candidate_selected_without_confirmation": {
                            "name": "flow_h5",
                            "family": "signed_amount_flow",
                            "window": 5,
                            "skip": 0,
                            "hold_days": 5,
                        },
                        "stable_positive_before_confirmation": True,
                        "confirmation_cost_sensitivity": {
                            "30": {
                                "primary_phase": {
                                    "excess": {
                                        "annualized_sharpe": -0.2,
                                        "annualized_return": -0.01,
                                    }
                                },
                                "phase_robustness": {"median_excess_sharpe": -0.3},
                            }
                        },
                        "bonferroni_adjusted_p": 1.0,
                        "candidate_usefulness": "NOT_VALIDATED",
                    }
                },
                "untouched_forward_observation": {
                    "status": "ARMED_AWAITING_NEW_SESSIONS",
                    "freeze_cutoff": 20260828,
                },
                "limitations": ["survivor bias"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ashare_views, "_INSTITUTIONAL_SEARCH_PATH", evidence)

    payload = ashare_views._ashare_institutional_search_payload()

    assert payload["read_only"] is True
    assert payload["authoritative"] is False
    assert payload["diagnostic_only"] is True
    assert payload["evidence_status"] == "TECHNICAL_VALIDATION_ONLY"
    assert payload["source_summary"]["instruments"] == 5_549
    assert payload["surface_results"][0]["candidate_usefulness"] == "NOT_VALIDATED"
    assert payload["untouched_forward_observation"]["freeze_cutoff"] == 20260828
