from scripts.generate_model_risk_evidence import main as generate_model_risk
from scripts.update_model_risk_history import main as update_model_risk_history
from tools.search_trial_ledger_history import capture_history, check_history


def test_refresh_real_evidence_once():
    assert generate_model_risk([]) == 0
    assert update_model_risk_history([]) == 0
    for session_id in ("alpha-trial-session-1", "alpha-trial-session-2"):
        capture_history(
            session_id=session_id,
            ledger_path="data/cache/experiment_registry.db",
            history_path="evidence/alpha_trial_ledger_history.json",
            ledger_path_label="data/cache/experiment_registry.db",
        )
    assert len(check_history(
        ledger_path="data/cache/experiment_registry.db",
        history_path="evidence/alpha_trial_ledger_history.json",
        ledger_path_label="data/cache/experiment_registry.db",
    )["records"]) >= 2
