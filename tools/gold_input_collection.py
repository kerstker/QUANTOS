"""Keyless paired public capture -> fixed-template retrospective factory input."""
import asyncio
from copy import deepcopy
import hashlib
import threading

from quantos.runtime_paths import runtime_state_root
from services.data.gold_capture_snapshot import summarize_capture
from services.execution.perpetual_public_replay import prepare_public_replay
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.gold_ai_origin import encode
from tools.gold_cost_inputs import load_inputs
from tools.gold_cost_review import _read
from tools.gold_input_preparation import _publish, run_prepare
from tools.gold_public_capture import collect
from tools.perpetual_public_capture import capture_public
from tools.gold_capture_design import propose_design, verify_design


_LOCK = threading.Lock()


async def collect_pair(seconds, output):
    async def quotes():
        value = await asyncio.to_thread(capture_public, samples=max(3, seconds//5),
                                       interval_seconds=1, symbol='XAUUSDT')
        _publish(output/'quotes.json', encode(value))
        return value
    async def trades():
        value = await collect(seconds)
        _publish(output/'trades.json', encode(value))
        return value
    values = await asyncio.gather(quotes(), trades(), return_exceptions=True)
    if any(isinstance(v, BaseException) for v in values):
        _publish(output/'capture-error.json', encode({'status':'CAPTURE_FAILED',
            'errors':[type(v).__name__ if isinstance(v, BaseException) else None for v in values]}))
        raise ValueError('GOLD_PAIRED_CAPTURE_FAILED')
    return dict(zip(('quotes', 'trades'), values))


def run_collect(params,*,design_memory=None,research_memory=None):
    if (type(params) is not dict or set(params)-{'decision_policy'} != {'attempt','template_id','seconds'}
            or any(type(params[k]) is not int or not 1 <= params[k] <= 1000 for k in ('attempt','template_id'))
            or type(params['seconds']) is not int or not 15 <= params['seconds'] <= 60
            or params['attempt'] == params['template_id']):
        raise ValueError('INVALID_GOLD_COLLECTION_REQUEST')
    policy=params.get('decision_policy','FIXED_TEMPLATE_RETROSPECTIVE_WINDOW_V1')
    if policy not in ('FIXED_TEMPLATE_RETROSPECTIVE_WINDOW_V1','FIRST_RECEIVED_WATERMARK_V1','AI_DESIGNED_WATERMARK_V1','AI_DESIGNED_ZONE_V1'):
        raise ValueError('INVALID_GOLD_DECISION_POLICY')
    if design_memory is not None:
        from services.research.gold_capture_memory import validate_capture_memory
        design_memory=validate_capture_memory(design_memory)
        if policy not in ('AI_DESIGNED_WATERMARK_V1','AI_DESIGNED_ZONE_V1'):raise ValueError('GOLD_DESIGN_MEMORY_POLICY_MISMATCH')
    base = runtime_state_root()/'data/cache/ai_factory'
    if research_memory is not None:
        from tools.gold_research_memory import verify_memory
        if policy!='AI_DESIGNED_ZONE_V1':raise ValueError('GOLD_STRATEGY_MEMORY_POLICY_MISMATCH')
        research_memory=verify_memory(base,research_memory)
    output = base/'gold-collections'/str(params['attempt'])
    preparation = base/'gold-preparation'/str(params['attempt'])
    if any(path.resolve() != path.absolute() for path in (base, output, preparation)):
        raise ValueError('UNSAFE_GOLD_COLLECTION_PATH')
    with acquire_runtime_lock(base/'gold-collection.lock', process_lock=_LOCK) as acquired:
        if not acquired:
            raise RuntimeError('GOLD_COLLECTION_BUSY')
        raw = _read(base/'gold-inputs', f'{params["template_id"]}.json')
        template = load_inputs(raw)
        request = {**params,'template_sha256':hashlib.sha256(raw).hexdigest(),
                   'policy':policy,'authoritative':False}
        if design_memory is not None:request['design_memory_hash']=design_memory['result_hash']
        if research_memory is not None:request['research_memory_hash']=research_memory['result_hash']
        if output.exists():
            if _read(output,'request.json') != encode(request) or _read(output,'template.json') != raw:
                raise ValueError('GOLD_COLLECTION_REQUEST_CHANGED')
            # No network restart: replay only the two complete retained captures.
            pair = {k:load_json_without_duplicate_keys(_read(output,k+'.json')) for k in ('quotes','trades')}
        else:
            if preparation.exists() or (base/'gold-inputs'/f'{params["attempt"]}.json').exists():
                raise ValueError('GOLD_COLLECTION_INPUT_ID_OCCUPIED')
            output.mkdir(parents=True)
            _publish(output/'request.json',encode(request))
            _publish(output/'template.json',raw)
            if policy in ('AI_DESIGNED_WATERMARK_V1','AI_DESIGNED_ZONE_V1'):
                if design_memory is not None:_publish(output/'design-memory.json',encode(design_memory))
                if research_memory is not None:_publish(output/'research-memory.json',encode(research_memory))
                _publish(output/'design-started.json',encode({'status':'MODEL_CALL_STARTED','authoritative':False}))
                design=propose_design(seconds=params['seconds'],base_rule={k:template['document']['rule'][k]
                    for k in ('window_ms','max_age_ms')},**({'capture_memory':design_memory} if design_memory is not None else {}),
                    **({'price_design':True} if policy=='AI_DESIGNED_ZONE_V1' else {}),
                    **({'research_memory':research_memory} if research_memory is not None else {}))
                _publish(output/'design.json',encode(design))
            pair = asyncio.run(collect_pair(params['seconds'], output))
            for key, value in pair.items():
                _publish(output/(key+'.json'),encode(value))
        trades = pair['trades']
        summarize_capture(trades, as_of_ms=trades['ended_at_ms'])
        if trades['status'] != 'CAPTURED_PUBLIC_PREFIX':
            raise ValueError('GOLD_COLLECTION_STREAM_INCOMPLETE')
        frames = prepare_public_replay(pair['quotes'])['frames']
        document = deepcopy(template['document'])
        # Fix the start from the first available quote and first received trade,
        # never search later windows for a favourable signal or outcome.
        start = max(frames[0].timestamp, trades['packets'][0]['received_at_ms']+1)
        shift = start-document['start_ms']
        document['start_ms'] = start
        document['decision_times_ms'] = [t+shift for t in document['decision_times_ms']]
        for field in ('registered_at_ms','expires_at_ms'):
            document['zone'][field] += shift
        document['packets'] = trades['packets']
        design_raw=None
        if policy in ('AI_DESIGNED_WATERMARK_V1','AI_DESIGNED_ZONE_V1'):
            design_raw=_read(output,'design.json')
            receipt=load_json_without_duplicate_keys(design_raw)
            design=verify_design(receipt,seconds=params['seconds'])
            if (receipt.get('design_scope')=='ANCHORED_PRICE_REGION_V1')!=(policy=='AI_DESIGNED_ZONE_V1'):
                raise ValueError('GOLD_CAPTURE_DESIGN_POLICY_MISMATCH')
            if receipt.get('capture_memory')!=design_memory:raise ValueError('GOLD_CAPTURE_DESIGN_MEMORY_MISMATCH')
            if receipt.get('research_memory')!=research_memory:raise ValueError('GOLD_STRATEGY_DESIGN_MEMORY_MISMATCH')
            if receipt['received_at_ms']>trades['started_at_ms']:
                raise ValueError('GOLD_CAPTURE_DESIGN_AFTER_CAPTURE')
            if receipt['base_rule']!={k:template['document']['rule'][k] for k in ('window_ms','max_age_ms')}:
                raise ValueError('GOLD_CAPTURE_DESIGN_TEMPLATE_MISMATCH')
            document['rule'].update({k:design[k] for k in ('window_ms','max_age_ms')})
            document['decision_times_ms']=[start+(i+1)*design['window_ms']+design['max_age_ms']
                                           for i in range(design['window_count'])]
        if policy in ('FIRST_RECEIVED_WATERMARK_V1','AI_DESIGNED_WATERMARK_V1','AI_DESIGNED_ZONE_V1'):
            from services.data.gold_execution_tape import new_gold_trade_cursor
            from services.research.gold_decision_schedule import watermark_decisions
            document['decision_times_ms']=watermark_decisions(document,cursor_factory=new_gold_trade_cursor)
        if policy=='AI_DESIGNED_ZONE_V1':
            from services.research.gold_price_design import compile_prices
            document,_=compile_prices(design,document=document,plan=template['plan'],anchor=frames[0],
                                      designed_at_ms=receipt['received_at_ms'])
        # The explicit region mode compiles prices from the first known quote;
        # other modes retain template prices. This remains retrospective replay.
        preparation.mkdir(parents=True, exist_ok=True)
        if design_raw is not None:_publish(preparation/'design.json',design_raw)
        for name, body in (('quotes.json',encode(pair['quotes'])),('signals.json',encode(document)),('template.json',raw)):
            _publish(preparation/name,body)
        result = run_prepare({'attempt':params['attempt']})
        _publish(output/'result.json',encode(result))
        return result
