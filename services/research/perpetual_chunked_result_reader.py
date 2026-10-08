"""Read the complete original technical result through a private disposable index.

No balances are restored, no trials allocated and no economic authority granted.
Original content and physical cuts are verified before and after consumption.
"""
from contextlib import contextmanager
import codecs
import hashlib
import math
import os
from pathlib import Path
import stat

from quantos.contracts import perpetual_chunked_result as shape
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from quantos.streaming_identity import StreamObject, exact_stream_identity_hash, MAX_BYTES
from services.research.research_evidence_reader import _ReadSet
from services.research.perpetual_chunked_result_store import _Index, _Object

require = shape.require


class _Parser:
    def __init__(self, parts, index, maximum_scalar_bytes):
        self.parts=iter(parts)
        self.index=index
        self.maximum_scalar_bytes=maximum_scalar_bytes
        self.decoder=codecs.getincrementaldecoder('utf-8')('strict')
        self.buffer=''
        self.position=0
        self.ended=False

    def peek(self):
        while self.position==len(self.buffer) and not self.ended:
            raw=next(self.parts,None)
            self.ended=raw is None
            self.buffer=self.decoder.decode(b'' if raw is None else raw,final=self.ended)
            self.position=0
        return self.buffer[self.position] if self.position<len(self.buffer) else ''

    def take(self):
        char=self.peek()
        require(bool(char), 'JSON_TRUNCATED')
        self.position+=1
        return char

    def whitespace(self):
        while self.peek() and self.peek() in ' \t\r\n':
            self.take()

    def string(self):
        require(self.take()=='"', 'JSON_STRING')
        raw=bytearray(b'"')
        escaped=False
        while True:
            char=self.take()
            raw.extend(char.encode('utf-8'))
            require(len(raw)<=self.maximum_scalar_bytes, 'SCALAR_BYTES')
            if char=='"' and not escaped:
                break
            escaped=not escaped if char=='\\' else False
        value=decode(bytes(raw))
        # Surrogate escapes cannot be emitted by the original UTF-8 writer.
        value.encode('utf-8')
        return value

    def value(self, depth=0):
        require(depth<=256, 'JSON_DEPTH')
        self.whitespace()
        token=self.peek()
        require(bool(token), 'JSON_TRUNCATED')
        if token in '{[':
            self.take()
            object_mode=token=='{'
            end='}' if object_mode else ']'
            node=self.index.add('object' if object_mode else 'array')
            ordinal=0
            self.whitespace()
            if self.peek()==end:
                self.take()
            else:
                while True:
                    self.whitespace()
                    key=None
                    if object_mode:
                        key=self.string()
                        self.whitespace()
                        require(self.take()==':', 'JSON_COLON')
                    child=self.value(depth+1)
                    self.index.edge(node,ordinal,key,child)
                    ordinal+=1
                    self.whitespace()
                    separator=self.take()
                    if separator==end:
                        break
                    require(separator==',', 'JSON_SEPARATOR')
            self.index.finish(node,ordinal)
            return node
        if token=='"':
            return self.index.add('scalar',self.string())
        raw=bytearray()
        while self.peek() and self.peek() not in ',]} \t\r\n':
            raw.extend(self.take().encode('utf-8'))
            require(len(raw)<=self.maximum_scalar_bytes, 'SCALAR_BYTES')
        value=decode(bytes(raw))
        require(value is None or type(value) in (bool,int,float), 'JSON_SCALAR')
        require(type(value) is not int or value.bit_length()<=4096, 'JSON_INTEGER')
        require(type(value) is not float or math.isfinite(value), 'JSON_FLOAT')
        return self.index.add('scalar',value)


def open_result(directory, index_directory, *, expected_manifest_sha256,
                maximum_source_bytes, maximum_index_bytes, maximum_nodes=shape.MAX_ROWS,
                maximum_scalar_bytes=16_000_000):
    return _open_result(directory, index_directory, expected_manifest_sha256=expected_manifest_sha256,
        maximum_source_bytes=maximum_source_bytes, maximum_index_bytes=maximum_index_bytes,
        maximum_nodes=maximum_nodes, maximum_scalar_bytes=maximum_scalar_bytes,
        manifest_contract=shape, result_binding=_perpetual_binding)


