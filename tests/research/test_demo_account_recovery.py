"""Full bounded reported-account recovery keeps manual attribution and lost stream evidence."""
from copy import deepcopy
import json

import pytest

from tests.tools.test_binance_demo_execution_origin import collect as origin_collect,transport as origin_transport,NOW
from tests.tools.test_binance_demo_statement import transport as statement_transport
from tests.research.test_binance_order_receipts import message,receipt
from tools import binance_demo_execution_origin,binance_demo_statement


def inputs(tmp_path):
    opening,_=origin_collect(tmp_path)
    credential_file=next((tmp_path/'credentials').iterdir())
    get,_=origin_transport()
    def closing_get(path,**kwargs):
        value=json.loads(get(path,**kwargs))
        def change(item):
            if isinstance(item,list):return [change(v) for v in item]
            if isinstance(item,dict):
                row={k:(v+60000 if k in ('time','updateTime','serverTime','nextFundingTime') and type(v) is int else change(v)) for k,v in item.items()}
                if row.get('asset')=='USDC':
                    for name in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):row[name]='4999.6801'
                return row
            return item
        return json.dumps(change(value)).encode()
    closing=binance_demo_execution_origin.collect(credential_file=credential_file,symbol='BTCUSDC',operator_read=True,
        get=closing_get,clock_ms=lambda:NOW+60000)
    trades=[];income=[];windows=[]
    for i,(side,price,fee,pnl) in enumerate([('BUY','60000','0.06','0'),('SELL','59900','0.0599','-0.2')],1):
        at=NOW+i*10000
        trades.append(dict(symbol='BTCUSDC',id=i,orderId=20+i,side=side,positionSide='BOTH',price=price,qty='0.002',
            quoteQty='120' if i==1 else '119.8',realizedPnl=pnl,commission=fee,commissionAsset='USDC',time=at,maker=False))
        income.append(dict(symbol='BTCUSDC',incomeType='COMMISSION',income='-'+fee,asset='USDC',time=at,tranId=i,tradeId=str(i)))
        if i==2:income.append(dict(symbol='BTCUSDC',incomeType='REALIZED_PNL',income=pnl,asset='USDC',time=at,tranId=3,tradeId='2'))
        event=message(at,'FILLED','0.002','0.002',trade_id=i,fee=fee,asset='USDC')
        event['o'].update(s='BTCUSDC',i=20+i,c='QDoriginalopen' if i==1 else 'web_manual_close',S=side,
            o='LIMIT' if i==1 else 'MARKET',q='0.002',L=price,p=price if i==1 else '0',f='IOC',m=False,rp=pnl,R=i==2)
        windows.append(dict(account_ref_sha256=opening['account_ref_sha256'],start_ms=at-5000,end_ms=at+5000,
            messages=[receipt(event,at+5)],gaps=[{'at_ms':at+6,'reason':'MESSAGE_REJECTED','response_sha256':str(i)*64}]))
    def statement(at):
        original=statement_transport([])
        def get(path,**kwargs):
            if path=='/fapi/v1/userTrades':value=trades
            elif path=='/fapi/v1/income':value=income
            elif path=='/fapi/v1/time':value={'serverTime':at}
            else:value=json.loads(original(path,**kwargs).decode().replace('USDT','USDC'))
            return json.dumps(value).encode()
        return binance_demo_statement.collect(credential_file=credential_file,symbols=['BTCUSDC'],
            start_ms=NOW-6000,end_ms=NOW+66000,operator_read=True,get=get,clock=lambda:at)
    order=dict(symbol='BTCUSDC',side='BUY',type='LIMIT',timeInForce='IOC',quantity='0.002',price='60000',
        reduceOnly=False,positionSide='BOTH',newClientOrderId='QDoriginalopen',newOrderRespType='ACK')
    return dict(opening=opening,closing=closing,statements=[statement(NOW+76000),statement(NOW+86000)],
        windows=windows,system_orders=[order],adv_notional='1000000',participation_rate='0.001')


def test_reported_account_recovery_replays_both_fills_once_with_manual_attribution(tmp_path):
    from services.research.demo_account_recovery import reconcile
    value=inputs(tmp_path);before=deepcopy(value)
    report,ledger=reconcile(**value)
    assert report['status']=='REPORTED_ACCOUNT_MATCHED'
    assert report['final_quantity']=='0' and report['canonical_wallet']=='4999.6801'
    assert report['wallet_residual']=='0' and report['reported_fee']=='0.1199'
    assert report['reported_realized_pnl']=='-0.2' and report['net_after_fees']=='-0.3199'
    assert report['manual_order_ids']==['22'] and report['system_order_ids']==['21']
    assert len(report['stream_gaps'])==2 and report['stream_coverage_repaired'] is False
    assert report['automatic_cycle_completed'] is False and report['economic_authority'] is False
    assert ledger.state_as_canonical_dict()==report['ledger_state'] and value==before


@pytest.mark.parametrize('damage',['account','wallet','second_sweep','missing_native','missing_fee','manual_open','boundary','same_quote_flow'])
def test_insufficient_or_conflicting_inputs_never_produce_account_context(tmp_path,damage):
    from services.research.demo_account_recovery import reconcile
    value=inputs(tmp_path)
    if damage=='account':value['windows'][1]['account_ref_sha256']='f'*64
    elif damage=='wallet':
        for read in value['closing']['reads']:
            if read['kind'].startswith('account'):
                for asset in read['data']['assets']:
                    if asset['asset']=='USDC':
                        for key in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):asset[key]='5000'
    elif damage=='second_sweep':value['statements'][1]=deepcopy(value['statements'][0])
    elif damage=='missing_native':value['windows'][1]['messages']=[]
    elif damage=='missing_fee':
        for raw in value['statements']:
            raw['income']=raw['income'][:-1]
    elif damage=='manual_open':
        event=json.loads(value['windows'][1]['messages'][0]['raw_utf8']);event['o']['R']=False
        value['windows'][1]['messages']=[receipt(event,NOW+20005)]
    elif damage=='boundary':value['windows'][1]['start_ms']=NOW+20010
    else:
        value['system_orders'][0]['symbol']='ETHUSDC'
    try:report,ledger=reconcile(**value)
    except ValueError:return
    assert report['status']=='UNRESOLVED' and ledger is None
