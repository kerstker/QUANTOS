"""Describe a bounded immutable forecast window without inventing account PnL."""
import math
import re
from services.research.ai_factory_lifecycle import model_diagnostics


def forward_summary(forecasts, labels, *, as_of_ms):
    if type(as_of_ms) is not int or as_of_ms<0 or max(len(forecasts),len(labels))>4096:
        raise ValueError('bounded forward snapshot required')
    issued={};mature={};hashes=set()
    for row in forecasts:
        slot=row['slot_ms'];at=row['issued_at_ms'];end=row['exit_ms'];prediction=row['prediction']
        if any(type(v) is not int for v in (slot,at,end)) or not 0<=slot<=at<end or at>as_of_ms or slot in issued:
            raise ValueError('prediction not recorded before outcome or duplicate slot')
        entry=row.get('entry_ms',end)
        available=row.get('available_at_ms',slot)
        if type(entry) is not int or type(available) is not int or not slot<=available<=at<=entry<=end:
            raise ValueError('forecast issued after entry or before information availability')
        if type(prediction) not in (int,float) or not math.isfinite(prediction):raise ValueError('invalid prediction')
        if not isinstance(row['model_hash'],str) or not re.fullmatch('[0-9a-f]{64}',row['model_hash']):raise ValueError('model identity missing')
        hashes.add(row['model_hash']);issued[slot]=row
    if len(hashes)>1:raise ValueError('compare models in separate frozen windows')
    for row in labels:
        slot=row['slot_ms'];available=row['available_at_ms']
        if type(slot) is not int or type(available) is not int or slot in mature or not 0<=slot<available<=as_of_ms:
            raise ValueError('invalid maturity time or duplicate label')
        if any(type(row[k]) not in (int,float) or not math.isfinite(row[k]) for k in ('prediction','realized_return')):
            raise ValueError('nonfinite label')
        if slot in issued and (available<issued[slot]['exit_ms'] or row['prediction']!=issued[slot]['prediction']):
            raise ValueError('label conflicts with frozen prediction')
        if slot in issued and (row.get('model_hash',issued[slot]['model_hash'])!=issued[slot]['model_hash']
                               or row.get('forecast_hash') is not None and row['forecast_hash']!=issued[slot].get('result_hash')):
            raise ValueError('label/model/forecast identity mismatch')
        mature[slot]=row
    slots=sorted(issued);common=sorted(set(issued)&set(mature));gaps=[]
    if slots:
        if slots[-1]-slots[0]>4096*3600000 or any((s-slots[0])%3600000 for s in slots):raise ValueError('invalid hourly window')
        gaps=sorted(set(range(slots[0],slots[-1]+1,3600000))-set(slots))
    return {'status':'DESCRIPTIVE_FORWARD_ONLY','common_mature_labels':len(common),
            'common_start_ms':common[0] if common else None,'common_end_ms':common[-1] if common else None,
            'missing_forecast_slots':gaps,'unmatched_label_slots':sorted(set(mature)-set(issued)),
            'pending_slots':[s for s in slots if issued[s]['exit_ms']>as_of_ms],
            'missing_mature_label_slots':[s for s in slots if issued[s]['exit_ms']<=as_of_ms and s not in mature],
            'diagnostics':model_diagnostics([mature[s] for s in common]),
            'cost_adjusted_pnl':None,'cost_evidence':'CANONICAL_FORWARD_LEDGER_NOT_AVAILABLE',
            'effective_independent_samples':None,'economic_eligible':False,
            'scope':'SUPPLIED_RECENT_WINDOW_NOT_COMPLETE_LIFETIME','model_hash':next(iter(hashes),None)}
