"""First-only Scope bundles remain non-executable at their shared importer."""
from copy import deepcopy

import pytest

from tools.demo_operating_bundle import BASE_FILES, manifest_check


def first_manifest():
    names = BASE_FILES | {
        'request.json', 'operator-approval.json', 'original-policy.json',
        'original-profile.json', 'original-origin.json',
    }
    return dict(schema='DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V2', source_commit='c'*40,
        expected_registry_sha256='a'*64, files={name:'b'*64 for name in names}, tasks=[])


def test_first_scope_importer_accepts_only_the_fixed_empty_task_bundle():
    manifest = first_manifest()
    assert manifest_check(manifest) == manifest


@pytest.mark.parametrize('damage', ['task', 'extra_file', 'missing_original', 'unknown_version'])
def test_first_scope_bundle_cannot_add_execution_or_omit_originals(damage):
    manifest = deepcopy(first_manifest())
    if damage == 'task':
        manifest['tasks'] = ['d'*64]
        manifest['files']['d'*64+'.json'] = 'e'*64
    elif damage == 'extra_file':
        manifest['files']['portfolio-runtime.json'] = 'e'*64
    elif damage == 'missing_original':
        del manifest['files']['original-policy.json']
    else:
        manifest['schema'] = 'DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V3'
    with pytest.raises(ValueError):
        manifest_check(manifest)
