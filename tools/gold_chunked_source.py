"""Freeze original input files outside JSON, then bridge bounded native inputs.

This is an offline original-file carrier. It grants no replay, source, result or
economic authority and does not change the existing execution/registry budgets.
"""
import base64
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import hashlib
from pathlib import Path
import tempfile

from quantos.contracts import gold_chunked_source as shape, gold_segmented_input as segments
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from quantos.contracts.registered_research_run import canonical, MAX_INPUT_BYTES
from services.research.research_evidence_reader import _ReadSet
from tools.gold_bar_experiment import _bytes, _encode, JOURNAL_FILES
from tools.perpetual_replay_research import source_identity
from tools import registered_research_run as files

require=shape.require


def _part(value, *, first=False):
    require(type(value) is dict, 'PART_TYPE')
    if first:
        allowed={'capture','journal_files',*shape.CONFIGURATION_FIELDS,'portfolio_plan',
                 'observed_funding_records','settlement_window_ms'}
        require({'capture','journal_files',*shape.CONFIGURATION_FIELDS}<=set(value)<=allowed,
                'FIRST_FIELDS')
    else:
        require({'schema','capture','journal_files'}<=set(value)<=segments.SEGMENT_FIELDS
            and value['schema']==segments.SEGMENT_SCHEMA, 'CONTINUATION_FIELDS')
    require(type(value['capture']) is dict and type(value['journal_files']) is dict
        and set(value['journal_files'])==set(JOURNAL_FILES), 'PART_FILES')
    if 'observed_funding_records' in value:
        require(type(value['observed_funding_records']) is list
            and 1<=len(value['observed_funding_records'])<=1000, 'FUNDING_RECORDS')
        require(type(value.get('settlement_window_ms')) is int
            and 1<=value['settlement_window_ms']<=1000, 'FUNDING_WINDOW')
    elif 'settlement_window_ms' in value:
        require(first and type(value['settlement_window_ms']) is int
            and 1<=value['settlement_window_ms']<=1000, 'FUNDING_WINDOW')


