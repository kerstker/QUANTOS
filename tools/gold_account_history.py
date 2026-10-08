"""Fixed GET-only history adapter. Called only inside a human observation session."""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from services.research.gold_trade_observation import load_document, normalize_binance_trades, _require, _time
from services.research.gold_account_history import normalize_income, normalize_orders
from tools.gold_account_capture import NoRedirect

PATHS=frozenset(('/fapi/v1/userTrades','/fapi/v1/income','/fapi/v1/allOrders','/fapi/v1/allAlgoOrders'))
QUERY_PATHS=frozenset(('/fapi/v1/order','/fapi/v1/algoOrder'))


def fetch_window(read,path,start_ms,end_ms,*,limit=1000,max_pages=64,on_page=None):
    _require(path in PATHS and 0<=_time(start_ms)<_time(end_ms) and end_ms-start_ms<=7*86400000,'INVALID_HISTORY_SCOPE')
    _require(type(limit) is int and 1<=limit<=1000 and type(max_pages) is int and 1<=max_pages<=128,'INVALID_BUDGET')
    result=[];evidence=[];seen=set()
    def page(lo,hi,number=None):
        _require(len(evidence)<max_pages,'PAGE_BUDGET')
        params={'startTime':lo,'endTime':hi-1,'limit':limit}
        if not path.endswith('income'):params['symbol']='XAUUSDT'
        else:params['page']=number
        rows=read(path,params)
        if not isinstance(rows,list) or len(rows)>limit:
            raise HistoryContractFailure({'endpoint':path,'reason':'INVALID_HISTORY_PAGE',
                'shape':'OBJECT' if isinstance(rows,dict) else 'LIST' if isinstance(rows,list) else 'OTHER'})
        algo_states=path.endswith('allAlgoOrders')
        _require(all(isinstance(r,dict) for r in rows),'INVALID_HISTORY_PAGE')
        outside=sum(not lo<=_time(r['createTime' if algo_states else 'time'])<hi for r in rows)
        # Real algo responses can include orders created before this window.
        # Retain them as current state observations, never window executions.
        _require(algo_states or outside==0,'HISTORY_OUTSIDE_WINDOW')
        digest=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        proof={'path':path,'params':params,'count':len(rows),'response_sha256':digest}
        if algo_states:proof.update(scope='ORDER_STATE_OBSERVATIONS',outside_creation_window=outside)
        evidence.append(proof)
        if on_page:on_page(proof)
        _require(not rows or digest not in seen,'REPEATED_PAGE');seen.add(digest)
        return rows
    if path.endswith('income'):
        for number in range(1,max_pages+1):
            rows=page(start_ms,end_ms,number);result.extend(rows)
            if len(rows)<limit:break
        else:raise ValueError('PAGE_BUDGET')
    else:
        pending=[(start_ms,end_ms)]
        while pending:
            lo,hi=pending.pop();rows=page(lo,hi)
            if len(rows)==limit:
                _require(not path.endswith('allAlgoOrders'),'ORDER_STATE_PAGE_SATURATED')
                _require(hi-lo>1,'SATURATED_MILLISECOND')
                mid=(lo+hi)//2;pending.extend(((mid,hi),(lo,mid)))
            else:result.extend(rows)
    _require(len(result)<=20000,'HISTORY_ROW_BUDGET')
    return result,evidence


class HistoryContractFailure(ValueError):
    def __init__(self,diagnostic):
        super().__init__('HISTORY_CONTRACT_FAILED')
        self.diagnostic=diagnostic


def contract(path,callback,*args,**kwargs):
    try:return callback(*args,**kwargs)
    except (HistoryContractFailure,HistoryRequestFailure):raise
    except (ValueError,KeyError,TypeError) as exc:
        fields={'time','updateTime','createTime','symbol','orderId','algoId','side','positionSide','status','type',
                'origQty','executedQty','price','quantity','triggerPrice','algoStatus','orderType'}
        codes={'INVALID_TOKEN','INVALID_TIME','INVALID_ORDER_STATUS','INVALID_ORDER_ID','ORDER_SCOPE_MISMATCH',
               'INVALID_ORDER_ROWS','NEGATIVE_ORDER_AMOUNT','OVERFILLED_ORDER','CONFLICTING_ORDER_ID','INVALID_ACTUAL_ORDER_ID',
               'INVALID_AMOUNT','NONPOSITIVE_AMOUNT','HISTORY_OUTSIDE_WINDOW','REPEATED_PAGE','INVALID_HISTORY_PAGE'}
        diagnostic={'endpoint':path,'reason':str(exc) if isinstance(exc,ValueError) and str(exc) in codes else 'ORDER_CONTRACT_INVALID'}
        if type(exc) is KeyError and exc.args and exc.args[0] in fields:diagnostic['missing_field']=exc.args[0]
        raise HistoryContractFailure(diagnostic) from None


