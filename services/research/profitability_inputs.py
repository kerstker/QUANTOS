"""Validate bounded research data imports before their immutable registration."""
import numpy as np
from services.research.ai_factory_store import encode
from services.research.profitability_information import basis_panel,information_panel
from services.research.profitability_trajectory import compare_trajectories,_orders
from services.research.profitability_calibration import decode_actions
from services.execution.perpetual_public_replay import prepare_public_replay

KINDS=('basis','information','trajectory','calibration')


def validate_input(kind,data):
    if kind not in KINDS or not isinstance(data,dict) or len(encode(data).encode())>4000000:
        raise ValueError('supported research JSON under 4 MB required')
    if kind in ('basis','information'):
        if set(data)!={'quote','observations'} or not isinstance(data['observations'],list) or not 1<=len(data['observations'])<=32768:
            raise ValueError('nonempty bounded information panel required')
        times=[r['slot_ms'] for r in data['observations']]
        panel=basis_panel if kind=='basis' else information_panel
        panel(times,np.zeros((len(times),5)),data['observations'],quote=data['quote'])
    elif kind=='trajectory':
        if set(data)!={'observed','simulated'}:raise ValueError('observed/simulated pair required')
        compare_trajectories(data['observed'],data['simulated'])
    else:
        if set(data)!={'observed','capture','actions','calibration_cutoff_ms'}:raise ValueError('fixed-action calibration input required')
        prepared=prepare_public_replay(data['capture']);_orders(data['observed']);decode_actions(data['actions'])
        cutoff=data['calibration_cutoff_ms']
        if type(cutoff) is not int or not prepared['frames'][0].timestamp<cutoff<prepared['frames'][-1].timestamp:
            raise ValueError('calibration cutoff must precede the final observation')
    return data
