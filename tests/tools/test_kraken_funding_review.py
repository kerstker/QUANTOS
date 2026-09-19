from copy import deepcopy
import pytest

from tests.data.test_kraken_funding import inputs, NOW
from tests.live.test_kraken_wire import order
from tools.kraken_funding_review import build
from tools.kraken_public_capture import digest


def fixture():
    account, requirements = inputs()
    account['result_hash'] = digest(account)
    draft = order(); draft['client_order_id'] = requirements['orders'][0]['client_order_id']
    requirements['orders'][0]['draft_sha256'] = digest(draft)
    return account, requirements, [draft]


def test_funding_binds_account_requirements_and_actual_draft_without_authority():
    account, requirements, drafts = fixture()
    result = build(account, requirements, drafts, as_of_ms=NOW+600)
    assert result['status'] == 'CONDITIONAL_BUDGET_FIT'
    assert result['account_source_hash'] == account['result_hash']
    assert result['requirements_hash'] == digest(requirements)
    assert result['draft_hashes'][drafts[0]['client_order_id']] == digest(drafts[0])
    assert not result['submission_enabled'] and not result['reservation_created']


@pytest.mark.parametrize('change', ['amount', 'account', 'duplicate', 'missing', 'unknown_fee'])
def test_no_reuse_after_draft_or_account_change(change):
    account, requirements, drafts = fixture()
    if change == 'amount': drafts[0]['volume'] = '123'
    if change == 'account': account['balances']['ZUSD']['balance'] = '100000'
    if change == 'duplicate': drafts.append(deepcopy(drafts[0]))
    if change == 'missing': drafts = []
    if change == 'unknown_fee':
        requirements['orders'][0]['fee_debits'] = None
        assert build(account, requirements, drafts, as_of_ms=NOW+600)['status'] == 'BLOCKED'
        return
    with pytest.raises(ValueError): build(account, requirements, drafts, as_of_ms=NOW+600)
