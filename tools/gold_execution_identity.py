"""Derive reviewable execution identity from a revalidated retained AI trial."""
from pathlib import Path
from decimal import Decimal
import hashlib
from dataclasses import dataclass

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest
from services.research.gold_execution_identity import identify,SCHEMA
from tools.gold_cost_review import review_request,_read
from tools.gold_ai_origin import verify_origin
from tools.gold_cost_inputs import dump_inputs,load_inputs
from tools.gold_ai_origin import encode


@dataclass(frozen=True, init=False)
class PreparedStrategy:
    """Process-local verification of fixed archive bytes, never an approval.

    Construction performs the original full verification. Consumers still read
    and compare all six archive files; nothing is restored from a caller's
    serialized assertion. A copied archive is usable only if byte-identical.
    """
    _files: tuple
    _definition: bytes
    _verifier_source: bytes

    def __init__(self, directory):
        root = Path(directory).absolute()
        names = ('input.json', 'report.json', 'manifest.json', 'research-origin.json',
                 'trials.db', 'strategy-definition.json')
        before = tuple((name, _read(root, name)) for name in names)
        source = Path(__file__).read_bytes()
        definition = retain_strategy_definition(root)
        if (tuple((name, _read(root, name)) for name in names) != before
                or Path(__file__).read_bytes() != source):
            raise ValueError('GOLD_PREPARED_STRATEGY_CHANGED')
        object.__setattr__(self, '_files', before)
        object.__setattr__(self, '_definition', encode(definition))
        object.__setattr__(self, '_verifier_source', source)

    def definition(self, directory):
        root = Path(directory).absolute()
        if (Path(__file__).read_bytes() != self._verifier_source
                or any(_read(root, name) != raw for name, raw in self._files)
                or any((root/('trials.db'+suffix)).exists() or (root/('trials.db'+suffix)).is_symlink()
                       for suffix in ('-wal', '-shm', '-journal'))):
            raise ValueError('GOLD_PREPARED_STRATEGY_CHANGED')
        return load_json_without_duplicate_keys(self._definition)


def from_archive(directory):
    root=Path(directory).absolute()
    request=review_request(root)
    if request['terminal']!='PASSED' or 'research-origin.json' not in request['files']:
        raise ValueError('GOLD_EXECUTION_AI_TRIAL_REQUIRED')
    raw={name:_read(root,name) for name in ('manifest.json','input.json','research-origin.json')}
    if any(hashlib.sha256(body).hexdigest()!=request['files'][name] for name,body in raw.items()):
        raise ValueError('GOLD_EXECUTION_ARCHIVE_CHANGED')
    manifest=load_json_without_duplicate_keys(raw['manifest.json'])
    origin=load_json_without_duplicate_keys(raw['research-origin.json'])
    candidate=verify_origin(origin,raw['input.json'])
    return identify({'schema':SCHEMA,'attempt':manifest['attempt'],'candidate_id':candidate,
        **{k:manifest[k] for k in ('trial_id','definition_id','input_sha256','report_sha256','research_origin_sha256')},
        'base_input_sha256':hashlib.sha256(dump_inputs(load_inputs(encode(origin['base_input'])))).hexdigest(),
        'manifest_sha256':request['files']['manifest.json'],'review_request_sha256':digest(request),
        'available_at_ms':max(e['at'] for e in request['trial_history']),
        'scope':'TECHNICAL_RESEARCH_IDENTITY_ONLY','economic_eligible':False,'submission_enabled':False})


def retain_strategy_definition(directory):
    """Export the tested parameters for review; historical zones are not new signals.

    This is a reproducible derivative of the original archive, not registration,
    permission, a fresh-data decision or an instruction to the Demo controller.
    """
    root = Path(directory).absolute()
    identity = from_archive(root)
    raw = _read(root, 'input.json')
    if hashlib.sha256(raw).hexdigest() != identity['input_sha256']:
        raise ValueError('GOLD_STRATEGY_INPUT_CHANGED')
    arguments = load_inputs(raw)
    document = arguments['document']
    plan = arguments['plan']
    body = {
        'schema': 'GOLD_RESEARCH_STRATEGY_DEFINITION_V1',
        'research_identity': identity,
        'symbol': 'XAUUSDT',
        'entry_semantics': 'GOLD_ABSORPTION_LONG_V1',
        'entry_rule': document['rule'],
        'historical_zone': document['zone'],
        'exit_and_size_plan': {k: str(v) if type(v) is Decimal else v
                               for k, v in plan.items()},
        'scope': 'ARCHIVED_STRATEGY_PARAMETERS_FOR_REVIEW_ONLY',
        'prospective_registration_required': True,
        'authoritative': False,
        'economic_eligible': False,
        'submission_enabled': False,
    }
    definition = {**body, 'result_hash': digest(body)}
    encoded = encode(definition)
    if from_archive(root) != identity:
        raise ValueError('GOLD_STRATEGY_ARCHIVE_CHANGED')
    target = root/'strategy-definition.json'
    if target.exists() or target.is_symlink():
        if _read(root, target.name) != encoded:
            raise ValueError('GOLD_STRATEGY_DEFINITION_CHANGED')
    else:
        with target.open('xb') as stream:
            stream.write(encoded)
    return definition
