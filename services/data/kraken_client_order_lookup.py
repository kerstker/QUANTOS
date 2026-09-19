"""Bounded client-ID discovery; an empty lookup never proves non-submission."""
from decimal import Context, localcontext
import re

from services.data.kraken_account import payload, number
from services.data.kraken_order_identity import _uuid
from services.data.kraken_public import require


def validate_params(endpoint, params):
    expected={'cl_ord_id','trades','rebase_multiplier'}
    if endpoint=='ClosedOrders':expected|={'start','end','ofs','closetime'}
    require(endpoint in ('OpenOrders','ClosedOrders') and isinstance(params,dict) and set(params)==expected and
            params['trades']=='true' and params['rebase_multiplier']=='base' and
            isinstance(params['cl_ord_id'],str) and re.fullmatch('[a-f0-9]{32}',params['cl_ord_id']),
            'KRAKEN_CLIENT_LOOKUP_SCOPE')
    if endpoint=='ClosedOrders':
        require(type(params['start']) is int and type(params['end']) is int and
                0<params['start']<params['end']<=params['start']+86402 and
                type(params['ofs']) is int and params['ofs']==0 and params['closetime']=='open',
                'KRAKEN_CLIENT_LOOKUP_WINDOW')
    return params


def requests(request):
    require(isinstance(request,dict) and set(request)=={'client_order_id','not_before_ms','deadline_ms'},
            'KRAKEN_CLIENT_LOOKUP_REQUEST')
    start,end=request['not_before_ms'],request['deadline_ms']
    require(type(start) is int and type(end) is int and 1000<start<end<=start+86400000,
            'KRAKEN_CLIENT_LOOKUP_TIME')
    common={'cl_ord_id':request['client_order_id'],'trades':'true','rebase_multiplier':'base'}
    closed={**common,'start':start//1000-1,'end':end//1000+1,'ofs':0,'closetime':'open'}
    return [(endpoint,validate_params(endpoint,p)) for endpoint,p in [('OpenOrders',common),('ClosedOrders',closed)]]


def assess(reads, request, *, as_of_ms):
    expected=requests(request)
    require(isinstance(reads,list) and len(reads)==2 and type(as_of_ms) is int,'KRAKEN_CLIENT_LOOKUP_READS')
    ref=None;previous=0;found=set();creation_times={}
    with localcontext(Context(prec=100)):
        for read,(endpoint,params) in zip(reads,expected):
            require(read['endpoint']==endpoint and read['params']==params,'KRAKEN_CLIENT_LOOKUP_READ_SCOPE')
            account=read['account_ref_sha256']
            require(isinstance(account,str) and re.fullmatch('[a-f0-9]{64}',account) and
                    (ref is None or account==ref),'KRAKEN_CLIENT_LOOKUP_ACCOUNT')
            ref=account;sent=read['requested_at_ms'];received=read['received_at_ms']
            require(type(sent) is int and type(received) is int and 0<sent<=received<=as_of_ms and
                    previous<=sent and received-sent<=30000 and as_of_ms-sent<=120000,
                    'KRAKEN_CLIENT_LOOKUP_CLOCK')
            previous=received;value=payload(read['body']);field='open' if endpoint=='OpenOrders' else 'closed'
            require(set(value)==({field} if field=='open' else {field,'count'}) and
                    isinstance(value[field],dict) and len(value[field])<=50,'KRAKEN_CLIENT_LOOKUP_RESPONSE')
            if field=='closed':
                require(type(value['count']) is int and value['count']==len(value[field]),'KRAKEN_CLIENT_LOOKUP_INCOMPLETE')
            for oid,row in value[field].items():
                require(isinstance(oid,str) and re.fullmatch('[A-Za-z0-9-]{1,80}',oid) and
                        isinstance(row,dict) and _uuid(row.get('cl_ord_id'))==request['client_order_id'],
                        'KRAKEN_CLIENT_LOOKUP_IDENTITY')
                require(row.get('status') in (('pending','open') if field=='open' else ('closed','canceled','expired')),
                        'KRAKEN_CLIENT_LOOKUP_STATUS')
                opened=number(row['opentm'])*1000
                require(oid not in creation_times or creation_times[oid]==opened,'KRAKEN_CLIENT_LOOKUP_ORDER_CHANGED')
                creation_times[oid]=opened
                require(opened<=received,'KRAKEN_CLIENT_LOOKUP_FUTURE_ORDER')
                if request['not_before_ms']<=opened<=request['deadline_ms']:found.add(oid)
    return {'schema':'KRAKEN_CLIENT_ORDER_LOOKUP_V1','account_ref_sha256':ref,'as_of_ms':as_of_ms,
            'status':'ORDER_ID_OBSERVED' if len(found)==1 else 'UNRESOLVED',
            'order_id':next(iter(found)) if len(found)==1 else None,'candidate_ids':sorted(found),
            'absence_proven':False,'resubmission_allowed':False,'submission_enabled':False,'authoritative':False}
