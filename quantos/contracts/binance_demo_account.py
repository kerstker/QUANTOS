"""Whitelisted account observations, explicitly not an atomic exchange snapshot."""
from decimal import Decimal
import re
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator
from quantos.contracts.binance_demo import StrictModel, Timestamp

READ_KINDS = ('config_before','account_before','symbols_before','positions','orders',
              'symbols_after','account_after','config_after')
ReadKind = Literal['config_before','account_before','symbols_before','positions','orders',
                   'symbols_after','account_after','config_after']


def _decimal(value):
    if (not isinstance(value,str) or not re.fullmatch(r'-?\d{1,30}(\.\d{1,24})?',value)
            or abs(Decimal(value))>Decimal('1e15')): raise ValueError('exact bounded decimal text required')
    return value


def _text(value, pattern=r'[A-Z0-9_]{1,32}'):
    if not isinstance(value,str) or not re.fullmatch(pattern,value): raise ValueError('invalid identity')
    return value


def _symbol(value):
    # Demo also lists Han-named contracts. Preserve identities without allowing
    # whitespace, separators, controls or bidirectional formatting characters.
    return _text(value, r'[A-Z0-9_\u3400-\u9fff]{1,32}')


def _rows(raw, fields, identity, *, max_rows=2000):
    if not isinstance(raw,list) or len(raw)>max_rows: raise ValueError('invalid bounded rows')
    rows=[]
    for row in raw:
        if not isinstance(row,dict): raise ValueError('row must be an object')
        result={name: parser(row[name]) for name,parser in fields.items()}
        rows.append(result)
    keys=[tuple(row[k] for k in identity) for row in rows]
    if len(set(keys))!=len(keys): raise ValueError('duplicate identity')
    return sorted(rows,key=lambda row: tuple(row[k] for k in identity))


def _bool(value):
    if type(value) is not bool: raise ValueError('boolean required')
    return value


def _int(value):
    if type(value) is not int or not 0<=value<=2**63-1: raise ValueError('bounded integer required')
    return value


def _side(value):
    if value not in ('BOTH','LONG','SHORT'): raise ValueError('unknown position side')
    return value


def normalize_account_read(kind, raw):
    try:
        if kind.startswith('config'):
            return {k:_bool(raw[k]) for k in ('canTrade','dualSidePosition','multiAssetsMargin')}
        if kind.startswith('account'):
            assets=_rows(raw['assets'], {'asset':_text, **dict.fromkeys(('walletBalance','crossWalletBalance',
                'availableBalance','unrealizedProfit','marginBalance'),_decimal)}, ('asset',),max_rows=500)
            positions=_rows(raw['positions'],{'symbol':_symbol,'positionSide':_side,'positionAmt':_decimal},('symbol','positionSide'))
            return {'assets':assets,'positions':positions}
        if kind.startswith('symbols'):
            rows=_rows(raw,{'symbol':_symbol,'marginType':_text,'leverage':_int},('symbol',))
            if any(r['marginType'] not in ('CROSSED','ISOLATED') or not 1<=r['leverage']<=1000 for r in rows):
                raise ValueError('invalid symbol configuration')
            return rows
        if kind=='positions':
            rows=_rows(raw,{'symbol':_symbol,'positionSide':_side,'marginAsset':_text,'updateTime':_int,
                **dict.fromkeys(('positionAmt','entryPrice','markPrice','unRealizedProfit','isolatedWallet'),_decimal)},('symbol','positionSide'))
            if any(Decimal(r['isolatedWallet'])<0 or (Decimal(r['positionAmt'])!=0 and
                    (Decimal(r['entryPrice'])<=0 or Decimal(r['markPrice'])<=0)) for r in rows):
                raise ValueError('invalid opening position prices or allocation')
            return rows
        if kind=='orders':
            rows = _rows(raw,{'symbol':_symbol,'orderId':lambda v:str(_int(v)),
                'clientOrderId':lambda v:_text(v,r'[.A-Z:/a-z0-9_-]{1,36}'),
                'positionSide':_side,'side':_text,'status':_text,'origQty':_decimal,'executedQty':_decimal},('symbol','orderId'))
            if any(int(r['orderId']) <= 0 or r['side'] not in ('BUY','SELL') or
                   r['status'] not in ('NEW','PARTIALLY_FILLED','PENDING_CANCEL') or
                   not 0 <= Decimal(r['executedQty']) <= Decimal(r['origQty']) or
                   Decimal(r['origQty']) <= 0 for r in rows):
                raise ValueError('invalid open order')
            return rows
        raise ValueError('unknown account observation')
    except (KeyError,TypeError,ArithmeticError):
        raise ValueError('invalid account observation fields') from None


class AccountRead(StrictModel):
    kind: ReadKind
    started_at_ms: Timestamp
    received_at_ms: Timestamp
    response_sha256: Annotated[str,Field(pattern=r'^[0-9a-f]{64}$')]
    data: dict[str,Any] | list[dict[str,Any]]

    @model_validator(mode='after')
    def valid_observation(self):
        # Native integer IDs are converted to decimal strings at collection;
        # subsequent parsing verifies that exact canonical representation.
        raw=self.data
        if self.kind=='orders':
            raw=[dict(row,orderId=int(row['orderId'])) for row in raw]
        if normalize_account_read(self.kind,raw)!=self.data or self.received_at_ms<self.started_at_ms:
            raise ValueError('noncanonical or backward observation')
        return self


class DemoAccountCapture(StrictModel):
    schema_version: Literal['binance-demo-account-capture.v1']='binance-demo-account-capture.v1'
    environment: Literal['BINANCE_USDM_DEMO']='BINANCE_USDM_DEMO'
    observed_at_ms: Timestamp
    completed_at_ms: Timestamp
    source_sha256: Annotated[str,Field(pattern=r'^[0-9a-f]{64}$')]
    status: Literal['NOT_REQUESTED','CAPTURED','FAILED']
    reason: Literal['ACCOUNT_NOT_REQUESTED','CAPTURE_COMPLETE','REQUEST_FAILED','RESPONSE_INVALID',
                    'REQUEST_TIMEOUT','CLOCK_UNRELIABLE','AUTHENTICATION_REJECTED','RATE_LIMITED',
                    'CREDENTIAL_FILE_INVALID','CREDENTIAL_FILE_PERMISSIONS','REDIRECT_REFUSED','RESPONSE_TOO_LARGE']
    reads: list[AccountRead]=Field(default_factory=list,max_length=8)
    authoritative: Literal[False]=False

    @model_validator(mode='after')
    def complete_read_interval(self):
        if not 0<=self.completed_at_ms-self.observed_at_ms<=120000: raise ValueError('invalid capture duration')
        names=tuple(r.kind for r in self.reads)
        if names!=READ_KINDS[:len(names)]: raise ValueError('invalid observation sequence')
        if self.status=='CAPTURED' and (names!=READ_KINDS or self.reason!='CAPTURE_COMPLETE'):
            raise ValueError('incomplete capture')
        if self.status=='NOT_REQUESTED' and (self.reads or self.reason!='ACCOUNT_NOT_REQUESTED'):
            raise ValueError('unrequested observations')
        if self.status=='FAILED' and self.reason in ('ACCOUNT_NOT_REQUESTED','CAPTURE_COMPLETE'):
            raise ValueError('invalid failure reason')
        previous=self.observed_at_ms
        for r in self.reads:
            if not previous<=r.started_at_ms<=r.received_at_ms<=self.completed_at_ms:
                raise ValueError('unbound request interval')
            previous=r.received_at_ms
        return self
