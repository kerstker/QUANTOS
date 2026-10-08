"""Seal the complete original Gold report; report identity and cash are unchanged."""
import hashlib
from pathlib import Path

from quantos.contracts import gold_chunked_result as contract
from quantos.streaming_identity import json_parts, exact_stream_identity_hash
from services.research.research_evidence_reader import _ReadSet
from tools import registered_research_run as files


def write_report(report, directory, *, input_sha256, source_identity_sha256, storage):
    contract.storage(storage)
    root = Path(directory).absolute()
    files.require(root.resolve() == root, 'GOLD_RESULT_PATH')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    files._directory(root)
    files._mkdir(root/'blobs')
    parts, buffer = [], bytearray()
    size = 0
    digest = hashlib.sha256()
    reads = _ReadSet(maximum_directory_entries=contract.MAX_PARTS)
    def retain(raw):
        files.require(len(parts) < contract.MAX_PARTS, 'GOLD_RESULT_PARTS')
        name = hashlib.sha256(raw).hexdigest()
        files._put(root/'blobs'/name, None, raw=raw)
        files.require(reads.read(root/'blobs'/name, len(raw)) == raw, 'GOLD_RESULT_CHANGED')
        parts.append(dict(sha256=name, bytes=len(raw)))
    try:
        for raw in json_parts(report, maximum_bytes=storage['maximum_bytes']):
            size += len(raw)
            digest.update(raw)
            offset = 0
            while offset < len(raw):
                count = min(storage['chunk_bytes']-len(buffer), len(raw)-offset)
                buffer.extend(raw[offset:offset+count])
                offset += count
                if len(buffer) == storage['chunk_bytes']:
                    retain(bytes(buffer))
                    buffer.clear()
        if buffer:
            retain(bytes(buffer))
        manifest = dict(schema=contract.SCHEMA, status='TECHNICAL_VALIDATION_ONLY', parts=parts,
            bytes=size, sha256=digest.hexdigest(), input_sha256=input_sha256,
            result_hash=report['result_hash'], source_identity_sha256=source_identity_sha256,
            authoritative=False, economic_eligible=False, submission_enabled=False)
        contract.validate(manifest)
        contract.bind_result(report, manifest)
        files.require(exact_stream_identity_hash({k:v for k,v in report.items() if k!='result_hash'})
            == report['result_hash'], 'GOLD_RESULT_CHANGED')
        files.require(set(reads.directory(root/'blobs')) == {p['sha256'] for p in parts}, 'GOLD_RESULT_MEMBERS')
        reads.recheck()
        files._put(root/'manifest.json', manifest)
        manifest_hash = hashlib.sha256(files._read(root/'manifest.json')).hexdigest()
        return contract.reference(manifest, manifest_hash)
    except BaseException as exc:
        files._put(root/'failure.json', dict(status='INCOMPLETE', error_type=type(exc).__name__,
            bytes=size, parts=parts, authoritative=False, economic_eligible=False, submission_enabled=False))
        raise