def _perpetual_binding(result, manifest):
    require(result.get('schema_version')=='LINEAR_PERPETUAL_FORWARD_SIMULATION_V3'
        and result.get('authoritative') is False and result.get('evidence_status')=='TECHNICAL_VALIDATION_ONLY'
        and result.get('input_hash')==manifest['input_hash']
        and result.get('result_hash')==manifest['result_hash'], 'RESULT_BINDING')


@contextmanager
def _open_result(directory, index_directory, *, expected_manifest_sha256,
                 maximum_source_bytes, maximum_index_bytes, manifest_contract, result_binding,
                 maximum_nodes=shape.MAX_ROWS, maximum_scalar_bytes=16_000_000):
    """Expose a repeatable full result only after its byte and semantic checks."""
    require(shape.sha(expected_manifest_sha256), 'MANIFEST_PIN')
    require(all(type(v) is int and 0<v<=MAX_BYTES for v in (maximum_source_bytes,maximum_index_bytes)), 'BYTE_BUDGET')
    require(type(maximum_nodes) is int and 0<maximum_nodes<=shape.MAX_ROWS, 'NODE_BUDGET')
    require(type(maximum_scalar_bytes) is int and 0<maximum_scalar_bytes<=16_000_000, 'SCALAR_BUDGET')
    root=Path(directory).absolute()
    scratch=Path(index_directory).absolute()
    require(root.resolve()==root and scratch.resolve()==scratch
        and not root.is_relative_to(scratch) and not scratch.is_relative_to(root), 'PATH')
    reads=_ReadSet(maximum_directory_entries=shape.MAX_PARTS)
    require(set(reads.directory(root))=={'manifest.json','blobs'}, 'MEMBERS')
    info=reads.directories[root][0]
    require(info[2]==os.geteuid() and stat.S_IMODE(info[4])==0o700, 'DIRECTORY_CUSTODY')
    raw=reads.read(root/'manifest.json',shape.MAX_MANIFEST_BYTES)
    info=reads.files[root/'manifest.json'][0]
    require(info[2]==os.geteuid() and stat.S_IMODE(info[4])==0o600, 'MANIFEST_CUSTODY')
    require(hashlib.sha256(raw).hexdigest()==expected_manifest_sha256, 'MANIFEST_CHANGED')
    manifest=manifest_contract.validate(decode(raw))
    require(manifest['bytes']<=maximum_source_bytes, 'SOURCE_BYTES')
    require(set(reads.directory(root/'blobs'))=={p['sha256'] for p in manifest['parts']}, 'BLOB_MEMBERS')
    info=reads.directories[root/'blobs'][0]
    require(info[2]==os.geteuid() and stat.S_IMODE(info[4])==0o700, 'DIRECTORY_CUSTODY')
    digest=hashlib.sha256()
    def parts():
        for binding in manifest['parts']:
            path=root/'blobs'/binding['sha256']
            value=reads.read(path,binding['bytes'])
            info=reads.files[path][0]
            require(info[2]==os.geteuid() and stat.S_IMODE(info[4])==0o600, 'BLOB_CUSTODY')
            require(len(value)==binding['bytes'] and hashlib.sha256(value).hexdigest()==binding['sha256'], 'BLOB_CHANGED')
            digest.update(value)
            for offset in range(0,len(value),1_048_576):
                yield value[offset:offset+1_048_576]
    index=_Index(scratch,maximum_index_bytes,maximum_nodes)
    try:
        parser=_Parser(parts(),index,maximum_scalar_bytes)
        node=parser.value()
        parser.whitespace()
        require(not parser.peek() and digest.hexdigest()==manifest['sha256'], 'CONTENT_CHANGED')
        index.readonly()
        require(reads.directory(scratch)==('index.sqlite3',), 'INDEX_MEMBERS')
        result=index.value(node)
        require(isinstance(result,_Object), 'RESULT_BINDING')
        result_binding(result,manifest)
        payload=StreamObject(lambda: ((k,v) for k,v in result.items() if k!='result_hash'))
        require(exact_stream_identity_hash(payload)==result['result_hash'], 'RESULT_HASH')
        reads.recheck()
        require(index.pin()==index.bound, 'INDEX_CHANGED')
        yield result
        reads.recheck()
        require(index.pin()==index.bound, 'INDEX_CHANGED')
    finally:
        index.close()
