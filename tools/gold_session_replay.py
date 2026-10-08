"""Replay one frozen shared prefix through the original gold portfolio ledger.

Each result reconstructs the entire supplied account tape, not summed window
returns. Extending a prefix is not yet an online session or a rule-epoch change.
"""
from pathlib import Path
import hashlib

from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_public_replay import prepare_public_replay, GOLD_UNIT_BASIS
from services.execution.perpetual_simulation import SimulationFrame
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.research.gold_net_portfolio import compare_portfolio
from tools.gold_bar_session import _cursor
from tools.gold_session_io import read as _read


def replay(directory, *, exchange_after, release_identity, quantities, initial_collateral,
           fee_rate, max_notional, observed_funding_records=None, settlement_window_ms=None,
           allow_flat_observation_gaps=False):
    if type(allow_flat_observation_gaps) is not bool:
        raise ValueError('SESSION_REPLAY_GAP_POLICY')
    if settlement_window_ms is not None and (type(settlement_window_ms) is not int
            or not 1 <= settlement_window_ms <= 1000):
        raise ValueError('SESSION_REPLAY_SETTLEMENT_WINDOW')
    root=Path(directory).absolute()
    summary_raw=_read(root,'summary.json')
    summary=decode(summary_raw)
    if (summary.get('schema')!='GOLD_SHARED_RAW_PREFIX_SUMMARY_V1'
            or summary.get('submission_enabled') is not False
            or summary.get('economic_eligible') is not False
            or summary.get('replay_ready') is not False
            or set(summary.get('files',{})) != {'warmup.json','warmup-receipt.json','inputs.json','sources.json'}):
        raise ValueError('SESSION_REPLAY_PREFIX_SCHEMA')
    raw={name:_read(root,name) for name in summary['files']}
    if any(hashlib.sha256(body).hexdigest()!=summary['files'][name] for name,body in raw.items()):
        raise ValueError('SESSION_REPLAY_PREFIX_HASH')
    inputs=decode(raw['inputs.json'])
    if (inputs.get('schema')!='GOLD_SHARED_RAW_PREFIX_V1'
            or inputs.get('as_of_ms')!=summary['as_of_ms']
            or inputs.get('submission_enabled') is not False):
        raise ValueError('SESSION_REPLAY_INPUT_SCHEMA')
    samples=inputs['samples']
    if not samples or samples[-1]['depth']['received_at']>inputs['as_of_ms']:
        raise ValueError('SESSION_REPLAY_QUOTE_WATERMARK')
    capture=dict(schema_version='GOLD_PUBLIC_SESSION_PREFIX_V1',symbol='XAUUSDT',
        bootstrap=inputs['bootstrap'],samples=samples,exchange_after=exchange_after,
        quantity_basis=dict(GOLD_UNIT_BASIS),max_gap_ms=30000,declared_interval_ms=10000,
        prefix_summary_sha256=hashlib.sha256(summary_raw).hexdigest())
    if 'capacity_observations' in inputs:
        capture['capacity_observations'] = inputs['capacity_observations']
    if allow_flat_observation_gaps:
        capture['observation_gap_policy'] = 'FLAT_ACCOUNT_PAUSE_V1'
    capture['capture_id']=exact_identity_hash(capture,name='public_replay_capture')
    prepared=prepare_public_replay(capture,observed_funding_records=observed_funding_records,
                                   settlement_window_ms=(settlement_window_ms
                                       if observed_funding_records is not None else None))
    frames=prepared['frames']
    receipt=decode(raw['warmup-receipt.json'])
    if receipt['received_at_ms']>frames[0].timestamp:
        raise ValueError('SESSION_REPLAY_WARMUP_UNAVAILABLE')
    cursor=_cursor(raw['warmup.json'],receipt)
    decision_times=[]
    for row in inputs['bar_messages']:
        if row['received_at_ms']>inputs['as_of_ms']:
            raise ValueError('SESSION_REPLAY_BAR_WATERMARK')
        decision=cursor.ingest(row['raw'],received_at_ms=row['received_at_ms'])
        if decision!=row['decision']:
            raise ValueError('SESSION_REPLAY_DECISION_CHANGED')
        if decision['status']=='CLOSED_BAR' and frames[0].timestamp<=row['received_at_ms']<=frames[-1].timestamp:
            decision_times.append(row['received_at_ms'])
    if not decision_times:
        raise ValueError('SESSION_REPLAY_NO_CLOSED_BAR_IN_QUOTE_COVERAGE')
    by_time={f.timestamp:f for f in frames}
    for t in decision_times:
        by_time.setdefault(t,SimulationFrame(t))
    prepared['frames']=tuple(by_time[t] for t in sorted(by_time))
    result=compare_portfolio(bars=cursor.bars,prepared=prepared,
        release_identity=release_identity,quantities=quantities,
        initial_collateral=initial_collateral,fee_rate=fee_rate,max_notional=max_notional,
        entry_not_before_ms=min(decision_times),incremental_bars=True,
        observation_binding=dict(schema='GOLD_SESSION_PREFIX_REPLAY_BINDING_V1',
            summary_sha256=hashlib.sha256(summary_raw).hexdigest(),files=summary['files'],
            as_of_ms=inputs['as_of_ms'],processed_through_ms=frames[-1].timestamp,
            declared_settlement_window_ms=settlement_window_ms,
            quote_source=inputs['quote_source'],bar_source=inputs['bar_source'],
            rule_recheck_sha256=exact_identity_hash(exchange_after,name='public_rule_receipt')))
    if (_read(root,'summary.json')!=summary_raw
            or any(_read(root,name)!=body for name,body in raw.items())):
        raise ValueError('SESSION_REPLAY_PREFIX_CHANGED')
    return result
