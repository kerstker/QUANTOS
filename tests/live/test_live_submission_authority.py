"""Ordinary live submission has no in-process authority escape hatch."""

import pytest

from live.governance.validation_attestation import (
    HUMAN_OVERRIDE,
    ValidationAttestation,
)
from live.pipeline import (
    _submit_live_batch_technical_diagnostic,
    submit_live_batch,
)
from services.validation.live_submission_authority import (
    LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE,
    LiveSubmissionAuthorityBlocked,
    live_submission_authority_admitted,
)


class _UntouchedCollaborator:
    def __init__(self) -> None:
        self.calls = 0

    def __getattr__(self, _name):
        self.calls += 1
        raise AssertionError("live collaborator was touched before authority denial")

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("live collaborator was called before authority denial")


def _kwargs():
    collaborators = {
        name: _UntouchedCollaborator()
        for name in ("gate", "ctx_factory", "venue", "mandate", "ledger", "audit")
    }
    kwargs = {
        "weights": {"BTC": 0.5},
        "prices": {"BTC": 60_000.0},
        "aum": 100_000.0,
        "current_positions": {},
        **collaborators,
        "validation_attestation": ValidationAttestation(
            strategy_id="forged-human-override",
            kind=HUMAN_OVERRIDE,
            attested_by="caller-claimed-human",
            attested_at=1,
            reason="caller-created evidence cannot be authority",
        ),
        "strategy_id": "forged-human-override",
        "decided_ts": 1,
        "now": 2,
    }
    return kwargs, collaborators


def test_caller_claims_and_booleans_never_create_live_submission_authority():
    forged = {
        "signed": True,
        "human_override": True,
        "attested_by": "caller-claimed-human",
    }
    assert live_submission_authority_admitted(
        requested=True, authority_record=forged
    ) is False
    assert live_submission_authority_admitted(
        requested=False, authority_record=forged
    ) is False


def test_public_submit_blocks_before_every_side_effect_collaborator():
    kwargs, collaborators = _kwargs()

    with pytest.raises(LiveSubmissionAuthorityBlocked, match=LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE):
        submit_live_batch(**kwargs)

    assert all(collaborator.calls == 0 for collaborator in collaborators.values())


def test_private_technical_seam_requires_explicit_diagnostic_mode():
    kwargs, collaborators = _kwargs()

    with pytest.raises(LiveSubmissionAuthorityBlocked, match="technical-diagnostic seam"):
        _submit_live_batch_technical_diagnostic(
            **kwargs,
            diagnostic_only=False,
        )

    assert all(collaborator.calls == 0 for collaborator in collaborators.values())
