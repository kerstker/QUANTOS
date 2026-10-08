"""Local AI proposes a bounded observation design before new public capture."""
import hashlib
import json
import os
import re
import time

from quantos.local_artifacts import with_result_hash
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.ai_factory_provider import LocalModelTransport, provider_status


def _encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _design(value,seconds,*,price_design=False):
    bounds={'window_ms':(1000,15000),'max_age_ms':(100,5000),'window_count':(2,6)}
    if price_design:
        from services.research.gold_price_design import PRICE_BOUNDS, validate_price_design
        validate_price_design(value)
        bounds.update(PRICE_BOUNDS)
    if (type(seconds) is not int or not 15<=seconds<=60 or type(value) is not dict
            or set(value)!=set(bounds) or any(type(value[k]) is not int or not lo<=value[k]<=hi
                for k,(lo,hi) in bounds.items())
            or value['window_ms']*value['window_count']+value['max_age_ms']>(seconds-10)*1000):
        raise ValueError('INVALID_GOLD_CAPTURE_DESIGN')
    return dict(value)


def verify_design(receipt,*,seconds):
    fields={'schema','design','seconds','base_rule','model','request_sha256','response_sha256',
            'received_at_ms','authoritative','result_hash'}
    memory=receipt.get('capture_memory') if type(receipt) is dict else None
    research=receipt.get('research_memory') if type(receipt) is dict else None
    if research is not None:fields.add('research_memory')
    price_design=type(receipt) is dict and receipt.get('design_scope')=='ANCHORED_PRICE_REGION_V1'
    if price_design:fields.add('design_scope')
    if memory is not None:fields.add('capture_memory')
    if (type(receipt) is not dict or set(receipt)!=fields
            or receipt['schema']!=('GOLD_AI_CAPTURE_DESIGN_V4' if research is not None else 'GOLD_AI_CAPTURE_DESIGN_V3' if price_design else 'GOLD_AI_CAPTURE_DESIGN_V2' if memory is not None else 'GOLD_AI_CAPTURE_DESIGN_V1') or receipt['seconds']!=seconds
            or receipt['authoritative'] is not False
            or type(receipt['received_at_ms']) is not int or receipt['received_at_ms']<1
            or type(receipt['model']) is not str or not 1<=len(receipt['model'])<=200
            or type(receipt['base_rule']) is not dict or set(receipt['base_rule'])!={'window_ms','max_age_ms'}
            or any(type(v) is not int or v<=0 for v in receipt['base_rule'].values())
            or any(type(receipt[k]) is not str or re.fullmatch('[a-f0-9]{64}',receipt[k]) is None
                   for k in ('request_sha256','response_sha256'))
            or with_result_hash({k:v for k,v in receipt.items() if k!='result_hash'})!=receipt):
        raise ValueError('INVALID_GOLD_CAPTURE_DESIGN_RECEIPT')
    if memory is not None:
        from services.research.gold_capture_memory import validate_capture_memory
        validate_capture_memory(memory)
        if memory['as_of_ms']>receipt['received_at_ms']:raise ValueError('FUTURE_GOLD_CAPTURE_MEMORY')
    if research is not None:
        from services.research.gold_research_memory import validate_memory
        validate_memory(research)
        if not price_design or research['as_of_ms']>receipt['received_at_ms']:
            raise ValueError('INVALID_GOLD_STRATEGY_MEMORY_CONTEXT')
    return _design(receipt['design'],seconds,price_design=price_design)


