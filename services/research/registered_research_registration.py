"""Compatibility exports for the original pure registration contract."""
from quantos.contracts.registered_research_registration import (
    canonical, plan_digest, require, expected_provenance, registration_evidence,
    native_trial_identity, initial_archive_manifest,
)

__all__ = ['canonical', 'plan_digest', 'require', 'expected_provenance',
           'registration_evidence', 'native_trial_identity', 'initial_archive_manifest']