def freeze(parts, output, *, maximum_bytes, settlement_window_ms=None):
    """Consume an original part iterator, writing each unique file once."""
    require(type(maximum_bytes) is int
        and shape.RESERVED_BYTES<maximum_bytes<=shape.MAX_SOURCE_BYTES, 'TOTAL_BYTES')
    iterator=iter(parts)
    try:
        first=next(iterator)
    except StopIteration as exc:
        raise ValueError('GOLD_SOURCE_EMPTY') from exc
    _part(first,first=True)
    if settlement_window_ms is not None:
        require(type(settlement_window_ms) is int and 1 <= settlement_window_ms <= 1000, 'FUNDING_WINDOW')
        require(first.get('settlement_window_ms', settlement_window_ms) == settlement_window_ms,
                'FUNDING_POLICY_CHANGED')
        first = dict(first, settlement_window_ms=settlement_window_ms)
    configured={name:value for name,value in _encode(first).items()
        if name in shape.CONFIGURATION_FIELDS|{'portfolio_plan'}}
    configured=decode(_bytes(configured))
    shape.configuration(configured)
    require(source_identity()==configured['release_identity'], 'SOURCE_CHANGED')
    root=Path(output).absolute()
    require(root.resolve()==root, 'PATH')
    root.mkdir(mode=0o700,parents=True,exist_ok=False)
    files._directory(root)
    files._mkdir(root/'blobs')
    known,descriptors={},[]
    size=0
    reads=_ReadSet(maximum_directory_entries=shape.MAX_BLOBS)

    def retain(raw):
        nonlocal size
        require(type(raw) is bytes and 0<len(raw)<=MAX_INPUT_BYTES, 'BLOB_BYTES')
        digest=hashlib.sha256(raw).hexdigest()
        leaf=root/'blobs'/digest
        if digest not in known:
            require(len(known)<shape.MAX_BLOBS
                and size+len(raw)<=maximum_bytes-shape.RESERVED_BYTES, 'TOTAL_BYTES')
            files._put(leaf,None,raw=raw)
            require(reads.read(leaf,len(raw))==raw, 'BLOB_CHANGED')
            known[digest]=len(raw)
            size+=len(raw)
        else:
            require(files._read(leaf)==raw, 'EXISTING_BLOB_CHANGED')
        return dict(sha256=digest,bytes=len(raw))

    def append(value):
        require(len(descriptors)<shape.MAX_PARTS, 'PART_COUNT')
        _part(value,first=not descriptors)
        if settlement_window_ms is not None and 'observed_funding_records' in value:
            require(value['settlement_window_ms'] == settlement_window_ms, 'FUNDING_POLICY_CHANGED')
        from quantos.contracts.gold_prefix_chain import SCHEMA as delta_schema, original_json
        summary = decode(base64.b64decode(value['journal_files']['summary.json'], validate=True))
        if summary.get('schema') == delta_schema:
            original_json(value['capture'])
        else:
            canonical(value['capture'],maximum=MAX_INPUT_BYTES)
        item=dict(schema=shape.PART_SCHEMA,capture=retain(_bytes(value['capture'])),journal_files={})
        for name,encoded in value['journal_files'].items():
            require(type(encoded) is str and len(encoded)<=4*((MAX_INPUT_BYTES+2)//3), 'JOURNAL_BOUND')
            item['journal_files'][name]=retain(base64.b64decode(encoded,validate=True))
        if 'observed_funding_records' in value:
            canonical(value['observed_funding_records'],maximum=MAX_INPUT_BYTES)
            item['observed_funding_records']=retain(_bytes(value['observed_funding_records']))
        if 'settlement_window_ms' in value:
            item['settlement_window_ms']=value['settlement_window_ms']
        descriptors.append(retain(_bytes(item)))

    try:
        append(first)
        # Drop the first expanded body before asking for the next one.
        first=None
        for part in iterator:
            append(part)
        require(set(reads.directory(root/'blobs'))==set(known), 'BLOB_MEMBERS')
        reads.recheck()
        require(source_identity()==configured['release_identity'], 'SOURCE_CHANGED')
        manifest=dict(schema=shape.FUNDING_SCHEMA if settlement_window_ms is not None else shape.SCHEMA,
            configuration=configured,parts=descriptors,
            part_count=len(descriptors),blob_count=len(known),blob_bytes=size,maximum_bytes=maximum_bytes,
            status='FROZEN_ORIGINALS_NOT_EXECUTION_VALIDATED',replay_ready=False,
            economic_eligible=False,submission_enabled=False)
        manifest['source_id']=shape.identity(manifest)
        shape.manifest(manifest)
        files._put(root/'manifest.json',None,raw=_bytes(manifest))
        return manifest
    except BaseException as exc:
        files._put(root/'failure.json',dict(status='INCOMPLETE',error_type=type(exc).__name__,
            part_count=len(descriptors),blob_count=len(known),blob_bytes=size,
            submission_enabled=False,economic_eligible=False))
        raise


class _Source:
    def __init__(self, directory, *, collection_source_identity=None):
        self.root=files._directory(Path(directory).absolute())
        self.reads=_ReadSet(maximum_directory_entries=shape.MAX_BLOBS)
        require(set(self.reads.directory(self.root))=={'manifest.json','blobs'}, 'NOT_FROZEN')
        self.raw_manifest=self.reads.read(self.root/'manifest.json',shape.MAX_MANIFEST_BYTES)
        self.manifest=shape.manifest(decode(self.raw_manifest))
        self.execution_source = deepcopy(source_identity())
        self.collection_source = deepcopy(self.execution_source if collection_source_identity is None
                                          else collection_source_identity)
        if collection_source_identity is not None:
            from quantos.contracts.gold_native_source import source_identity as validate_code_identity
            validate_code_identity(self.collection_source)
        require(self.collection_source == self.manifest['configuration']['release_identity'], 'SOURCE_CHANGED')
        self.names=set(self.reads.directory(self.root/'blobs'))
        require(len(self.names)==self.manifest['blob_count'], 'BLOB_MEMBERS')
        self.used={}
        self.size=0
        self.count=0
        self.started=False

    def read(self,binding):
        shape.blob(binding)
        digest=binding['sha256']
        require(digest in self.names, 'BLOB_MISSING')
        path=self.root/'blobs'/digest
        raw=self.reads.read(path,binding['bytes'])
        require(len(raw)==binding['bytes'] and hashlib.sha256(raw).hexdigest()==digest, 'BLOB_CHANGED')
        require(self.reads.files[path][0][4]&0o777==0o600, 'BLOB_CUSTODY')
        if digest not in self.used:
            self.used[digest]=len(raw)
            self.size+=len(raw)
        require(self.used[digest]==len(raw), 'BLOB_CHANGED')
        require(self.size<=self.manifest['blob_bytes'], 'TOTAL_BYTES')
        return raw

    def parts(self):
        require(not self.started, 'READER_ALREADY_STARTED')
        self.started=True
        funding_policy = self.manifest['schema'] == shape.FUNDING_SCHEMA
        window = None
        for index,binding in enumerate(self.manifest['parts']):
            item=decode(self.read(binding))
            require(type(item) is dict and set(item)<= {'schema','capture','journal_files',
                'observed_funding_records','settlement_window_ms'}
                and {'schema','capture','journal_files'}<=set(item)
                and item['schema']==shape.PART_SCHEMA
                and type(item['journal_files']) is dict
                and set(item['journal_files'])==set(JOURNAL_FILES), 'PART_FILES')
            value=dict(capture=decode(self.read(item['capture'])),journal_files={name:
                base64.b64encode(self.read(reference)).decode()
                for name,reference in item['journal_files'].items()})
            if 'observed_funding_records' in item:
                value['observed_funding_records']=decode(self.read(item['observed_funding_records']))
            if funding_policy:
                if index == 0:
                    window = item.get('settlement_window_ms')
                    require(type(window) is int and 1 <= window <= 1000, 'FUNDING_WINDOW_REQUIRED')
                if 'observed_funding_records' in item:
                    require(item.get('settlement_window_ms') == window, 'FUNDING_POLICY_CHANGED')
                elif 'settlement_window_ms' in item:
                    require(index == 0 and item['settlement_window_ms'] == window, 'FUNDING_HEADER')
            if 'settlement_window_ms' in item:
                # V2's first header declares future policy. A part without an
                # actual funding receipt still uses the original non-funding mode.
                if not funding_policy or 'observed_funding_records' in item:
                    value['settlement_window_ms']=item['settlement_window_ms']
            if index==0:
                value.update(decode(_bytes(self.manifest['configuration'])))
                for name in ('quantity','initial_collateral','fee_rate'):
                    value[name]=Decimal(value[name])
            else:
                value['schema']=segments.SEGMENT_SCHEMA
            _part(value,first=index==0)
            self.count+=1
            yield value

    def verify(self):
        require(self.count==self.manifest['part_count'], 'NOT_ALL_PARTS_READ')
        require(set(self.used)==self.names and sum(self.used.values())==self.manifest['blob_bytes'],
                'BLOB_MEMBERS')
        require(decode(self.raw_manifest)==self.manifest, 'MANIFEST_CHANGED')
        self.reads.recheck()
        require(source_identity() == self.execution_source
            and self.collection_source == self.manifest['configuration']['release_identity'], 'SOURCE_CHANGED')


@contextmanager
def open_source(directory):
    """Read every bound original; normal exit verifies the full physical cut."""
    reader=_Source(directory)
    yield reader
    reader.verify()


def materialize(directory):
    """Bridge existing bounded inputs; larger sources await the native protocol."""
    from tools.gold_native_prefix import assemble
    with open_source(directory) as reader:
        require(reader.manifest['part_count']<=segments.MAX_SEGMENTS, 'NATIVE_EXECUTION_BOUND')
        parts=reader.parts()
        first=next(parts)
        rest=list(parts)
        result=assemble(first,rest) if rest else first
        canonical(_encode(result),maximum=MAX_INPUT_BYTES)
    return result


class NativeSource:
    """Hold complete immutable source bytes across the original native transaction."""
    def __init__(self, directory, document=None, *, replay_budget=None, collection_source_identity=None):
        from quantos.contracts import gold_native_source as native
        if document is not None:
            native.input_document(document)
            if document['input_schema'] in native.REANALYSIS_INPUT_SCHEMAS:
                pinned = native.collection_identity(document)
                require(collection_source_identity is None or collection_source_identity == pinned, 'SOURCE_CHANGED')
                collection_source_identity = pinned
            else:
                require(collection_source_identity is None, 'REANALYSIS_INPUT_REQUIRED')
        self.reader = _Source(directory, collection_source_identity=collection_source_identity)
        self.document, self.descriptors, self.files = native.inspect(self.reader.manifest,
            self.reader.names, self.reader.read, manifest_sha256=hashlib.sha256(self.reader.raw_manifest).hexdigest())
        if document is not None:
            native.input_document(document)
            require(replay_budget is None or replay_budget == document.get('replay_budget'), 'NATIVE_BUDGET_CHANGED')
            replay_budget = document.get('replay_budget')
        self.document = (native.reanalysis_reference(self.document, self.reader.execution_source, replay_budget)
            if collection_source_identity is not None else native.execution_reference(self.document, replay_budget))
        if document is not None:
            require(self.document == document, 'NATIVE_INPUT_CHANGED')
        self.closed = False
        self.started = False
        self.verify()

    def verify(self):
        from quantos.contracts.gold_native_source import collection_identity
        require(not self.closed, 'NATIVE_SOURCE_CLOSED')
        reader = self.reader
        require(set(reader.used) == reader.names and sum(reader.used.values()) == reader.manifest['blob_bytes'],
                'BLOB_MEMBERS')
        require(decode(reader.raw_manifest) == reader.manifest, 'MANIFEST_CHANGED')
        reader.reads.recheck()
        require(source_identity() == reader.execution_source
            and collection_identity(self.document) == reader.manifest['configuration']['release_identity'],
            'SOURCE_CHANGED')

    def execute(self, arguments, *, preparation, history):
        from tools.gold_segmented_replay import _compare_parts
        from tools.gold_bar_experiment import _encode
        require(not self.started and not self.closed and _encode(arguments) == self.document,
                'NATIVE_INPUT_CHANGED')
        self.started = True
        result = _compare_parts(arguments, segments.iter_journals(self.reader.parts()),
            input_transport_sha256=hashlib.sha256(_bytes(self.document)).hexdigest(),
            source_binding=self.document['source_binding'], preparation=preparation, history=history,
            replay_budget=self.document.get('replay_budget'))
        self.reader.verify()
        return result

    def __enter__(self):
        return self

    def __exit__(self, error_type, *unused):
        try:
            if error_type is None:
                self.verify()
        finally:
            self.closed = True


@contextmanager
def compare_source(directory, preparation_directory, *, maximum_preparation_bytes, history,
                   replay_budget=None):
    """Execute complete retained originals and hold their cut through consumption.

    This trusted offline entrypoint does not allocate a job or trial. The new
    dataset binding identifies the actual manifest and all transitively bound
    originals, rather than claiming a legacy reassembled JSON input identity.
    """
    from tools.gold_input_history import GoldInputHistory
    from tools.gold_segmented_replay import _compare_parts
    require(history is not None, 'HISTORY_REQUIRED')
    if replay_budget is not None:
        from quantos.contracts.perpetual_replay_budget import validate
        replay_budget = validate(replay_budget)
    with open_source(directory) as reader:
        if replay_budget is not None:
            require(reader.manifest['part_count'] <= replay_budget['maximum_environment_segments'],
                    'REPLAY_ENVIRONMENT_BUDGET')
        arguments=decode(_bytes(reader.manifest['configuration']))
        for name in ('quantity','initial_collateral','fee_rate'):
            arguments[name]=Decimal(arguments[name])
        binding=dict(schema='QUANTOS_GOLD_SOURCE_EXECUTION_BINDING_V1',
                     source_id=reader.manifest['source_id'],
                     manifest_sha256=hashlib.sha256(reader.raw_manifest).hexdigest(),
                     part_count=reader.manifest['part_count'], blob_bytes=reader.manifest['blob_bytes'])
        input_sha256 = binding['manifest_sha256']
        if replay_budget is not None:
            input_sha256 = hashlib.sha256(_bytes(dict(
                schema='QUANTOS_GOLD_BUDGETED_SOURCE_EXECUTION_INPUT_V1',
                manifest_sha256=binding['manifest_sha256'], replay_budget=replay_budget))).hexdigest()
        with GoldInputHistory(preparation_directory, maximum_bytes=maximum_preparation_bytes) as preparation:
            result=_compare_parts(arguments, segments.iter_journals(reader.parts()),
                input_transport_sha256=input_sha256, source_binding=binding,
                preparation=preparation, history=history, replay_budget=replay_budget)
            reader.verify()
            yield result
            reader.verify()


def write_request(directory, output):
    """Use the unchanged original fixed request; no job or trial is allocated."""
    from tools.gold_native_prefix import write_request as original
    return original(materialize(directory),output)


def from_session(quote_directory, bar_directory, output, *, segment_count,
                 release_identity, quantity, initial_collateral, fee_rate,
                 maximum_bytes, settlement_window_ms=None, portfolio_plan=None):
    """Retain each original prepared part without accumulating expanded bodies."""
    from tools.gold_native_prefix import session_parts
    with tempfile.TemporaryDirectory(prefix='quantos-gold-source-prefix-') as temporary:
        parts=session_parts(quote_directory,bar_directory,Path(temporary)/'prefixes',
            segment_count=segment_count,release_identity=release_identity,quantity=quantity,
            initial_collateral=initial_collateral,fee_rate=fee_rate,
            settlement_window_ms=settlement_window_ms)
        def configured():
            for index, part in enumerate(parts):
                if index == 0 and portfolio_plan is not None:
                    part = dict(part, portfolio_plan=deepcopy(portfolio_plan))
                yield part
        return freeze(configured(),output,maximum_bytes=maximum_bytes,
                      settlement_window_ms=settlement_window_ms)
