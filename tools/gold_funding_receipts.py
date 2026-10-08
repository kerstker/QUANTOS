"""Read original public funding polls with first-observed receipt clocks."""
import base64
import hashlib
from pathlib import Path

from services.execution.public_funding_observations import load_funding_observations
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_cost_review import _read


def receipts_for_window(directories, *, start_ms, end_ms):
    if (type(directories) not in (list,tuple) or not 1 <= len(directories) <= 32
            or type(start_ms) is not int or type(end_ms) is not int
            or not 0 <= start_ms < end_ms <= 2**63-1):
        raise ValueError('FUNDING_RECEIPT_WINDOW')
    seen={}
    scanned=0
    for directory in directories:
        root=Path(directory).absolute()
        plan=decode(_read(root,'plan.json'))
        if (plan.get('schema')!='GOLD_FUNDING_OBSERVATION_PLAN_V1'
                or plan.get('symbol')!='XAUUSDT' or plan.get('submission_enabled') is not False):
            raise ValueError('FUNDING_RECEIPT_PLAN')
        for poll in sorted(root.glob('poll-*')):
            scanned+=1
            if scanned>4096:
                raise ValueError('FUNDING_RECEIPT_POLL_BUDGET')
            if not (poll/'receipt.json').exists():
                continue
            receipt_raw=_read(poll,'receipt.json')
            if not receipt_raw.endswith(b'\n'):
                continue
            receipt=decode(receipt_raw)
            received=receipt['received_at_ms']
            requested=receipt['requested_at_ms']
            if (type(received) is not int or type(requested) is not int
                    or not 0 <= requested <= received or received-requested>30000):
                raise ValueError('FUNDING_RECEIPT_CLOCK')
            if received>end_ms:
                continue
            raw=_read(poll,'response.json')
            if (hashlib.sha256(raw).hexdigest()!=receipt['response_sha256']
                    or not receipt['endpoint'].startswith('/fapi/v1/fundingRate?symbol=XAUUSDT&')):
                raise ValueError('FUNDING_RECEIPT_SOURCE')
            observations=load_funding_observations(raw,start_ms=0,end_ms=requested,
                requested_at_ms=requested,received_at_ms=received)
            record=dict(kind='funding',requested_at=requested,received_at=received,
                endpoint=receipt['endpoint'],payload=decode(raw),response_sha256=receipt['response_sha256'],
                response_base64=base64.b64encode(raw).decode(),
                original_receipt_sha256=hashlib.sha256(receipt_raw).hexdigest())
            for observation in observations:
                event=observation.event
                if not start_ms <= event.timestamp <= end_ms:
                    continue
                previous=seen.get(event.timestamp)
                if previous is not None and previous[0]!=event:
                    raise ValueError('CONFLICTING_FUNDING_SETTLEMENT')
                if previous is None or received<previous[1]['received_at']:
                    seen[event.timestamp]=(event,record)
            if _read(poll,'receipt.json')!=receipt_raw or _read(poll,'response.json')!=raw:
                raise ValueError('FUNDING_RECEIPT_CHANGED')
    records={}
    for _,record in seen.values():
        key=(record['received_at'],record['response_sha256'],record['original_receipt_sha256'])
        records[key]=record
    return [records[key] for key in sorted(records)] if records else None
