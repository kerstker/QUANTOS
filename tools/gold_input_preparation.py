"""Publish saved public gold captures into the original factory input catalogue.

Only a numbered local preparation bundle is read. Quotes and signals are not
collected here; the template supplies explicit simulation costs and sizing.
"""
import hashlib
import os
import tempfile

from quantos.runtime_paths import runtime_state_root
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.gold_ai_origin import encode
from tools.gold_cost_inputs import load_inputs, dump_inputs
from tools.gold_cost_review import _read
from tools.gold_public_cost_inputs import assemble_inputs, verify_assembly
from tools.gold_signal_replay import replay
from tools.perpetual_replay_research import source_identity


def _publish(path, raw):
    if path.exists() or path.is_symlink():
        if _read(path.parent, path.name) != raw:
            raise ValueError('GOLD_PREPARATION_ALREADY_BOUND')
        return
    fd, temporary = tempfile.mkstemp(prefix='.publishing-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # No overwrite; readers never see a partial JSON.
    finally:
        os.unlink(temporary)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def run_prepare(params):
    if (type(params) is not dict or set(params) != {'attempt'}
            or type(params['attempt']) is not int or not 1 <= params['attempt'] <= 1000):
        raise ValueError('INVALID_GOLD_PREPARATION_ID')
    attempt = params['attempt']
    base = runtime_state_root()/'data/cache/ai_factory'
    request = base/'gold-preparation'/str(attempt)
    destination = base/'gold-inputs'
    if request.resolve() != request.absolute() or destination.resolve() != destination.absolute():
        raise ValueError('UNSAFE_GOLD_PREPARATION_PATH')
    with acquire_runtime_lock(base/'gold-preparation.lock') as acquired:
        if not acquired:
            raise RuntimeError('GOLD_PREPARATION_BUSY')
        raw = {name: _read(request, name) for name in ('quotes.json', 'signals.json', 'template.json')}
        if (request/'design.json').exists() or (request/'design.json').is_symlink():
            from tools.gold_capture_design import verify_design
            raw['design.json']=_read(request,'design.json')
            receipt=load_json_without_duplicate_keys(raw['design.json'])
            design=verify_design(receipt,seconds=receipt['seconds'])
            signal=load_json_without_duplicate_keys(raw['signals.json'])
            if (any(signal['rule'][k]!=design[k] for k in ('window_ms','max_age_ms'))
                    or len(signal['decision_times_ms'])!=design['window_count']):
                raise ValueError('GOLD_CAPTURE_DESIGN_INPUT_MISMATCH')
        template = load_inputs(raw['template.json'])
        document = load_json_without_duplicate_keys(raw['signals.json'])
        plan = template['plan']
        if 'design.json' in raw and receipt.get('design_scope')=='ANCHORED_PRICE_REGION_V1':
            from services.execution.perpetual_public_replay import prepare_public_replay
            from services.research.gold_price_design import compile_prices
            frames=prepare_public_replay(load_json_without_duplicate_keys(raw['quotes.json']))['frames']
            compiled,plan=compile_prices(design,document=document,plan=plan,anchor=frames[0],
                                         designed_at_ms=receipt['received_at_ms'])
            if compiled['zone']!=document['zone']:
                raise ValueError('GOLD_PRICE_DESIGN_INPUT_MISMATCH')
        signal_report = replay(document)
        if signal_report['status'] != 'RESEARCH_SIGNALS_ONLY':
            index = ((signal_report['failed_window_start_ms']-document['start_ms'])
                     //document['rule']['window_ms'])
            diagnostic = {
                'schema': 'GOLD_INPUT_SIGNAL_DIAGNOSTIC_V1',
                'status': signal_report['status'], 'reason': signal_report['reason'],
                'failed_window_start_ms': signal_report['failed_window_start_ms'],
                'scheduled_decision_ms': document['decision_times_ms'][index],
                'source_sha256': {name: hashlib.sha256(body).hexdigest() for name, body in raw.items()},
                'authoritative': False,
            }
            if any(_read(request, name) != body for name, body in raw.items()):
                raise ValueError('GOLD_PREPARATION_SOURCE_CHANGED')
            _publish(request/'signal-validation.json', encode(diagnostic))
            raise ValueError('GOLD_SIGNAL_INPUT_INCOMPLETE')
        release = source_identity()
        arguments = assemble_inputs(capture=load_json_without_duplicate_keys(raw['quotes.json']),
            document=document, plan=plan, release_identity=release,
            **{k: template[k] for k in ('initial_collateral', 'costs')},
            limits=template.get('limits'))
        verify_assembly(arguments)
        output = dump_inputs(arguments)
        if source_identity() != release or any(_read(request, name) != body for name, body in raw.items()):
            raise ValueError('GOLD_PREPARATION_SOURCE_CHANGED')
        summary = {'schema': 'GOLD_FACTORY_INPUT_PUBLICATION_V1', 'status': 'PUBLISHED',
            'input_id': attempt, 'input_sha256': hashlib.sha256(output).hexdigest(),
            'source_sha256': {name: hashlib.sha256(body).hexdigest() for name, body in raw.items()},
            'release_identity': release, 'authoritative': False, 'source_authenticated': False,
            'evidence_status': 'TECHNICAL_VALIDATION_ONLY'}
        destination.mkdir(parents=True, exist_ok=True)
        _publish(destination/f'{attempt}.metadata.json', encode(summary))
        _publish(destination/f'{attempt}.json', output)
        return summary
