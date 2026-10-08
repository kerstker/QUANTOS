"""Bounded private rejection evidence; never an accepted receipt or gap repair.

Only recognized financial envelopes with safe scalar vocabularies retain raw
bytes. Unknown text, malformed JSON and lease/credential fields retain hashes
only. No rejected content is copied into public views or service logs.
"""
import hashlib
import re

from live.execution.demo_pre_submit import _retain
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_user_stream import MAX_MESSAGE_BYTES
from tools.demo_resident_recovery import _check_path

MAX_RECORDS=256
MAX_BYTES=16*1024*1024
KINDS={'ACCOUNT_UPDATE','ORDER_TRADE_UPDATE','ACCOUNT_CONFIG_UPDATE','MARGIN_CALL','TRADE_LITE'}
WORDS={'BUY','SELL','BOTH','LONG','SHORT','cross','isolated','CROSSED','ISOLATED','ORDER','FUNDING_FEE',
    'DEPOSIT','WITHDRAW','TRANSFER','MARKET','LIMIT','STOP','STOP_MARKET','TAKE_PROFIT','TAKE_PROFIT_MARKET',
    'TRAILING_STOP_MARKET','IOC','GTC','GTX','FOK','GTD','NEW','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED',
    'EXPIRED_IN_MATCH','TRADE','CALCULATED','AMENDMENT','REJECTED','CONTRACT_PRICE','MARK_PRICE','NONE'}|KINDS


def safe_financial_raw(raw):
    if type(raw) is not str or len(raw.encode())>MAX_MESSAGE_BYTES:return None
    try:
        value=load_json_without_duplicate_keys(raw)
        if type(value) is not dict or value.get('e') not in KINDS:return None
        def walk(item,key='',depth=0):
            if depth>8:return False
            if type(item) is dict:
                return len(item)<=64 and all(type(k) is str and re.fullmatch('[A-Za-z]{1,12}',k) and
                    not any(s in k.lower() for s in ('key','secret','token','password','signature','auth')) and
                    walk(v,k,depth+1) for k,v in item.items())
            if type(item) is list:return len(item)<=2000 and all(walk(v,key,depth+1) for v in item)
            if item is None or type(item) is bool:return True
            if type(item) is int:return abs(item)<=2**53-1
            if type(item) is not str:return False
            if re.fullmatch(r'-?\d{1,20}(\.\d{1,18})?',item) or item in WORDS:return True
            if key in ('s','a','S','N') and re.fullmatch('[A-Z][A-Z0-9]{0,19}',item):return True
            # Client IDs have a documented bounded identifier format. An
            # unexpected string in any other field is never treated as money.
            return key=='c' and re.fullmatch(r'[.A-Z:/a-z0-9_-]{1,36}',item) is not None
        return raw if walk(value) else None
    except (ValueError,TypeError,UnicodeError,RecursionError):return None


def retain(root,*,raw,account_ref_sha256,source_sha256,at_ms,diagnostic):
    directory=root/'receipt-reconciliation'/'rejected-stream'
    uid=root.stat().st_uid
    for parent in (directory.parent,directory):
        parent.mkdir(mode=0o700,exist_ok=True);_check_path(parent,uid,directory=True)
    count=size=0
    for path in directory.iterdir():
        _check_path(path,uid,directory=False)
        count+=1;size+=path.stat().st_size
        if count>=MAX_RECORDS or size>MAX_BYTES-MAX_MESSAGE_BYTES*2:
            raise ValueError('REJECTION_RETENTION_BUDGET')
    if count>=MAX_RECORDS:raise ValueError('REJECTION_RETENTION_BUDGET')
    kept=safe_financial_raw(raw)
    encoded=raw.encode() if isinstance(raw,str) else raw
    value={'schema':'DEMO_REJECTED_STREAM_EVIDENCE_V1','account_ref_sha256':account_ref_sha256,
        'source_sha256':source_sha256,'at_ms':at_ms,'response_sha256':hashlib.sha256(encoded).hexdigest(),
        'raw_utf8':kept,'diagnostic':diagnostic,'status':'RETAINED_FOR_OFFLINE_REVIEW' if kept is not None else 'HASH_ONLY_UNSAFE_PAYLOAD',
        'accepted_receipt':False,'gap_resolved':False,'economic_authority':False}
    return _retain(directory,value)
