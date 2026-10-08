"""The original protected worker can supply an unsigned first-market proposal."""
from contextlib import nullcontext
from copy import deepcopy
import json

import pytest

from tests.live.test_demo_market_partition import book as book
from tests.live.test_demo_market_partition_runtime import installed as installed
from tests.live.test_gold_first_enrollment_review import first as first
from tests.tools.test_gold_first_enrollment_review import bound as bound, exchange as exchange
from tools import demo_successor_setup as operator


def test_original_cli_prepares_new_gold_scope_without_confirming_or_installing(
        exchange, book, tmp_path, monkeypatch, capsys):
    value, calls = exchange
    _, context, inputs, _, _ = book
    root=tmp_path/'operator-setup'
    monkeypatch.setattr(operator, 'SETUP', root)
    monkeypatch.setattr(operator, 'active_setup', lambda: root)
    monkeypatch.setattr(operator, 'verify_source', lambda _: None)
    monkeypatch.setattr(operator.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(operator, 'cycle_lock', lambda _: nullcontext())
    old=operator.setup.propose(origin=inputs['legacy_origin'], registry=value['registry'],
        position_context=context, now_ms=inputs['now_ms'], source_commit='c'*40)
    records={'spec.json':old, 'result.json':{'spec_sha256':operator.setup.digest(old), 'source_commit':'c'*40},
        operator.installer.REGISTRY.name:value['registry']}
    monkeypatch.setattr(operator, 'read', lambda path: deepcopy(records[path.name]))
    monkeypatch.setattr(operator.setup, 'operator_confirmation', lambda *a: pytest.fail('unsigned scope cannot confirm'))
    monkeypatch.setattr(operator, 'install_successor', lambda *a, **k: pytest.fail('unsigned scope cannot install'))
    before=deepcopy(value['state']), value['events'].row_count(), deepcopy(value['registry'])
    assert operator.main(['--source-commit', 'c'*40, '--prepare-first-gold-enrollment']) == 0
    output=json.loads(capsys.readouterr().out)
    assert output['status']=='UNSIGNED_FIRST_GOLD_PROPOSAL'
    assert output['spec_sha256']==operator.setup.digest(output['spec'])
    assert output['spec']['symbol']=='XAUUSDT' and output['spec']['window_seconds']==3600
    assert output['spec']['first_gold_enrollment']['sealed_usdt_wallet']=='4200'
    assert all(output[key] is False for key in ('gold_enrollment_installed','orders_submitted',
        'services_started','economic_authority'))
    assert calls[-1]==('close',) and not root.exists()
    assert before==(value['state'], value['events'].row_count(), value['registry'])


@pytest.mark.parametrize('extra', [['--enroll'], ['--run-cycle'], ['--symbol','XAUUSDT'],
    ['--review-first-gold-enrollment'], ['--review-market-transition','XAUUSDT'],
    ['--prepare-market-partition'], ['--portfolio-config','/isolated.json'],
    ['--research-identity-sha256','a'*64]])
def test_unsigned_first_scope_rejects_other_actions_before_source_or_account_capture(monkeypatch, extra):
    monkeypatch.setattr(operator, 'verify_source', lambda _: pytest.fail('reject before protected reads'))
    assert operator.main(['--source-commit','c'*40,'--prepare-first-gold-enrollment',*extra]) == 3
