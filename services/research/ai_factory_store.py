"""Durable research-session controls and immutable experiment receipts.

The canonical TrialLedger remains the trial authority. This store holds workflow state only.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def encode(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()
def now_ms():return time.time_ns()//1000000


class FactoryStore:
    def __init__(self,path):
        p=Path(path)
        if p.is_symlink():raise ValueError('unsafe factory store')
        p.parent.mkdir(parents=True,exist_ok=True)
        self.c=sqlite3.connect(p,timeout=30);self.c.row_factory=sqlite3.Row
        self.c.executescript('''
          CREATE TABLE IF NOT EXISTS sessions(attempt INTEGER PRIMARY KEY, config TEXT NOT NULL,
          config_hash TEXT NOT NULL, state TEXT NOT NULL, command TEXT NOT NULL, updated INTEGER NOT NULL,
          result TEXT NOT NULL DEFAULT '{}');
          CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, attempt INTEGER,
          at INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS trials(attempt INTEGER, ordinal INTEGER, payload TEXT NOT NULL,
          sha256 TEXT NOT NULL, PRIMARY KEY(attempt,ordinal));
        ''')
    def __enter__(self):return self
    def __exit__(self,*args):self.c.close()
    def event(self,attempt,kind,payload):
        self.c.execute('INSERT INTO events(attempt,at,kind,payload) VALUES(?,?,?,?)',(attempt,now_ms(),kind,encode(payload)))
    def create(self,config):
        attempt=config['attempt'];raw=encode(config)
        with self.c:
            row=self.c.execute('SELECT config FROM sessions WHERE attempt=?',(attempt,)).fetchone()
            if row:
                if row[0]!=raw:raise ValueError('attempt already belongs to another configuration')
                return self.get(attempt)
            self.c.execute('INSERT INTO sessions(attempt,config,config_hash,state,command,updated) VALUES(?,?,?,?,?,?)',
                           (attempt,raw,digest(config),'READY','run',now_ms()))
            self.event(attempt,'CREATED',config)
        return self.get(attempt)
    def get(self,attempt):
        r=self.c.execute('SELECT * FROM sessions WHERE attempt=?',(attempt,)).fetchone()
        if r is None:raise ValueError('unknown research session')
        d=dict(r);d['config']=json.loads(d['config']);d['result']=json.loads(d['result'])
        if digest(d['config'])!=d['config_hash']:raise ValueError('configuration integrity failed')
        return d
    def control(self,attempt,action):
        if action not in ('pause','resume','cancel'):raise ValueError('unknown control')
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            row=self.get(attempt)
            if row['state'] in ('CANCELLED','COMPLETED') or row['command']=='cancel':raise ValueError('session is terminal')
            command='run' if action=='resume' else action
            self.c.execute('UPDATE sessions SET command=?,updated=? WHERE attempt=?',(command,now_ms(),attempt))
            if action=='cancel' and row['state'] in ('READY','PAUSED','FAILED'):
                self.c.execute("UPDATE sessions SET state='CANCELLED' WHERE attempt=?",(attempt,))
            self.event(attempt,'CONTROL',{'action':action})
    def update(self,attempt,state,result=None):
        with self.c:
            self.c.execute('UPDATE sessions SET state=?,updated=? WHERE attempt=?',(state,now_ms(),attempt))
            if result is not None:self.c.execute('UPDATE sessions SET result=? WHERE attempt=?',(encode(result),attempt))
            self.event(attempt,state,result or {})
    def record_trial(self,attempt,ordinal,payload):
        raw=encode(payload)
        with self.c:
            old=self.c.execute('SELECT payload FROM trials WHERE attempt=? AND ordinal=?',(attempt,ordinal)).fetchone()
            if old:
                if old[0]!=raw:raise ValueError('trial receipt is immutable')
                return
            self.c.execute('INSERT INTO trials VALUES(?,?,?,?)',(attempt,ordinal,raw,digest(payload)))
            self.event(attempt,'TRIAL',payload)
    def trials(self,attempt):
        rows=self.c.execute('SELECT * FROM trials WHERE attempt=? ORDER BY ordinal',(attempt,)).fetchall()
        result=[]
        for row in rows:
            p=json.loads(row['payload'])
            if digest(p)!=row['sha256']:raise ValueError('trial receipt integrity failed')
            result.append(p)
        return result
    def events(self,attempt):
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in self.c.execute(
            'SELECT * FROM events WHERE attempt=? ORDER BY seq DESC LIMIT 200',(attempt,))]
    @classmethod
    def snapshot(cls,path,attempt=None):
        p=Path(path)
        if not p.exists():return {'sessions':[],'selected':None,'trials':[],'events':[]}
        if p.is_symlink():raise ValueError('unsafe factory store')
        obj=object.__new__(cls);obj.c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=5);obj.c.row_factory=sqlite3.Row
        try:
            obj.c.execute('BEGIN')
            sessions=[obj.get(r[0]) for r in obj.c.execute('SELECT attempt FROM sessions ORDER BY updated DESC LIMIT 50')]
            selected=obj.get(attempt) if attempt is not None else (sessions[0] if sessions else None)
            return {'sessions':sessions,'selected':selected,'trials':obj.trials(selected['attempt']) if selected else [],
                    'events':obj.events(selected['attempt']) if selected else []}
        finally:obj.c.close()
