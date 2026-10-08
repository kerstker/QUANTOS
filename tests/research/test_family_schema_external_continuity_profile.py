"""Typed Pass-M profiles for non-equality external continuity semantics."""

from __future__ import annotations

import dataclasses

import pytest

from services.research.experiment_registry import family_schema_spec as spec


START = "VERIFY_CLOSURE_START_EXECUTION_ATTESTATION"
TERMINAL = "VERIFY_CLOSURE_TERMINAL_EXECUTION_ATTESTATION"
LEASE = "VERIFY_CLOSURE_IMMUTABLE_ARTIFACT_LEASE"


def _subject_input() -> spec.ExternalContinuityRowInputSpec:
    return spec.ExternalContinuityRowInputSpec(
        alias="closure",
        source_kind=spec.ExternalContinuityRowInputKind.SUBJECT_ROW,
        table="data_snooping_family_closures",
        bindings=(),
        required_fields=(
            "authority_instance_id",
            "family_id",
            "attempt_id",
            "terminal_attempt_event_id",
            "evaluation_batch_id",
            "evaluation_batch_receipt_hash",
            "oos_access_attempt_terminal_hash",
            "closure_status",
            "closed_at_us",
        ),
        required_verification_rule_ids=(
            "family_closure_row_hash",
            "family_closure_attempt_batch_branch",
        ),
        cardinality=None,
        key_requirement=None,
    )


def _attempt_input() -> spec.ExternalContinuityRowInputSpec:
    return spec.ExternalContinuityRowInputSpec(
        alias="attempt",
        source_kind=spec.ExternalContinuityRowInputKind.EXACT_RELATED_ROW,
        table="data_snooping_oos_access_attempts",
        bindings=(
            spec.ExternalContinuityReadBindingSpec(
                "attempt_id", "closure.attempt_id"
            ),
            spec.ExternalContinuityReadBindingSpec(
                "attempt_event_id", "closure.terminal_attempt_event_id"
            ),
            spec.ExternalContinuityReadBindingSpec(
                "record_hash", "closure.oos_access_attempt_terminal_hash"
            ),
        ),
        required_fields=("status",),
        required_verification_rule_ids=(
            "oos_attempt_row_hash",
            "oos_attempt_identity_stability",
            "oos_attempt_terminal_batch_branch",
        ),
    )


def _batch_input() -> spec.ExternalContinuityRowInputSpec:
    return spec.ExternalContinuityRowInputSpec(
        alias="evaluation_batch",
        source_kind=spec.ExternalContinuityRowInputKind.EXACT_RELATED_ROW,
        table="data_snooping_evaluation_batches",
        bindings=(
            spec.ExternalContinuityReadBindingSpec(
                "evaluation_batch_id", "closure.evaluation_batch_id"
            ),
            spec.ExternalContinuityReadBindingSpec(
                "record_hash", "closure.evaluation_batch_receipt_hash"
            ),
            spec.ExternalContinuityReadBindingSpec(
                "start_execution_attestation_hash", "start.attestation_hash"
            ),
            spec.ExternalContinuityReadBindingSpec(
                "terminal_execution_attestation_hash", "terminal.attestation_hash"
            ),
            spec.ExternalContinuityReadBindingSpec(
                "immutable_artifact_lease_hash", "lease.lease_hash"
            ),
        ),
        required_fields=("committed_at_us",),
        required_verification_rule_ids=(
            "evaluation_batch_row_hash",
            "evaluation_batch_started_attempt_identity",
            "evaluation_batch_attempt_terminal_binding",
        ),
    )


def _complete_branch() -> spec.ExternalContinuityChronologyBranchSpec:
    return spec.ExternalContinuityChronologyBranchSpec(
        branch_kind=spec.ExternalContinuityStatusBranchKind.COMPLETE,
        selector=spec.ExternalContinuityBranchSelectorSpec(
            closure_status_path="closure.closure_status",
            closure_status="COMPLETE",
            attempt_status_path="attempt.status",
            attempt_status="COMPLETED",
        ),
        required_additional_external_verifier_codes=(TERMINAL,),
        row_inputs=(_subject_input(), _attempt_input(), _batch_input()),
        comparisons=(
            spec.ExternalContinuityTimeComparisonSpec(
                "lease.acquired_at_us", spec.ComparisonOperator.LE, "start.issued_at_us"
            ),
            spec.ExternalContinuityTimeComparisonSpec(
                "start.issued_at_us", spec.ComparisonOperator.LT, "terminal.issued_at_us"
            ),
            spec.ExternalContinuityTimeComparisonSpec(
                "terminal.issued_at_us",
                spec.ComparisonOperator.LE,
                "evaluation_batch.committed_at_us",
            ),
            spec.ExternalContinuityTimeComparisonSpec(
                "evaluation_batch.committed_at_us",
                spec.ComparisonOperator.LE,
                "closure.closed_at_us",
            ),
            spec.ExternalContinuityTimeComparisonSpec(
                "closure.closed_at_us", spec.ComparisonOperator.LE, "lease.expires_at_us"
            ),
        ),
    )


