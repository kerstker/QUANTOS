"""Statistical tuning must bind the declared pre-OOS grid, without authority."""
import copy

import pytest

from quantos.contracts.research_evaluation_plan import core_status, validate_core
from quantos.contracts.research_statistical_review import digest, validate_statistical_policy
from services.validation.project_research_statistics import _registration
from tests.contracts.test_research_statistical_review import DAY, policy
from tests.validation.test_research_evaluation_plan import core


def binding(*, split="both", locked=True):
    declaration = core()
    statistical = policy()
    sample = statistical["sample"]
    start, end = sample["start_ms"], sample["end_ms"]
    declaration["sample"] = dict(sample, end_ms=end + 10 * DAY if locked else end)
    declaration["splits"] = dict(
        train=dict(start_ms=start, end_ms=start + 15 * DAY),
        validation=dict(start_ms=start + 16 * DAY, end_ms=end),
        locked_oos=dict(start_ms=end + DAY, end_ms=end + 10 * DAY) if locked else None,
    )
    if split != "both":
        declaration["splits"][split] = dict(start_ms=start, end_ms=end)
        declaration["splits"]["validation" if split == "train" else "train"] = None
    statistical["source_identity_sha256"] = declaration["source_identity_sha256"]
    statistical["dataset_manifest_sha256"] = declaration["dataset_manifest_sha256"]
    declaration["benchmark"] = {
        key: statistical["benchmark_policy"][key]
        for key in ("id", "definition_sha256", "input_sha256")
    }
    declaration["selection"]["validation_policy_sha256"] = digest(statistical)
    return dict(plan=dict(evaluation_core=declaration)), statistical


def register(run, statistical):
    validate_core(run["plan"]["evaluation_core"])
    validate_statistical_policy(statistical)
    return _registration(run, statistical, digest(statistical))


@pytest.mark.parametrize("split", ["both", "train", "validation"])
def test_accepts_exact_declared_pre_oos_grid_without_certifying_oos(split):
    run, statistical = binding(split=split)
    before = copy.deepcopy((run, statistical))
    assert register(run, statistical) is True
    assert (run, statistical) == before
    status = core_status(run["plan"]["evaluation_core"])
    assert status["oos_status"] == "UNKNOWN"
    assert status["economic_authority"] is status["submission_enabled"] is False
    assert statistical["economic_authority"] is statistical["submission_enabled"] is False


def test_existing_full_sample_without_oos_keeps_exact_binding():
    run, statistical = binding(split="train", locked=False)
    assert register(run, statistical) is True
    statistical["sample"]["start_ms"] += DAY
    run["plan"]["evaluation_core"]["selection"]["validation_policy_sha256"] = digest(statistical)
    with pytest.raises(ValueError, match="PREDECLARED_SAMPLE"):
        register(run, statistical)


@pytest.mark.parametrize("damage", ["later_start", "earlier_end", "oos_overlap", "full_sample", "frequency"])
def test_refuses_changed_statistical_grid_even_with_a_matching_policy_hash(damage):
    run, statistical = binding()
    sample = statistical["sample"]
    if damage == "later_start":
        sample["start_ms"] += DAY
    elif damage == "earlier_end":
        sample["end_ms"] -= DAY
    elif damage == "oos_overlap":
        sample["end_ms"] += DAY
    elif damage == "full_sample":
        statistical["sample"] = copy.deepcopy(run["plan"]["evaluation_core"]["sample"])
    else:
        sample["frequency_ms"] //= 2
    run["plan"]["evaluation_core"]["selection"]["validation_policy_sha256"] = digest(statistical)
    with pytest.raises(ValueError, match="PREDECLARED_SAMPLE|LOCKED_OOS_NOT_STATISTICAL_TUNING"):
        register(run, statistical)


def test_does_not_fill_an_undeclared_gap_between_train_and_validation():
    run, statistical = binding()
    run["plan"]["evaluation_core"]["splits"]["validation"]["start_ms"] += DAY
    with pytest.raises(ValueError, match="PREDECLARED_STATISTICAL_CONTIGUITY"):
        register(run, statistical)


def test_locked_oos_without_declared_tuning_windows_is_rejected():
    run, statistical = binding()
    run["plan"]["evaluation_core"]["splits"].update(train=None, validation=None)
    with pytest.raises(ValueError, match="PREDECLARED_STATISTICAL_WINDOW_MISSING"):
        register(run, statistical)


@pytest.mark.parametrize("damage", ["source", "dataset", "benchmark", "registration"])
def test_window_fix_keeps_existing_data_benchmark_and_registration_binding(damage):
    run, statistical = binding()
    declaration = run["plan"]["evaluation_core"]
    if damage == "source":
        declaration["source_identity_sha256"] = "b" * 64
    elif damage == "dataset":
        declaration["dataset_manifest_sha256"] = "b" * 64
    elif damage == "benchmark":
        declaration["benchmark"]["input_sha256"] = "b" * 64
    else:
        declaration["registered_at_ms"] = statistical["study_scope"]["registered_at_ms"] - 1
    with pytest.raises(ValueError, match="PREDECLARED_DATA|PREDECLARED_BENCHMARK|POLICY_REGISTRATION"):
        register(run, statistical)


def test_missing_or_different_policy_commitment_is_still_unregistered():
    run, statistical = binding()
    run["plan"]["evaluation_core"]["selection"]["validation_policy_sha256"] = "b" * 64
    assert register(run, statistical) is False
    run["plan"]["evaluation_core"] = None
    assert _registration(run, statistical, digest(statistical)) is False
