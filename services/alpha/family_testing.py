"""Compatibility exports; implementation lives in quantos.analytics.family_testing."""

from quantos.analytics.family_testing import (
    FamilyAssignment,
    HierarchicalFdrResult,
    assign_families,
    simes_pvalue,
    hierarchical_fdr,
    family_adjusted_trial_count,
    marginal_ir,
)

__all__ = ['FamilyAssignment', 'HierarchicalFdrResult', 'assign_families', 'simes_pvalue', 'hierarchical_fdr', 'family_adjusted_trial_count', 'marginal_ir']