def _incomplete_branch() -> spec.ExternalContinuityChronologyBranchSpec:
    return spec.ExternalContinuityChronologyBranchSpec(
        branch_kind=spec.ExternalContinuityStatusBranchKind.INCOMPLETE,
        selector=spec.ExternalContinuityBranchSelectorSpec(
            closure_status_path="closure.closure_status",
            closure_status="NOT_ASSESSABLE",
            attempt_status_path="attempt.status",
            attempt_status="INCOMPLETE",
        ),
        required_additional_external_verifier_codes=(),
        row_inputs=(_subject_input(), _attempt_input()),
        comparisons=(
            spec.ExternalContinuityTimeComparisonSpec(
                "lease.acquired_at_us", spec.ComparisonOperator.LE, "start.issued_at_us"
            ),
            spec.ExternalContinuityTimeComparisonSpec(
                "start.issued_at_us", spec.ComparisonOperator.LE, "closure.closed_at_us"
            ),
            spec.ExternalContinuityTimeComparisonSpec(
                "closure.closed_at_us", spec.ComparisonOperator.LE, "lease.expires_at_us"
            ),
        ),
    )


def _start_terminal_profile() -> spec.ExternalContinuitySemanticProfileSpec:
    return spec.ExternalContinuitySemanticProfileSpec(
        semantic_kind=(
            spec.ExternalContinuitySemanticKind.START_TERMINAL_STRICT_PROVIDER_PREFIX
        ),
        required_subject_context_fields=("family_id", "attempt_id", "evaluation_batch_id"),
        required_subject_roles=(("left", "START"), ("right", "TERMINAL")),
        require_distinct_evidence_hashes=True,
        require_same_provider_authority=True,
        require_same_provider_genesis=True,
        require_right_projection_strict_extension=True,
        require_strict_issued_chronology=True,
        require_full_lease_time_window=False,
        strict_time_order_paths=("start.issued_at_us", "terminal.issued_at_us"),
        provider_inputs=(
            spec.ExternalContinuityProviderInputSpec(
                "start",
                START,
                spec.ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                ("attestation_hash", "issued_at_us"),
            ),
            spec.ExternalContinuityProviderInputSpec(
                "terminal",
                TERMINAL,
                spec.ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                ("attestation_hash", "issued_at_us"),
            ),
        ),
        chronology_branches=(),
    )


def _start_lease_profile() -> spec.ExternalContinuitySemanticProfileSpec:
    return spec.ExternalContinuitySemanticProfileSpec(
        semantic_kind=spec.ExternalContinuitySemanticKind.START_LEASE_SUBJECT_TIME_WINDOW,
        required_subject_context_fields=("family_id", "attempt_id", "evaluation_batch_id"),
        required_subject_roles=(("left", "START"),),
        require_distinct_evidence_hashes=False,
        require_same_provider_authority=False,
        require_same_provider_genesis=False,
        require_right_projection_strict_extension=False,
        require_strict_issued_chronology=False,
        require_full_lease_time_window=True,
        strict_time_order_paths=(),
        provider_inputs=(
            spec.ExternalContinuityProviderInputSpec(
                "start",
                START,
                spec.ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                ("attestation_hash", "issued_at_us"),
            ),
            spec.ExternalContinuityProviderInputSpec(
                "lease",
                LEASE,
                spec.ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                ("lease_hash", "acquired_at_us", "expires_at_us"),
            ),
            spec.ExternalContinuityProviderInputSpec(
                "terminal",
                TERMINAL,
                spec.ExternalContinuityProviderInputSourceKind.BRANCH_ADDITIONAL,
                ("attestation_hash", "issued_at_us"),
            ),
        ),
        chronology_branches=(_complete_branch(), _incomplete_branch()),
    )