class HistoryRequestFailure(ValueError):
    def __init__(self,diagnostic):
        super().__init__('HISTORY_REQUEST_REJECTED')
        self.diagnostic=diagnostic


def signed_reader(api_key,api_secret,*,clock_ms=None,pause=None):
    clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000);pause=pause or time.sleep
    previous=clock_ms();deadline=time.monotonic()+120
    def read(path,params):
        nonlocal previous
        _require(path in PATHS|QUERY_PATHS,'ENDPOINT_NOT_ALLOWED')
        _require(set(params)<= {'startTime','endTime','limit','symbol','page','orderId','algoId'} and
                 all(type(v) is int for k,v in params.items() if k!='symbol') and
                 ('symbol' not in params or params['symbol']=='XAUUSDT'),'PARAMETERS_NOT_ALLOWED')
        _require(time.monotonic()<deadline,'HISTORY_TIME_BUDGET')
        pause(1 if path.endswith('income') else 0.25)
        now=clock_ms();_require(now>=previous,'CLOCK_UNRELIABLE');previous=now
        query=urllib.parse.urlencode(params|{'timestamp':now,'recvWindow':5000})
        signature=hmac.new(api_secret.encode(),query.encode(),hashlib.sha256).hexdigest()
        request=urllib.request.Request('https://fapi.binance.com'+path+'?'+query+'&signature='+signature,
            headers={'X-MBX-APIKEY':api_key},method='GET')
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=10) as response:
                raw=response.read(2_000_001)
                _require(response.status==200 and len(raw)<=2_000_000,'HISTORY_RESPONSE_INVALID')
            return load_document(raw)
        except urllib.error.HTTPError as exc:
            code=None
            try:
                raw=exc.read(16385)
                parsed=load_document(raw) if len(raw)<=16384 else {}
                value=parsed.get('code') if isinstance(parsed,dict) else None
                if type(value) is int and -99999<=value<0:code=value
            except Exception:pass
            finally:exc.close()
            if type(exc.code) is int and 400<=exc.code<=599:
                raise HistoryRequestFailure({'endpoint':path,'http_status':exc.code,'exchange_code':code}) from None
            raise ValueError('HISTORY_REQUEST_FAILED') from None
        except Exception:raise ValueError('HISTORY_REQUEST_FAILED') from None
    return read


def collect_history(read,*,account_ref,start_ms,end_ms,exported_ms,on_page=None,known_orders=(),known_algos=()):
    passes=[];proofs=[]
    for _ in range(2):
        native,p=fetch_window(read,'/fapi/v1/userTrades',start_ms,end_ms,on_page=on_page);proofs.extend(p)
        fills=normalize_binance_trades({'schema':'GOLD_BINANCE_USER_TRADES_V1','venue':'BINANCE',
            'environment':'MAINNET','symbol':'XAUUSDT','account_ref':account_ref,
            'start_ms':start_ms,'end_ms':end_ms,'exported_at_ms':exported_ms,'fills':native})
        fills['fills']=sorted({r['trade_id']:r for r in fills['fills']}.values(),key=lambda r:int(r['trade_id']))
        native,p=fetch_window(read,'/fapi/v1/income',start_ms,end_ms,on_page=on_page);proofs.extend(p)
        cash=normalize_income(native,start_ms=start_ms,end_ms=end_ms)
        order_sets={}
        for algo,known in ((False,known_orders),(True,known_algos)):
            path='/fapi/v1/allAlgoOrders' if algo else '/fapi/v1/allOrders'
            native,p=contract(path,fetch_window,read,path,start_ms,end_ms,on_page=on_page);proofs.extend(p)
            orders=contract(path,normalize_orders,native,algo=algo);id_key='algoId' if algo else 'orderId'
            ids={r[id_key] for r in orders}
            _require(len(known)<=100,'KNOWN_ORDER_BUDGET')
            for order in known:
                if order['symbol']!='XAUUSDT' or order[id_key] in ids:continue
                path='/fapi/v1/algoOrder' if algo else '/fapi/v1/order'
                params={id_key:order[id_key]}
                if not algo:params['symbol']='XAUUSDT'
                raw=read(path,params)
                normalized=contract(path,normalize_orders,[raw],algo=algo)[0]
                _require(normalized[id_key]==order[id_key],'QUERY_ID_MISMATCH')
                orders.append(normalized);ids.add(normalized[id_key])
            order_sets['algos' if algo else 'orders']=sorted(orders,key=lambda r:r[id_key])
        passes.append({'fills':fills,'income':cash,**order_sets})
    return {'schema':'GOLD_ACCOUNT_HISTORY_V1','status':'STABLE_WINDOW_READS' if passes[0]==passes[1] else 'CHANGED_DURING_READ',
            'passes':passes,'pages':proofs,'history_complete':False}
