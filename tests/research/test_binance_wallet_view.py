import pytest
from services.research.binance_wallet_view import normalize_wallet,project_wallets


def test_stablecoins_remain_separate_and_margin_is_not_locked_cash():
 f=normalize_wallet('futures',{'assets':[{'asset':'USDT','walletBalance':'10','availableBalance':'7','unrealizedProfit':'2','marginBalance':'12','initialMargin':'5'}]})
 assert f[0]['wallet']=='10' and f[0]['locked'] is None and f[0]['margin_used']=='5'
 assert next(r for r in f if r['asset']=='USDC')['wallet'] is None
 s=normalize_wallet('spot',{'balances':[{'asset':'USDT','free':'3','locked':'1'},{'asset':'USDC','free':'8','locked':'0'}]})
 u=normalize_wallet('funding',[{'asset':'USDT','free':'2','locked':'1','freeze':'1','withdrawing':'1'}])
 d={'schema':'BINANCE_WALLET_OBSERVATIONS_V1','observed_at_ms':1000,'submission_enabled':False,
    'accounts':{k:{'status':'OBSERVED','observed_at_ms':1000,'balances':v} for k,v in [('futures',f),('spot',s),('funding',u)]}}
 result=project_wallets(d,now_ms=1000)
 assert result['totals']['USDT']=='19'
 assert result['totals']['USDC'] is None
 d['accounts']['funding']={'status':'UNAVAILABLE','observed_at_ms':1000,'balances':[]}
 assert project_wallets(d,now_ms=1000)['totals']['USDT'] is None
 assert project_wallets(d,now_ms=200000)['status']=='STALE'


def test_duplicates_and_invalid_values_are_rejected():
 with pytest.raises(ValueError):normalize_wallet('spot',{'balances':[{'asset':'USDT','free':'NaN','locked':'0'}]})
 row={'asset':'USDT','free':'1','locked':'0'}
 with pytest.raises(ValueError):normalize_wallet('spot',{'balances':[row,row]})
 assert normalize_wallet('funding',[])[0]['wallet']=='0'
