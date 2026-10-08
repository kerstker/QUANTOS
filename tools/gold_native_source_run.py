"""Trusted native source bootstrap; reuses original registration and producer."""
from contextlib import contextmanager
import copy
from pathlib import Path
import tempfile
import time

from quantos.contracts import registered_research_run as contract
from quantos.contracts import gold_native_source as shape
from tools import registered_research_run as original
from tools.gold_chunked_source import NativeSource


def source_bytes(directory, storage):
    """Separate original-byte budget; native metadata retains its old cap."""
    shape.storage(storage)
    root = original._directory(directory)
    members = {p.name for p in root.iterdir()}
    original.require('blobs' in members and members <= {'manifest.json', 'blobs'}, 'SOURCE_MEMBERS')
    blobs = original._directory(root/'blobs')
    names = {p.name for p in blobs.iterdir()}
    original.require(len(names) <= shape.source.MAX_BLOBS, 'SOURCE_FILES')
    total = 0
    for path in [*(blobs/name for name in names), *([root/'manifest.json'] if 'manifest.json' in members else [])]:
        # The original read verifies regular/private/single-link custody.
        raw = original._read(path, maximum=contract.MAX_INPUT_BYTES)
        if path.parent == blobs:
            original.require(original.plan_digest(raw) == path.name, 'SOURCE_BLOB_CHANGED')
        total += len(raw)
        original.require(total <= storage['maximum_bytes'], 'SOURCE_BYTES')
    return total


def prepare(directory, *, project_root, source_storage, collection_source_identity=None, **options):
    shape.storage(source_storage)
    if collection_source_identity is not None:
        shape.source_identity(collection_source_identity)
        original.require(options.get('forward_design') is None, 'REANALYSIS_NOT_FORWARD_DECLARED')
    with NativeSource(directory, replay_budget=source_storage.get('replay_budget'),
                      collection_source_identity=collection_source_identity) as inputs:
        original.require(inputs.reader.manifest['maximum_bytes'] <= source_storage['maximum_bytes'], 'SOURCE_BUDGET')
        arguments = original.gold._decode(copy.deepcopy(inputs.document))
        plan = original.prepare_gold(arguments, project_root=project_root, source_storage=source_storage, **options)
        destination = original.run_directory(project_root, plan)
        with original._locked(destination):
            root = original._mkdir(destination/shape.DIRECTORY)
            blobs = original._mkdir(root/'blobs')
            original.require(set(p.name for p in root.iterdir()) <= {'manifest.json', 'blobs'}, 'SOURCE_MEMBERS')
            original.require(set(p.name for p in blobs.iterdir()) <= inputs.reader.names, 'SOURCE_BLOBS')
            for name in sorted(inputs.reader.names):
                raw = inputs.reader.reads.read(inputs.reader.root/'blobs'/name,
                                               inputs.reader.used[name])
                original._put(blobs/name, None, raw=raw)
            inputs.verify()
            original._put(root/'manifest.json', None, raw=inputs.reader.raw_manifest)
            source_bytes(root, source_storage)
            with NativeSource(root, inputs.document):
                pass
        return plan


def run(arguments, *, destination, registry, clock_ms=None):
    arguments = copy.deepcopy(arguments)
    raw = original._gold_input(arguments)
    root = original._directory(Path(destination).absolute())
    with NativeSource(root/shape.DIRECTORY, original.gold._encode(arguments)) as inputs:
        active = [None, None]
        def verify_source(plan):
            original.require(plan['schema'] in contract.SOURCE_GOLD_SCHEMAS, 'SOURCE_PLAN')
            shape.bind_plan(plan, original.gold._encode(arguments))
            original.require(original.canonical(plan['source_identity']) == original.canonical(original.gold.source_identity())
                and arguments['release_identity'] == plan['source_identity'], 'SOURCE_CHANGED')
            original.require([t['candidate_id'] for t in plan['trials']] == list(original.gold.trial_candidates(arguments)),
                             'NATIVE_CANDIDATES')
            original.require(inputs.reader.manifest['maximum_bytes'] <= plan['source_storage']['maximum_bytes'], 'SOURCE_BUDGET')
            inputs.verify()
        def execute():
            return original.gold._execute(arguments, history=active[0], source_context=(inputs, active[1]))
        @contextmanager
        def execute_context(plan):
            from tools.gold_stream_history import GoldResearchHistory
            from tools.gold_input_history import GoldInputHistory
            scratch = Path(tempfile.mkdtemp(prefix='quantos-gold-native-source-'))
            with GoldResearchHistory(scratch/'accounts', maximum_history_bytes=plan['history_storage']['maximum_bytes']) as history:
                with GoldInputHistory(scratch/'prepared', maximum_bytes=plan['source_storage']['maximum_preparation_bytes']) as prepared:
                    active[:] = [history, prepared]
                    try:
                        yield
                        inputs.verify()
                    finally:
                        active[:] = [None, None]
        return original._native_run(destination=root, input_raw=raw, registry=registry,
            clock_ms=clock_ms or (lambda: time.time_ns()//1_000_000), adapter=contract.GOLD,
            execute=execute, verify_source=verify_source, execute_context=execute_context)
