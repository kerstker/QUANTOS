"""Cash completion needs a canonical match and an actual system round trip."""
import json
from copy import deepcopy
import pytest

from tools.demo_account_cash import compare
from services.research.demo_account_recovery import reconcile
from tests.research.test_demo_account_recovery import inputs
from tests.research.test_binance_order_receipts import receipt


def fixture(tmp_path):
    value=inputs(tmp_path)
    o=dict(value['system_orders'][0],newClientOrderId='QDsystemclose',side='SELL',price='59900',reduceOnly=True)
    value['system_orders'].append(o)
    row=value['windows'][1]['messages'][0];p=json.loads(row['raw_utf8'])
    p['o'].update(c=o['newClientOrderId'],o='LIMIT',p=o['price'])
    value['windows'][1]['messages']=[receipt(p,row['received_at_ms'])]
    report,_=reconcile(**value)
    assert report['status']=='REPORTED_ACCOUNT_MATCHED'
    context=dict(ledger_state_hash=report['ledger_state_hash'],canonical_wallet=report['canonical_wallet'],quantity='0')
    return value,context


def test_complete_fixed_order_pair_and_canonical_money_are_both_required(tmp_path):
    value,context=fixture(tmp_path);before=deepcopy(value)
    report,ledger,count=compare(value,context,cycle_order_ids=['21','22'])
    assert count==2 and report['manual_order_ids']==[] and ledger.state_as_canonical_dict()==report['ledger_state']
    assert report['wallet_residual']=='0' and value==before


@pytest.mark.parametrize('damage',['ledger','wallet','one_order','reused_id','foreign_id','manual_attribution'])
def test_cash_agreement_cannot_create_missing_cycle_or_replace_a_book(tmp_path,damage):
    value,context=fixture(tmp_path);ids=['21','22']
    if damage=='ledger':context['ledger_state_hash']='f'*64
    elif damage=='wallet':context['canonical_wallet']='5000'
    elif damage=='one_order':ids=['21']
    elif damage=='reused_id':ids=['21','21']
    elif damage=='foreign_id':ids=['21','24']
    else:context['reported_account_prefix']={'manual_order_ids':['22']}
    with pytest.raises(ValueError):compare(value,context,cycle_order_ids=ids)
