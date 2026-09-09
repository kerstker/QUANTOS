"""Four predeclared position policies; no optimizer, broker, or outer feedback."""
from services.research.ai_factory_decision import decide

POLICY_IDS = ('BASELINE_COST_UTILITY', 'BAND_20_20', 'HYSTERESIS_20_5', 'HYSTERESIS_30_10')
_BANDS = {'BAND_20_20': (20,20), 'HYSTERESIS_20_5': (20,5), 'HYSTERESIS_30_10': (30,10)}


def decide_registered(policy, **inputs):
    if policy not in POLICY_IDS:
        raise ValueError('unregistered position policy')
    base = decide(**inputs)
    if policy == POLICY_IDS[0]:
        return base
    current = inputs['current']; prediction = inputs['prediction']
    entry, exit_ = _BANDS[policy]
    reason = 'ENTRY_BAND'; target = 0.
    if abs(current) > .30:
        reason = 'RISK_REDUCTION'
    elif current and prediction * current > 0 and abs(prediction)*10000 > exit_:
        # Continuation must still cover the fixed risk and settled-funding terms.
        hold = next((row for row in base['alternatives'] if row['target'] is None), None)
        if hold is not None and hold['utility'] > 0:
            target = None; reason = 'HYSTERESIS_HOLD'
    elif abs(prediction)*10000 > entry:
        target = base['target']; reason = base['reason']
    choice = next(row for row in base['alternatives'] if row['target'] == target)
    return {**base, **choice, 'policy': policy, 'reason': reason,
            'entry_bps': entry, 'exit_bps': exit_}
