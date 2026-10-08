from services.deployment.runtime import evaluate_and_record_deployment
from services.deployment.store import DeploymentDecisionStore
from quantos.contracts.governance import Environment
from tools.deployment_events import dispatch_and_project_deployment_events


def test_deployment_outbox_tool_dispatches_and_projects_without_applying(tmp_path):
    state_path = str(tmp_path / "deployment.db")
    store = DeploymentDecisionStore(state_path, create=True)
    decision = evaluate_and_record_deployment(
        store,
        Environment.LIVE,
        subject="live-denied",
        at=100,
    )
    assert decision.allowed is False

    summary = dispatch_and_project_deployment_events(
        store,
        state_path=state_path,
        now=101,
        worker_id="deployment-event-worker",
    )
    assert summary["dispatch"] == {"dispatched": 1, "pending": 0}
    assert summary["projection"]["projected"] == 1
    assert store.pending_event_outbox() == ()