def test_start_lease_profile_closes_complete_and_incomplete_chronology() -> None:
    profile = _start_lease_profile()
    complete, incomplete = profile.chronology_branches

    assert complete.required_additional_external_verifier_codes == (TERMINAL,)
    assert tuple(row.alias for row in complete.row_inputs) == (
        "closure",
        "attempt",
        "evaluation_batch",
    )
    assert tuple((step.left_path, step.operator, step.right_path) for step in complete.comparisons) == (
        ("lease.acquired_at_us", spec.ComparisonOperator.LE, "start.issued_at_us"),
        ("start.issued_at_us", spec.ComparisonOperator.LT, "terminal.issued_at_us"),
        (
            "terminal.issued_at_us",
            spec.ComparisonOperator.LE,
            "evaluation_batch.committed_at_us",
        ),
        (
            "evaluation_batch.committed_at_us",
            spec.ComparisonOperator.LE,
            "closure.closed_at_us",
        ),
        ("closure.closed_at_us", spec.ComparisonOperator.LE, "lease.expires_at_us"),
    )
    assert incomplete.required_additional_external_verifier_codes == ()
    assert tuple(row.alias for row in incomplete.row_inputs) == ("closure", "attempt")


def test_start_terminal_profile_remains_closed_and_branch_free() -> None:
    profile = _start_terminal_profile()
    assert profile.chronology_branches == ()
    assert profile.fail_closed and not profile.default_pass


