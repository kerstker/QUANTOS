"""Historical public ancestry uses a genuine Scope installation, never its seed.

The inherited fixture substitutes local operator/custody/account I/O only. The
Scope compiler, signature, installer activation and original archive are native.
"""
from copy import deepcopy
import json

import pytest

from tests.live.test_demo_market_partition import book as book
from tests.live.test_demo_market_partition_runtime import installed as installed
from tests.live.test_gold_first_enrollment_review import first as first
from tests.tools.test_gold_first_enrollment_materials import materials as materials
from tests.tools.test_gold_budget_scope_preparation import scope_inputs as scope_inputs
from tests.tools.test_gold_budget_scope_materials import day_materials as day_materials
from tests.tools.test_gold_budget_scope_install import local as local, operator
from tools import demo_successor_setup as successor


@pytest.fixture
def scope_records(local, monkeypatch, capsys):
    from tools import gold_strategy_budget_install as tool
    operator(tool, local, monkeypatch)
    assert tool.main(['--source-commit', 'c'*40, '--enroll-approved']) == 0, capsys.readouterr().out
    stage = successor.active_setup()
    read = lambda p: json.loads(p.read_bytes())
    public_names = ('registry.json', 'policy.json', 'profile.json', 'request.json',
                    'operator-approval.json', 'original-policy.json', 'original-profile.json')
    records = dict(
        schema='DEMO_GOLD_SCOPE_INSTALLATION_ANCESTRY_V1',
        active_pointer=read(local['area']/'active.json'),
        activation=read(stage/'activation.json'), result=read(stage/'result.json'),
        spec=read(stage/'spec.json'), intent=read(stage/'scope-intent.json'),
        manifest=read(stage/'bundle/manifest.json'),
        operator_enrollment=read(stage/'operator-enrollment.json'),
        registry_preimage=read(stage/'registry-preimage.json'),
        original_setup=dict(result=local['original'], spec=read(local['area']/'spec.json')),
        public_documents={name: read(stage/'bundle'/name) for name in public_names},
    )
    # The validator must not call reconstruction or any filesystem/key reader.
    monkeypatch.setattr(tool, 'reconstruct', lambda *a, **k: pytest.fail('private seed reconstruction'))
    return records, read(local['cfg']/'policy.json'), read(local['public'])


def test_native_scope_public_ancestry_is_historical_and_has_no_private_reader(scope_records, monkeypatch):
    from live.execution import demo_gold_scope_ancestry as ancestry
    records, scope, registry = scope_records
    before = deepcopy(records)
    checked = ancestry.validate_scope_ancestry(records, scope=scope, registry=registry, source_commit='c'*40)
    assert checked['original_cycle_sha256'] == scope['scope_enrollment']['original_cycle_sha256']
    assert checked['scope_sha256'] == ancestry.digest(scope)
    assert checked['execution_approval_granted'] is False
    assert records == before


@pytest.mark.parametrize('damage', ['activation', 'manifest', 'signature', 'original', 'secret', 'status'])
def test_public_scope_ancestry_rejects_missing_or_changed_originals(scope_records, damage):
    from live.execution import demo_gold_scope_ancestry as ancestry
    records, scope, registry = deepcopy(scope_records)
    if damage == 'activation': records['activation']['enrollment_sha256'] = '0'*64
    elif damage == 'manifest': records['manifest']['files']['policy.json'] = '0'*64
    elif damage == 'signature': scope['scope']['max_quantity_per_order'] = '123'
    elif damage == 'original': records['original_setup']['result']['task_sha256'] = '0'*64
    elif damage == 'secret': records['public_documents']['signing-key.json'] = {}
    else: records['result']['status'] = 'INSTALLED_PENDING_CYCLE'
    with pytest.raises(ValueError):
        ancestry.validate_scope_ancestry(records, scope=scope, registry=registry, source_commit='c'*40)
