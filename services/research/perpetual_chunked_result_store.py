"""Private disposable SQLite index for complete retained JSON result views.

This persistence adapter owns schema, queries, bounds and read-only connection
lifetime. It never restores account state, allocates trials or grants authority.
The reader verifies original manifests, blobs and result identities separately.
"""
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from quantos.contracts import perpetual_chunked_result as shape
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from quantos.streaming_identity import StreamArray, StreamObject, key_order
from services.research.research_evidence_reader import _open, _identity

require = shape.require


class _Object(StreamObject, Mapping):
    def __init__(self, index, node, count):
        self.index, self.node, self.count = index, node, count
        super().__init__(self._items)

    def __len__(self):
        self.index.connection()
        return self.count

    def __iter__(self):
        for (key,) in self.index.connection().execute(
                'SELECT key FROM edges WHERE parent=? ORDER BY ordinal', (self.node,)):
            yield key

    def __getitem__(self, key):
        if type(key) is not str:
            raise KeyError(key)
        row = self.index.connection().execute('SELECT child FROM edges WHERE parent=? AND key=?',
                                             (self.node, key)).fetchone()
        if row is None:
            raise KeyError(key)
        return self.index.value(row[0])

    def _items(self):
        for key, child in self.index.connection().execute(
                'SELECT key,child FROM edges WHERE parent=? ORDER BY sort_key COLLATE BINARY', (self.node,)):
            yield key, self.index.value(child)


class _Array(StreamArray, Sequence):
    def __init__(self, index, node, count):
        self.index, self.node, self.count = index, node, count
        super().__init__(self._items)

    def __len__(self):
        self.index.connection()
        return self.count

    def __getitem__(self, ordinal):
        if isinstance(ordinal, slice):
            return [self[i] for i in range(*ordinal.indices(len(self)))]
        if ordinal < 0:
            ordinal += len(self)
        if not 0 <= ordinal < len(self):
            raise IndexError(ordinal)
        row = self.index.connection().execute('SELECT child FROM edges WHERE parent=? AND ordinal=?',
                                             (self.node, ordinal)).fetchone()
        require(row is not None, 'INDEX_ROW')
        return self.index.value(row[0])

    def __iter__(self):
        yield from self._items()

    def _items(self):
        for (child,) in self.index.connection().execute(
                'SELECT child FROM edges WHERE parent=? ORDER BY ordinal', (self.node,)):
            yield self.index.value(child)


class _Index:
    def __init__(self, directory, maximum_bytes, maximum_nodes):
        self.root = Path(directory).absolute()
        require(self.root.resolve()==self.root, 'INDEX_PATH')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.path=self.root/'index.sqlite3'
        fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        os.close(fd)
        self.maximum_bytes, self.maximum_nodes = maximum_bytes, maximum_nodes
        self.nodes = 0
        self.fd = None
        self.db = sqlite3.connect(self.path)
        try:
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA cache_size=-2048')
            self.db.execute('PRAGMA temp_store=FILE')
            self.db.execute('CREATE TABLE nodes(id INTEGER PRIMARY KEY,kind TEXT,raw BLOB,count INTEGER)')
            self.db.execute('CREATE TABLE edges(parent INTEGER,ordinal INTEGER,key TEXT,sort_key TEXT,child INTEGER,PRIMARY KEY(parent,ordinal),UNIQUE(parent,key)) WITHOUT ROWID')
            self.db.execute('CREATE INDEX edges_sort ON edges(parent,sort_key)')
            self.flush()
        except BaseException:
            self.close()
            raise

    def connection(self):
        require(self.db is not None, 'INDEX_CLOSED')
        return self.db

    def flush(self):
        size=self.db.execute('PRAGMA page_count').fetchone()[0]*self.db.execute('PRAGMA page_size').fetchone()[0]
        require(size<=self.maximum_bytes, 'INDEX_BYTES')
        self.db.commit()

    def add(self, kind, value=None):
        self.nodes+=1
        require(self.nodes<=self.maximum_nodes, 'INDEX_NODES')
        raw=None if kind!='scalar' else json.dumps(value,ensure_ascii=False,separators=(',', ':'),allow_nan=False).encode()
        self.db.execute('INSERT INTO nodes VALUES(?,?,?,0)', (self.nodes,kind,raw))
        if self.nodes%4096==0:
            self.flush()
        return self.nodes

    def edge(self, parent, ordinal, key, child):
        try:
            self.db.execute('INSERT INTO edges VALUES(?,?,?,?,?)',
                (parent,ordinal,key,None if key is None else key_order(key),child))
        except sqlite3.IntegrityError as exc:
            raise ValueError('CHUNKED_RESULT_DUPLICATE_KEY') from exc

    def finish(self, node, count):
        self.db.execute('UPDATE nodes SET count=? WHERE id=?', (count,node))

    def value(self, node):
        row=self.connection().execute('SELECT kind,raw,count FROM nodes WHERE id=?', (node,)).fetchone()
        require(row is not None, 'INDEX_ROW')
        kind,raw,count=row
        if kind=='scalar':
            return decode(raw)
        require(kind in ('object','array'), 'INDEX_TYPE')
        return (_Object if kind=='object' else _Array)(self,node,count)

    def pin(self):
        before=os.fstat(self.fd)
        digest=hashlib.sha256()
        os.lseek(self.fd,0,os.SEEK_SET)
        while True:
            raw=os.read(self.fd,1_048_576)
            if not raw:
                break
            digest.update(raw)
        fd=_open(self.path,os.O_RDONLY)
        try:
            require(_identity(os.fstat(fd))==_identity(before)==_identity(os.fstat(self.fd)), 'INDEX_CHANGED')
        finally:
            os.close(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink==1
            and before.st_uid==os.geteuid() and stat.S_IMODE(before.st_mode)==0o600, 'INDEX_CUSTODY')
        return _identity(before),digest.hexdigest()

    def readonly(self):
        self.flush()
        self.db.close()
        self.db=None
        self.fd=_open(self.path,os.O_RDONLY)
        self.db=sqlite3.connect(f'file:/proc/self/fd/{self.fd}?mode=ro&immutable=1',uri=True)
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute('PRAGMA trusted_schema=OFF')
        self.bound=self.pin()

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db=None
        if self.fd is not None:
            os.close(self.fd)
            self.fd=None
