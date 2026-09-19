"""Observed account fee schedule; neither fee-asset proof nor execution permission."""
import hashlib
import re

from services.data.kraken_account import payload, number, canonical
from services.data.kraken_public import require


def validate_params(value):
    require(isinstance(value,dict) and set(value)=={'pair','fee_schedule','rebase_multiplier'} and
            value['fee_schedule'] is False and value['rebase_multiplier']=='base','KRAKEN_FEE_QUERY_SCOPE')
    pairs=value['pair']
    require(isinstance(pairs,list) and len(pairs)==1,'KRAKEN_FEE_QUERY_SIZE')
    item=pairs[0]
    require(isinstance(item,dict) and set(item)=={'asset','aclass'} and
            item['aclass'] in ('forex','equity_pair') and isinstance(item['asset'],str) and
            re.fullmatch(r'[A-Za-z0-9.]{1,20}/[A-Za-z0-9.]{1,20}',item['asset']), 'KRAKEN_FEE_QUERY_PRODUCT')
    return value


def params(spec):
    require(spec['asset_class'] in ('currency','tokenized_asset'),'KRAKEN_FEE_PRODUCT_CLASS')
    return validate_params({'pair':[{'asset':spec['pair'],
        'aclass':'equity_pair' if spec['asset_class']=='tokenized_asset' else 'forex'}],
        'fee_schedule':False,'rebase_multiplier':'base'})


def _schedule(row):
    require(isinstance(row,dict) and {'fee','minfee','maxfee'}<=set(row),'KRAKEN_FEE_SCHEDULE')
    rates={k:number(row[k],signed=True) for k in ('fee','minfee','maxfee')}
    require(-100<=rates['minfee']<=rates['fee']<=rates['maxfee']<100,'KRAKEN_FEE_SCHEDULE_RANGE')
    return {k:canonical(v) for k,v in rates.items()}


def assess(read, *, spec, as_of_ms):
    require(read['endpoint']=='TradeVolume' and validate_params(read['params'])==params(spec),'KRAKEN_FEE_REQUEST_BINDING')
    ref=read['account_ref_sha256'];require(isinstance(ref,str) and re.fullmatch('[a-f0-9]{64}',ref),'KRAKEN_FEE_ACCOUNT')
    sent,received=read['requested_at_ms'],read['received_at_ms']
    require(type(as_of_ms) is int and type(sent) is int and type(received) is int and
            0<sent<=received<=as_of_ms and received-sent<=30000 and as_of_ms-sent<=120000,'KRAKEN_FEE_TIME')
    value=payload(read['body']);key=spec['pair_key'];taker=value.get('fees');maker=value.get('fees_maker',{})
    require(isinstance(taker,dict) and set(taker)=={key} and isinstance(maker,dict) and
            set(maker) in (set(),{key}),'KRAKEN_FEE_RESPONSE_PRODUCT')
    return {'schema':'KRAKEN_FEE_OBSERVATION_V1','status':'OBSERVED_FEE_SCHEDULE',
        'account_ref_sha256':ref,'instrument_id':spec['instrument_id'],'as_of_ms':as_of_ms,
        'received_at_ms':received,'source_body_sha256':hashlib.sha256(read['body'].encode()).hexdigest(),
        'taker_or_flat':_schedule(taker[key]),'maker':_schedule(maker[key]) if maker else None,
        'fee_asset_status':'UNKNOWN','submission_enabled':False,'authoritative':False,
        'scope':'OBSERVED_ACCOUNT_FEE_SCHEDULE_ONLY'}
