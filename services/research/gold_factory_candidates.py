"""Finite gold absorption hypotheses; neither trading plans nor qualification."""
from copy import deepcopy

CATALOG = 'GOLD_ABSORPTION_V1'


def candidates():
    return [{'id': index, 'instrument_id': 'XAUUSDT',
             'family': CATALOG, 'sell_threshold': threshold,
             'confirm_windows': windows,
             'hypothesis': 'Repeated sell-flow absorption inside a previously registered zone',
             'falsification': 'No confirmed signal or no cost-adjusted edge on independently held-out data',
             'scope': 'RETROSPECTIVE_TECHNICAL_EXPERIMENT_ONLY'}
            for index, (threshold, windows) in enumerate(
                (threshold, windows) for threshold in ('10', '25', '50') for windows in (2, 3))]


def apply_candidate(document, candidate_id):
    space = candidates()
    if type(candidate_id) is not int or not 0 <= candidate_id < len(space):
        raise ValueError('INVALID_GOLD_CANDIDATE')
    if (not isinstance(document, dict) or document.get('schema') != 'GOLD_SIGNAL_REPLAY_INPUT_V1'
            or not isinstance(document.get('rule'), dict)):
        raise ValueError('INVALID_GOLD_CANDIDATE_INPUT')
    result = deepcopy(document)
    candidate = space[candidate_id]
    for field in ('sell_threshold', 'confirm_windows'):
        result['rule'][field] = candidate[field]
    return result
