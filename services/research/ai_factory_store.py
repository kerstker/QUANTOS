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
          CREATE TABLE IF NOT EXISTS campaigns(campaign_id INTEGER PRIMARY KEY, config TEXT NOT NULL,
          config_hash TEXT NOT NULL, state TEXT NOT NULL, operator TEXT NOT NULL, created_ms INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS campaign_rounds(campaign_id INTEGER NOT NULL, ordinal INTEGER NOT NULL,
          attempt INTEGER NOT NULL UNIQUE, state TEXT NOT NULL, created_ms INTEGER NOT NULL, finished_ms INTEGER, trigger_key TEXT,
          PRIMARY KEY(campaign_id,ordinal));
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

    def create_campaign(self,config,*,operator,now):
        from services.research.ai_factory_campaign import validate_campaign
        config=validate_campaign(config)
        if not isinstance(operator,str) or not 1<=len(operator.strip())<=128:raise ValueError('invalid operator')
        if config.get('trigger')=='drift':
            champion=self.get(config['champion_attempt'])
            if champion['state']!='COMPLETED' or not champion['result'].get('model_hash'):raise ValueError('frozen champion required')
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            old=self.c.execute('SELECT config,operator FROM campaigns WHERE campaign_id=?',(config['campaign_id'],)).fetchone()
            if old:
                if old[0]!=encode(config) or old[1]!=operator:raise ValueError('campaign identity already exists')
                return
            if self.c.execute('SELECT COUNT(*) FROM campaigns').fetchone()[0]>=100:raise ValueError('campaign registry capacity reached')
            self.c.execute('INSERT INTO campaigns VALUES(?,?,?,?,?,?)',(config['campaign_id'],encode(config),digest(config),'RUNNING',operator,now))
            self.event(None,'CAMPAIGN_CREATED',{'campaign_id':config['campaign_id'],'operator':operator})

    def control_campaign(self,campaign_id,action,*,operator):
        if action not in ('pause','resume','stop'):raise ValueError('invalid campaign action')
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            row=self.c.execute('SELECT state FROM campaigns WHERE campaign_id=?',(campaign_id,)).fetchone()
            if row is None or row[0]=='STOPPED':raise ValueError('unknown or stopped campaign')
            state={'pause':'PAUSED','resume':'RUNNING','stop':'STOPPED'}[action]
            self.c.execute('UPDATE campaigns SET state=? WHERE campaign_id=?',(state,campaign_id))
            self.event(None,'CAMPAIGN_CONTROL',{'campaign_id':campaign_id,'action':action,'operator':operator})

    def campaign_snapshot(self):
        # Older frozen databases remain readable without a schema migration.
        if not self.c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaigns'").fetchone():
            return {'campaigns':[],'rounds':[]}
        campaigns=[]
        for row in self.c.execute('SELECT * FROM campaigns ORDER BY campaign_id DESC LIMIT 100'):
            item=dict(row);item['config']=json.loads(item['config'])
            if digest(item['config'])!=item['config_hash']:raise ValueError('campaign integrity failed')
            campaigns.append(item)
        return {'campaigns':campaigns,'rounds':[dict(r) for r in self.c.execute('SELECT * FROM campaign_rounds ORDER BY campaign_id DESC,ordinal DESC LIMIT 1200')]}

    def reserve_campaign(self,campaign_id,*,now,trigger_key=None):
        from services.research.ai_factory import validate_config
        from services.research.ai_factory_campaign import validate_campaign
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            row=self.c.execute('SELECT * FROM campaigns WHERE campaign_id=?',(campaign_id,)).fetchone()
            if row is None or row['state']!='RUNNING':return None
            config=validate_campaign(json.loads(row['config']))
            if digest(config)!=row['config_hash']:raise ValueError('campaign integrity failed')
            rounds=[dict(r) for r in self.c.execute('SELECT * FROM campaign_rounds WHERE campaign_id=? ORDER BY ordinal',(campaign_id,))]
            pending=[r for r in rounds if r['state']=='RESERVED']
            if pending:return pending[0]
            if len(rounds)>=config['max_cycles']:
                self.c.execute("UPDATE campaigns SET state='COMPLETED' WHERE campaign_id=?",(campaign_id,));return None
            if trigger_key is not None and rounds and rounds[-1]['trigger_key']==trigger_key:return None
            if rounds and now-rounds[-1]['finished_ms']<config['interval_hours']*3600000:return None
            excluded=[];memory=[]
            for prior in rounds:
                memory.extend({'candidate_id':t['candidate_id'],'score':t.get('metrics',{}).get('score'),'status':t['status']} for t in self.trials(prior['attempt']) if t.get('candidate_id') is not None)
                excluded.extend(t['candidate_id'] for t in self.trials(prior['attempt']) if t.get('candidate_id') is not None)
            excluded=sorted(set(excluded))
            if 72-len(excluded)<config['budget']:
                self.c.execute("UPDATE campaigns SET state='EXHAUSTED' WHERE campaign_id=?",(campaign_id,));return None
            attempt=self.c.execute('SELECT COALESCE(MAX(attempt),0)+1 FROM sessions').fetchone()[0]
            session=validate_config({'attempt':attempt,'mode':config['mode'],'budget':config['budget'],'objective':config['objective'],
                'search_plan':{'version':2,'excluded_ids':excluded,'source_ids':config['source_ids'],'memory':memory}})
            self.c.execute('INSERT INTO sessions(attempt,config,config_hash,state,command,updated) VALUES(?,?,?,?,?,?)',
                (attempt,encode(session),digest(session),'READY','run',now))
            self.event(attempt,'CREATED',session)
            ordinal=len(rounds)
            self.event(attempt,'CAMPAIGN_ROUND',{'campaign_id':campaign_id,'ordinal':ordinal,'parent_attempts':[r['attempt'] for r in rounds]})
            self.c.execute('INSERT INTO campaign_rounds VALUES(?,?,?,?,?,?,?)',(campaign_id,ordinal,attempt,'RESERVED',now,None,trigger_key))
            return dict(self.c.execute('SELECT * FROM campaign_rounds WHERE campaign_id=? AND ordinal=?',(campaign_id,ordinal)).fetchone())

    def finish_campaign_round(self,campaign_id,ordinal,state,*,now):
        if state not in ('COMPLETED','FAILED','CANCELLED'):raise ValueError('invalid terminal round state')
        with self.c:
            self.c.execute("UPDATE campaign_rounds SET state=?,finished_ms=? WHERE campaign_id=? AND ordinal=? AND state='RESERVED'",(state,now,campaign_id,ordinal))

    @classmethod
    def read_campaigns(cls,path):
        p=Path(path)
        if p.is_symlink():raise ValueError('unsafe factory store')
        if not p.exists():return {'campaigns':[],'rounds':[]}
        obj=object.__new__(cls);obj.c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=5);obj.c.row_factory=sqlite3.Row
        try:
            obj.c.execute('BEGIN')
            obj.verify_campaigns()
            return obj.campaign_snapshot()
        finally:obj.c.close()

    def verify_campaigns(self):
        from services.research.ai_factory_campaign import validate_campaign
        from services.research.ai_factory import validate_config
        snapshot=self.campaign_snapshot()
        campaigns={c['campaign_id']:c for c in snapshot['campaigns']}
        for cid,campaign in campaigns.items():
            config=validate_campaign(campaign['config'])
            rounds=sorted((r for r in snapshot['rounds'] if r['campaign_id']==cid),key=lambda r:r['ordinal'])
            if len(rounds)>config['max_cycles'] or [r['ordinal'] for r in rounds]!=list(range(len(rounds))):raise ValueError('campaign round budget or sequence corrupt')
            for row in rounds:
                session=validate_config(self.get(row['attempt'])['config'])
                if any(session[k]!=config[k] for k in ('objective','mode','budget')) or session.get('search_plan',{}).get('source_ids')!=config['source_ids']:
                    raise ValueError('campaign session binding corrupt')
                if row['state'] not in ('RESERVED','COMPLETED','FAILED','CANCELLED'):raise ValueError('campaign round state corrupt')
                if (row['state']=='RESERVED')!=(row['finished_ms'] is None):raise ValueError('campaign terminal time corrupt')
        if any(r['campaign_id'] not in campaigns for r in snapshot['rounds']):raise ValueError('orphan campaign round')