def propose_design(*,seconds,base_rule,environ=None,transport=None,capture_memory=None,price_design=False,research_memory=None):
    env=os.environ if environ is None else environ
    if provider_status(env)['transport']!='LOCAL_HTTP' or provider_status(env)['status']=='NOT_CONFIGURED':
        raise ValueError('GOLD_CAPTURE_DESIGN_REQUIRES_LOCAL_MODEL')
    if (type(price_design) is not bool or type(seconds) is not int or not 15<=seconds<=60 or type(base_rule) is not dict
            or set(base_rule)!={'window_ms','max_age_ms'}
            or any(type(v) is not int or v<=0 for v in base_rule.values())):
        raise ValueError('INVALID_GOLD_CAPTURE_DESIGN_CONTEXT')
    properties={'window_ms':{'type':'integer','minimum':1000,'maximum':15000},
                'max_age_ms':{'type':'integer','minimum':100,'maximum':5000},
                'window_count':{'type':'integer','minimum':2,'maximum':6}}
    if price_design:
        from services.research.gold_price_design import PRICE_BOUNDS
        properties.update({k:{'type':'integer','minimum':lo,'maximum':hi}
                           for k,(lo,hi) in PRICE_BOUNDS.items()})
    context={'purpose':'DESIGN_NEXT_PUBLIC_CAPTURE_BEFORE_OBSERVING_IT','symbol':'XAUUSDT',
             'capture_seconds':seconds,'base_rule':base_rule,
             'max_design_horizon_ms':(seconds-10)*1000,
             'constraint':'window_ms * window_count + max_age_ms <= max_design_horizon_ms',
             'decision_policy':'FIRST_RECEIVED_WATERMARK_V1'}
    if price_design:
        context.update(price_anchor='MIDPOINT_OF_FIRST_PUBLIC_QUOTE_BEFORE_SIGNAL_WINDOWS',
            price_constraint='stop_bps < zone_lower_bps < zone_upper_bps < target_bps',
            price_units='integer basis points relative to the anchor; size and costs stay fixed')
    if capture_memory is not None:
        from services.research.gold_capture_memory import validate_capture_memory
        capture_memory=validate_capture_memory(capture_memory)
        if capture_memory['as_of_ms']>time.time_ns()//1000000:raise ValueError('FUTURE_GOLD_CAPTURE_MEMORY')
        context['prior_capture_diagnostics']=capture_memory
    if research_memory is not None:
        from services.research.gold_research_memory import validate_memory
        research_memory=validate_memory(research_memory)
        if not price_design or research_memory['as_of_ms']>time.time_ns()//1000000:
            raise ValueError('INVALID_GOLD_STRATEGY_MEMORY_CONTEXT')
        context['prior_technical_experiments']=research_memory
    model=env['QUANTOS_FACTORY_MODEL'].strip()
    instruction='Design a prospective observation schedule for a new gold public capture. Return only JSON containing window_ms, max_age_ms, window_count. Respect every bound and the total horizon. No outcome data are available; this is a research design, not proof of trading merit.'
    if price_design:
        instruction='Design a prospective research recipe for a new gold public capture. Return only JSON containing '+', '.join(properties)+'. Respect every bound and the total horizon. Freeze relative price rules before observing the new capture. No outcome data are available; this is a research design, not proof of trading merit.'
    request={'model':model,'temperature':0,'max_tokens':250,
             'chat_template_kwargs':{'enable_thinking':False},
             'messages':[{'role':'system','content':instruction},
                         {'role':'user','content':json.dumps(context,allow_nan=False)}],
             'response_format':{'type':'json_schema','json_schema':{'name':'gold_capture_design','strict':True,
                 'schema':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}}}}
    raw=_encode(request)
    response=(transport or LocalModelTransport()).post(endpoint=env['QUANTOS_FACTORY_ENDPOINT'].strip(),
        headers={'Content-Type':'application/json'},body=raw,timeout_seconds=60,max_response_bytes=65536)
    if response.status!=200 or len(response.body)>65536:raise ValueError('GOLD_CAPTURE_DESIGN_MODEL_REJECTED')
    try:
        value=load_json_without_duplicate_keys(response.body)
        design=_design(load_json_without_duplicate_keys(value['choices'][0]['message']['content']),seconds,price_design=price_design)
    except (ValueError,KeyError,TypeError,IndexError):
        raise ValueError('INVALID_GOLD_CAPTURE_DESIGN') from None
    receipt=with_result_hash({'schema':'GOLD_AI_CAPTURE_DESIGN_V4' if research_memory is not None else 'GOLD_AI_CAPTURE_DESIGN_V3' if price_design else 'GOLD_AI_CAPTURE_DESIGN_V2' if capture_memory is not None else 'GOLD_AI_CAPTURE_DESIGN_V1',
        **({'research_memory':research_memory} if research_memory is not None else {}),
        **({'design_scope':'ANCHORED_PRICE_REGION_V1'} if price_design else {}),
        **({'capture_memory':capture_memory} if capture_memory is not None else {}),'design':design,'seconds':seconds,
        'base_rule':base_rule,'model':model,'request_sha256':hashlib.sha256(raw).hexdigest(),
        'response_sha256':hashlib.sha256(response.body).hexdigest(),'received_at_ms':time.time_ns()//1000000,
        'authoritative':False})
    verify_design(receipt,seconds=seconds)
    return receipt
