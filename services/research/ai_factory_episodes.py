"""Immutable research episodes with append-only hash history and scoped retrieval."""
from pathlib import Path
import json
import math
import sqlite3
from services.research.ai_factory_context import SCOPES,is_hash
from services.research.ai_factory_store import digest

FIELDS={'episode_id','experiment_hash','snapshot_hash','partition_hash','scope','known_at_ms',
        'market_max_ms','evidence_hash','parent_ids','summary'}


def validate_episode(value):
    if (not isinstance(value,dict) or set(value)!=FIELDS or value['scope'] not in SCOPES
            or not all(is_hash(value[k]) for k in ('episode_id','experiment_hash','snapshot_hash','partition_hash','evidence_hash'))
            or any(type(value[k]) is not int or value[k]<0 for k in ('known_at_ms','market_max_ms'))
            or value['market_max_ms']>value['known_at_ms']):
        raise ValueError('invalid research episode identity')
    parents=value['parent_ids'];summary=value['summary']
    if not isinstance(parents,list) or len(parents)>72 or not all(is_hash(p) for p in parents) or len(set(parents))!=len(parents):
        raise ValueError('invalid episode parents')
    if (not isinstance(summary,dict) or set(summary)-{'candidate_id','status','score','reason'}
            or summary.get('status') not in ('PASSED','FAILED','UNKNOWN')
            or summary.get('candidate_id') is not None and (type(summary['candidate_id']) is not int or not 0<=summary['candidate_id']<72)
            or summary.get('score') is not None and (type(summary['score']) not in (int,float) or not math.isfinite(summary['score']))
            or 'reason' in summary and (not isinstance(summary['reason'],str) or not 1<=len(summary['reason'])<=160)):
        raise ValueError('invalid bounded research summary')


class ResearchEpisodes:
    def __init__(self,path,*,read_only=False):
        path=Path(path)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('unsafe episode path')
        self.read_only=read_only
        if not read_only:path.parent.mkdir(parents=True,exist_ok=True)
        self.c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) if read_only else sqlite3.connect(path)
        self.c.row_factory=sqlite3.Row
        if not read_only:
            self.c.execute('PRAGMA journal_mode=WAL');self.c.execute('PRAGMA synchronous=FULL')
            self.c.execute('CREATE TABLE IF NOT EXISTS episodes(seq INTEGER PRIMARY KEY,episode_id TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,previous_hash TEXT NOT NULL,receipt_hash TEXT NOT NULL)')
            self.c.commit()

    def __enter__(self):return self
    def __exit__(self,*args):self.c.close()

    def verify(self):
        previous='0'*64;known=-1;seen={}
        for expected,row in enumerate(self.c.execute('SELECT * FROM episodes ORDER BY seq'),1):
            if expected>100000:raise ValueError('research episode inventory exceeds bound')
            value=json.loads(row['payload']);validate_episode(value)
            receipt=digest({'seq':expected,'previous_hash':previous,'episode':value})
            if (row['seq']!=expected or row['previous_hash']!=previous or row['receipt_hash']!=receipt
                    or row['episode_id']!=value['episode_id'] or value['known_at_ms']<known):
                raise ValueError('research episode chain conflict')
            self._parents(value,seen)
            seen[value['episode_id']]=value;previous=receipt;known=value['known_at_ms']
        return {'episodes':len(seen),'head_hash':previous,'last_known_at_ms':known}

    @staticmethod
    def _parents(value,seen):
        for parent_id in value['parent_ids']:
            parent=seen.get(parent_id)
            if (parent is None or parent['known_at_ms']>value['known_at_ms']
                    or parent['experiment_hash']!=value['experiment_hash'] or parent['snapshot_hash']!=value['snapshot_hash']
                    or value['scope']=='INNER_SEARCH' and (parent['scope']!='INNER_SEARCH' or parent['partition_hash']!=value['partition_hash'])):
                raise ValueError('research episode parent unavailable or out of scope')

    def append(self,value):
        if self.read_only:raise ValueError('read-only episode store')
        validate_episode(value)
        self.c.execute('BEGIN IMMEDIATE')
        try:
            head=self.verify();old=self.c.execute('SELECT * FROM episodes WHERE episode_id=?',(value['episode_id'],)).fetchone()
            if old:
                if json.loads(old['payload'])!=value:raise ValueError('immutable research episode conflict')
                self.c.commit();return old['receipt_hash']
            if value['known_at_ms']<head['last_known_at_ms']:raise ValueError('research episode cannot backdate knowledge')
            parents={}
            for identity in value['parent_ids']:
                row=self.c.execute('SELECT payload FROM episodes WHERE episode_id=?',(identity,)).fetchone()
                if row:parents[identity]=json.loads(row[0])
            self._parents(value,parents)
            seq=head['episodes']+1;previous=head['head_hash'];receipt=digest({'seq':seq,'previous_hash':previous,'episode':value})
            self.c.execute('INSERT INTO episodes VALUES(?,?,?,?,?)',(seq,value['episode_id'],json.dumps(value,sort_keys=True,allow_nan=False),previous,receipt))
            self.c.commit();return receipt
        except BaseException:self.c.rollback();raise

    def read(self,context,*,limit=72):
        if type(limit) is not int or not 1<=limit<=72:raise ValueError('bounded episode query required')
        self.verify();visible=[]
        for row in self.c.execute('SELECT payload,receipt_hash FROM episodes ORDER BY seq DESC'):
            value=json.loads(row['payload'])
            if context.allows(value):visible.append({**value,'receipt_hash':row['receipt_hash']})
            if len(visible)>=limit:break
        return list(reversed(visible))

    def get(self,episode_id):
        self.verify()
        row=self.c.execute('SELECT payload FROM episodes WHERE episode_id=?',(episode_id,)).fetchone()
        return json.loads(row[0]) if row else None
