"""Content-bound gold trial names; no strategy promotion or permission."""
from copy import deepcopy
import re
from services.research.ai_factory_store import digest

SCHEMA='GOLD_RESEARCH_EXECUTION_IDENTITY_V1'
_FIELDS='schema attempt candidate_id trial_id definition_id base_input_sha256 input_sha256 report_sha256 research_origin_sha256 manifest_sha256 review_request_sha256 available_at_ms scope economic_eligible submission_enabled'


def identify(body):
    if type(body) is not dict or set(body)!=set(_FIELDS.split()):
        raise ValueError('GOLD_EXECUTION_IDENTITY_FIELDS')
    if (body['schema']!=SCHEMA or body['scope']!='TECHNICAL_RESEARCH_IDENTITY_ONLY' or
        body['economic_eligible'] is not False or body['submission_enabled'] is not False):
        raise ValueError('GOLD_EXECUTION_IDENTITY_SCOPE')
    for k,low,high in (('attempt',1,1000),('candidate_id',0,5),('available_at_ms',1,8640000000000000)):
        if type(body[k]) is not int or not low<=body[k]<=high:
            raise ValueError('GOLD_EXECUTION_IDENTITY_BOUND')
    for k in ('trial_id','definition_id','base_input_sha256','input_sha256','report_sha256','research_origin_sha256','manifest_sha256','review_request_sha256'):
        if type(body[k]) is not str or re.fullmatch('[a-f0-9]{64}',body[k]) is None:
            raise ValueError('GOLD_EXECUTION_IDENTITY_HASH')
    result=deepcopy(body)|{'strategy_id':'gold-research-'+digest(body)}
    return result|{'result_hash':digest(result)}


def validate_identity(value,*,as_of_ms,strategy_id):
    if type(value) is not dict or set(value)!=set(_FIELDS.split())|{'strategy_id','result_hash'}:
        raise ValueError('GOLD_EXECUTION_IDENTITY_FIELDS')
    if identify({k:value[k] for k in _FIELDS.split()})!=value:
        raise ValueError('GOLD_EXECUTION_IDENTITY_CHANGED')
    if type(as_of_ms) is not int or value['available_at_ms']>as_of_ms:
        raise ValueError('GOLD_EXECUTION_IDENTITY_FUTURE')
    if value['strategy_id']!=strategy_id:
        raise ValueError('GOLD_EXECUTION_STRATEGY_MISMATCH')
    return deepcopy(value)