@pytest.mark.parametrize(
    ("branch", "changes", "message"),
    (
        (_complete_branch(), {"required_additional_external_verifier_codes": ()}, "terminal"),
        (
            _complete_branch(),
            {"row_inputs": (_subject_input(), _attempt_input())},
            "evaluation batch",
        ),
        (
            _complete_branch(),
            {"comparisons": _complete_branch().comparisons[:-1]},
            "chronology",
        ),
        (
            _incomplete_branch(),
            {"required_additional_external_verifier_codes": (TERMINAL,)},
            "forbids terminal",
        ),
        (
            _incomplete_branch(),
            {"row_inputs": (*_incomplete_branch().row_inputs, _batch_input())},
            "forbids evaluation batch",
        ),
        (
            _incomplete_branch(),
            {"selector": dataclasses.replace(_incomplete_branch().selector, attempt_status="NOT_STARTED")},
            "INCOMPLETE",
        ),
        (_incomplete_branch(), {"fail_closed": False}, "fail closed"),
        (_incomplete_branch(), {"default_pass": True}, "default pass"),
    ),
)
def test_start_lease_branch_tampering_is_rejected(
    branch: spec.ExternalContinuityChronologyBranchSpec,
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        dataclasses.replace(branch, **changes)


def test_profile_rejects_missing_or_reordered_status_branches() -> None:
    profile = _start_lease_profile()
    with pytest.raises(ValueError, match="COMPLETE.*INCOMPLETE"):
        dataclasses.replace(profile, chronology_branches=(_incomplete_branch(),))
    with pytest.raises(ValueError, match="COMPLETE.*INCOMPLETE"):
        dataclasses.replace(profile, chronology_branches=tuple(reversed(profile.chronology_branches)))


def test_related_read_requires_exact_key_and_declared_source_alias() -> None:
    attempt = _attempt_input()
    with pytest.raises(ValueError, match="earlier row input"):
        dataclasses.replace(
            _complete_branch(),
            row_inputs=(attempt, _subject_input(), _batch_input()),
        )
    with pytest.raises(ValueError, match="EXACTLY_ONE"):
        dataclasses.replace(attempt, cardinality=None)


def test_publication_uses_exact_related_closure_clock() -> None:
    publication = spec.ExternalContinuityRowInputSpec(
        alias="publication",
        source_kind=spec.ExternalContinuityRowInputKind.SUBJECT_ROW,
        table="artifact_publication_receipts",
        bindings=(),
        required_fields=("closure_manifest_hash", "closure_status", "attempt_status"),
        required_verification_rule_ids=(
            "publication_receipt_closure_exact",
            "publication_receipt_closure_external_summaries_exact",
        ),
        cardinality=None,
        key_requirement=None,
    )
    related_closure = spec.ExternalContinuityRowInputSpec(
        alias="related_closure",
        source_kind=spec.ExternalContinuityRowInputKind.EXACT_RELATED_ROW,
        table="data_snooping_family_closures",
        bindings=(
            spec.ExternalContinuityReadBindingSpec(
                "record_hash", "publication.closure_manifest_hash"
            ),
        ),
        required_fields=(
            "closure_status",
            "closed_at_us",
            "attempt_id",
            "terminal_attempt_event_id",
            "oos_access_attempt_terminal_hash",
        ),
        required_verification_rule_ids=(
            "family_closure_row_hash",
            "family_closure_attempt_batch_branch",
        ),
    )
    branch = dataclasses.replace(
        _incomplete_branch(),
        selector=spec.ExternalContinuityBranchSelectorSpec(
            "related_closure.closure_status",
            "NOT_ASSESSABLE",
            "attempt.status",
            "INCOMPLETE",
        ),
        row_inputs=(
            publication,
            related_closure,
            dataclasses.replace(
                _attempt_input(),
                bindings=(
                    spec.ExternalContinuityReadBindingSpec(
                        "attempt_id", "related_closure.attempt_id"
                    ),
                    spec.ExternalContinuityReadBindingSpec(
                        "attempt_event_id", "related_closure.terminal_attempt_event_id"
                    ),
                    spec.ExternalContinuityReadBindingSpec(
                        "record_hash", "related_closure.oos_access_attempt_terminal_hash"
                    ),
                ),
            ),
        ),
        comparisons=(
            spec.ExternalContinuityTimeComparisonSpec(
                "lease.acquired_at_us", spec.ComparisonOperator.LE, "start.issued_at_us"
            ),
            spec.ExternalContinuityTimeComparisonSpec(
                "start.issued_at_us",
                spec.ComparisonOperator.LE,
                "related_closure.closed_at_us",
            ),
            spec.ExternalContinuityTimeComparisonSpec(
                "related_closure.closed_at_us",
                spec.ComparisonOperator.LE,
                "lease.expires_at_us",
            ),
        ),
    )
    assert branch.comparisons[-1].left_path == "related_closure.closed_at_us"


def test_external_continuity_embeds_profile_exactly() -> None:
    profile = _start_lease_profile()
    continuity = spec.ExternalAuthorityContinuitySpec(
        verifier_code="VERIFY_CLOSURE_START_LEASE_CONTINUITY",
        required_external_verifier_codes=(START, LEASE),
        provider_field_equalities=(
            spec.ExternalProviderFieldEqualitySpec(
                START, "lease_id", LEASE, "lease_id"
            ),
        ),
        reference_presence_column="start_execution_attestation_hash",
        semantic_profile=profile,
    )
    assert continuity.semantic_profile is profile


def test_provider_aliases_must_resolve_outer_and_branch_verifier_results() -> None:
    profile = _start_lease_profile()
    wrong_start = dataclasses.replace(
        profile.provider_inputs[0],
        verifier_code="VERIFY_OTHER_START_EXECUTION_ATTESTATION",
    )
    drifted = dataclasses.replace(
        profile,
        provider_inputs=(wrong_start, *profile.provider_inputs[1:]),
    )
    with pytest.raises(ValueError, match="outer provider aliases"):
        spec.ExternalAuthorityContinuitySpec(
            verifier_code="VERIFY_CLOSURE_START_LEASE_CONTINUITY",
            required_external_verifier_codes=(START, LEASE),
            provider_field_equalities=(
                spec.ExternalProviderFieldEqualitySpec(
                    START, "lease_id", LEASE, "lease_id"
                ),
            ),
            reference_presence_column="start_execution_attestation_hash",
            semantic_profile=drifted,
        )

    with pytest.raises(ValueError, match="start/lease/terminal provider inputs"):
        dataclasses.replace(
            profile,
            provider_inputs=profile.provider_inputs[:-1],
        )


def test_status_selector_and_provider_projection_alias_tampering_is_rejected() -> None:
    with pytest.raises(ValueError, match="exact attempt-row status"):
        dataclasses.replace(
            _incomplete_branch(),
            selector=dataclasses.replace(
                _incomplete_branch().selector,
                attempt_status_path="closure.closure_status",
            ),
        )

    batch = _batch_input()
    bad_binding = dataclasses.replace(
        batch.bindings[-3],
        source_path="start.issued_at_us",
    )
    with pytest.raises(ValueError, match="provider field is not closed"):
        dataclasses.replace(
            _complete_branch(),
            row_inputs=(
                _subject_input(),
                _attempt_input(),
                dataclasses.replace(
                    batch,
                    bindings=(*batch.bindings[:-3], bad_binding, *batch.bindings[-2:]),
                ),
            ),
        )
