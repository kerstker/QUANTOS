"""Actual cold BTC installation to first Scope and the existing future chain.

Operator identity, accepted source, UID custody and public venue transport are
isolated fixtures. Original installation, ledger/issuer history, approvals,
public records and native admission run; this is not a field deployment claim.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from tests.tools.test_gold_first_scope_materials import first_scope_archive as first_scope_archive
from tests.tools.test_gold_first_scope_install import (
    cold_scope_local as original_cold_scope_local, installed_first_scope as installed_first_scope,
)
from tests.tools.test_gold_future_budget_materials import build_native_scope
from tests.tools.test_gold_future_budget_install import native_install as native_install
from tests.tools.test_gold_execution_delegation_v2_install import (
    signed_native as signed_native, v2_installation as v2_installation,
)
from tests.live.test_gold_future_delivery import build_installed_future_delivery, send_first_future
from tests.live.test_gold_first_scope_original_history import issuer_claims


@pytest.fixture
def cold_scope_local(tmp_path, monkeypatch, first_scope_archive, request):
    """Bind real warmup bytes before the first Scope request or confirmation."""
    from tests.research.test_gold_bar_dataset import raw_rows, STEP
    from tests.live.test_demo_market_partition_runtime import shift
    from tools.demo_operator_setup import canonical
    from tests.live import test_gold_first_scope_original_history as history_fixture
    from tests.tools import test_gold_scope_pilot as retention_fixture
    from tools import (gold_bar_fresh_inputs, gold_bar_episode_tail,
        demo_reported_recovery, demo_resident_recovery, gold_bar_worker_inputs,
        gold_bar_pilot_capacity, gold_pilot_retention, gold_pilot_retention_store,
        gold_strategy_worker)

    include_unsigned = getattr(request, 'param', False)
    native_history = history_fixture.build_original_btc_history
    def history(*args, **kwargs):
        return native_history(*args, **kwargs, include_unsigned=include_unsigned)
    monkeypatch.setattr(history_fixture, 'build_original_btc_history', history)
    original = original_cold_scope_local.__wrapped__(tmp_path, monkeypatch, first_scope_archive)
    local = next(original)
    try:
        # Native readers import custody aliases lazily. Map all readers used
        # here per case, retaining C's native mode/link checks and root bound.
        for module in (gold_bar_fresh_inputs, gold_bar_episode_tail, demo_reported_recovery,
                gold_bar_worker_inputs, gold_bar_pilot_capacity, gold_pilot_retention,
                gold_pilot_retention_store, gold_strategy_worker):
            monkeypatch.setattr(module, '_check_path', demo_resident_recovery._check_path)
        monkeypatch.setattr(retention_fixture, 'native_path_check', demo_resident_recovery._check_path)
        assert not local['confirmations']
        warmup_end = local['clock'][0] // STEP * STEP
        # Position the synthetic next close inside the declared 300s capture.
        # All authorizations are still created later, at this advanced time.
        previous_clock = local['clock'][0]
        local['clock'][0] = max(previous_clock, warmup_end+STEP-120000)
        origin = shift(local['parts']['origin'], local['clock'][0]-previous_clock)
        local['parts']['origin'] = origin
        local['value']['origin'] = origin
        assert warmup_end+10 <= local['clock'][0] < warmup_end+STEP
        rows = raw_rows()
        delta = warmup_end - rows[-1][6] - 1
        for row in rows:
            row[0] += delta
            row[6] += delta
        raw = canonical(rows)
        receipt = (json.dumps(dict(requested_at_ms=warmup_end,
            received_at_ms=warmup_end+10, response_sha256=hashlib.sha256(raw).hexdigest()),
            sort_keys=True, allow_nan=False, indent=2)+'\n').encode()
        runtime = deepcopy(local['parts']['runtime'])
        runtime['history_sha256'] = hashlib.sha256(receipt).hexdigest()
        area = local['root']/'strategy-bar-history'/runtime['history_sha256']
        area.parent.mkdir(mode=0o700)
        area.mkdir(mode=0o700)
        for name, body in (('response.json', raw), ('receipt.json', receipt)):
            path = area/name
            path.write_bytes(body)
            path.chmod(0o600)
        runtime_path = local['ctl']/'strategy-runtime.json'
        runtime_path.write_bytes(canonical(runtime))
        runtime_path.chmod(0o600)
        local['parts']['runtime'] = runtime
        local['value']['scope_warmup_end_ms'] = warmup_end
        yield local
    finally:
        original.close()


@pytest.fixture
def native_scope(installed_first_scope, tmp_path, monkeypatch):
    installed = installed_first_scope
    records = installed['records'], installed['scope'], installed['registry']
    return build_native_scope(records, installed['local'], tmp_path, monkeypatch,
        original_issuer_root=installed['issuer_root'])


@pytest.mark.parametrize('cold_scope_local', [False, True], indirect=True,
    ids=['signed-history', 'signed-and-unsigned-history'])
def test_cold_first_scope_preserves_old_btc_history_through_the_first_future_order(
        installed_first_scope, v2_installation, tmp_path, monkeypatch, request):
    installed = installed_first_scope; history = installed['history']
    prefix = history['issuer_prefix']; prior_claims = set(history['issuer_claims'])
    assert prefix and [row['kind'] for row in prefix[:2]] == ['RESERVED', 'SIGNED']
    assert len(prefix) == (3 if history['unsigned_handoff'] is not None else 2)
    if history['unsigned_handoff'] is not None:
        assert prefix[-1]['kind'] == 'RESERVED'
        assert len(prior_claims) == 2
    assert installed['scope']['schema'] == 'DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V2'
    assert installed['records']['manifest']['tasks'] == []
    assert installed['records']['original_setup']['result']['source_commit'] == history['old_policy']['executor_release_commit']
    assert installed['scope']['scope_enrollment']['original_issuer_history']['records'] == len(prefix)
    delivered = build_installed_future_delivery(v2_installation, tmp_path, monkeypatch, request,
        original_issuer_prefix=prefix)
    registered_at = delivered.enable['operator_confirmation']['approved_at_ms']
    assert 0 < delivered.now_ms-registered_at <= delivered.runtime['seconds']*1000
    assert delivered.admin['store'].path == history['issuer_path']
    assert set(issuer_claims(delivered.admin['store'])) == prior_claims
    result = send_first_future(delivered, monkeypatch)
    assert result['execution']['status'] == 'WIRE_ATTEMPTS_RECORDED'
    rows = delivered.admin['store'].snapshot()
    assert rows[:len(prefix)] == prefix
    assert prior_claims < set(issuer_claims(delivered.admin['store']))
    assert history['book_tuple'][1]['canonical_wallet'] == '4999.6801'
    assert result['cash_reconciled'] is False
