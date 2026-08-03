from __future__ import annotations

from quantos.contracts.hashing import stable_hash
from services.research import alpha_expression as ax
from services.research.paper_candidate_registry import PaperCandidateRegistry
from tools.alpha_candidate_admission import admit_factory_candidates
from tools.alpha_candidate_admission import run


EXPRESSION = ("cs_rank", ("neg", ("field", "oi_value")))
ALPHA_ID = ax.alpha_id(EXPRESSION)
DEFINITION_HASH = stable_hash({"expression": ax.serialize(EXPRESSION), "quantile": 0.3, "cadence_days": 7})


class FakeExperimentRegistry:
    def __init__(self, *, verified: bool = True, verdict: str = "PASS") -> None:
        self.verified = verified
        self.row = {
            "alpha_id": ALPHA_ID,
            "search_id": "search-1",
            "dataset_version": "dataset-1",
            "config_hash": DEFINITION_HASH,
            "verdict": verdict,
            "recorded_ts": 1234,
        }

    def verify_evaluation(self, run_id: str) -> bool:
        return self.verified and run_id == "run-1"

    def get_evaluation(self, run_id: str):
        return dict(self.row) if run_id == "run-1" else None


def _document(**candidate_updates: object) -> dict:
    candidate = {
        "alpha_id": ALPHA_ID,
        "expr": ax.serialize(EXPRESSION),
        "definition_hash": DEFINITION_HASH,
        "source_search_id": "search-1",
        "evaluation_run_id": "run-1",
        "dataset_version": "dataset-1",
        "quantile": 0.3,
        "cadence_days": 7,
    }
    candidate.update(candidate_updates)
    return {"schema_version": 2, "eligible_discoveries": [candidate]}


def test_verified_pass_is_admitted_idempotently_to_forward_paper(tmp_path) -> None:
    with PaperCandidateRegistry(tmp_path / "candidates.db") as registry:
        first = admit_factory_candidates(
            _document(),
            candidate_registry=registry,
            experiment_registry=FakeExperimentRegistry(),
            admitted_at_ms=2000,
        )
        replay = admit_factory_candidates(
            _document(),
            candidate_registry=registry,
            experiment_registry=FakeExperimentRegistry(),
            admitted_at_ms=3000,
        )

        assert first["status"] == "PASS"
        assert first["admitted"] == [ALPHA_ID]
        assert replay["status"] == "PASS"
        assert replay["replayed"] == [ALPHA_ID]
        assert registry.all()[0].admitted_at_ms == 1234
        assert registry.chain_valid() is True
        assert first["safety"]["lifecycle_promotion"] is False
        assert first["safety"]["live_allowed"] is False


def test_unverified_non_pass_and_binding_mismatch_fail_closed(tmp_path) -> None:
    cases = (
        (FakeExperimentRegistry(verified=False), _document(), "not verified"),
        (FakeExperimentRegistry(verdict="FAIL"), _document(), "not PASS"),
        (FakeExperimentRegistry(), _document(dataset_version="other"), "binding mismatch"),
    )
    for index, (experiments, document, reason) in enumerate(cases):
        with PaperCandidateRegistry(tmp_path / f"candidates-{index}.db") as registry:
            report = admit_factory_candidates(
                document,
                candidate_registry=registry,
                experiment_registry=experiments,
                admitted_at_ms=2000,
            )
            assert report["status"] == "BLOCKED"
            assert report["admitted"] == []
            assert reason in report["rejected"][0]["reason"]
            assert registry.all() == ()


def test_factory_schema_and_candidate_shape_are_fail_closed(tmp_path) -> None:
    with PaperCandidateRegistry(tmp_path / "candidates.db") as registry:
        try:
            admit_factory_candidates(
                {"schema_version": 1, "eligible_discoveries": []},
                candidate_registry=registry,
                experiment_registry=FakeExperimentRegistry(),
                admitted_at_ms=2000,
            )
        except RuntimeError as exc:
            assert "schema" in str(exc)
        else:
            raise AssertionError("legacy factory artifact must not be admitted")

        report = admit_factory_candidates(
            {"schema_version": 2, "eligible_discoveries": ["bad"]},
            candidate_registry=registry,
            experiment_registry=FakeExperimentRegistry(),
            admitted_at_ms=2000,
        )
        assert report["status"] == "BLOCKED"
        assert report["rejected"][0]["alpha_id"] == "UNKNOWN"


def test_empty_factory_handoff_does_not_require_production_experiment_db(tmp_path, monkeypatch) -> None:
    factory = tmp_path / "factory.json"
    factory.write_text('{"schema_version":2,"eligible_discoveries":[]}', encoding="utf-8")

    def unexpected_registry():
        raise AssertionError("empty handoff must not open the experiment registry")

    monkeypatch.setattr(
        "tools.alpha_candidate_admission.production_experiment_registry",
        unexpected_registry,
    )
    report = run(
        factory_path=factory,
        registry_path=tmp_path / "candidates.db",
        output_path=tmp_path / "admission.json",
        admitted_at_ms=2000,
    )

    assert report["status"] == "PASS"
    assert report["eligible_count"] == 0
    assert report["admitted"] == []
    assert report["registry"]["status"] == "VERIFIED"


def test_forward_worker_consumes_verified_dynamic_registry(tmp_path, monkeypatch) -> None:
    from tools import forward_track_advance

    registry_path = tmp_path / "candidates.db"
    with PaperCandidateRegistry(registry_path) as registry:
        report = admit_factory_candidates(
            _document(),
            candidate_registry=registry,
            experiment_registry=FakeExperimentRegistry(),
            admitted_at_ms=2000,
        )
        assert report["status"] == "PASS"
    monkeypatch.setattr(forward_track_advance, "_CANDIDATE_REGISTRY", str(registry_path))

    alphas, dynamic_ids = forward_track_advance._registered_alphas(
        workflow_authority=False
    )

    assert dynamic_ids == (ALPHA_ID,)
    assert ALPHA_ID in alphas
    assert alphas[ALPHA_ID]["expr"] == EXPRESSION
    assert alphas[ALPHA_ID]["quantile"] == 0.3
    assert alphas[ALPHA_ID]["cadence_days"] == 7
