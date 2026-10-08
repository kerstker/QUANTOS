"""Durable research-session controls and immutable experiment receipts.

The canonical TrialLedger remains the trial authority. This store holds workflow state only.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from services.research.gold_collection_program import CollectionPrograms


def encode(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()
def now_ms():return time.time_ns()//1000000


class FactoryStore(CollectionPrograms):
    def __init__(self,path):
        p=Path(path)
        if p.is_symlink():raise ValueError('unsafe factory store')
        p.parent.mkdir(parents=True,exist_ok=True)
        self.c=sqlite3.connect(p,timeout=30);self.c.row_factory=sqlite3.Row
        self.c.executescript('''
          CREATE TABLE IF NOT EXISTS collection_batch_failures(campaign_id INTEGER PRIMARY KEY, observed_ms INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS collection_programs(root_id INTEGER PRIMARY KEY, max_batches INTEGER NOT NULL, state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS collection_program_batches(root_id INTEGER NOT NULL,ordinal INTEGER NOT NULL,campaign_id INTEGER NOT NULL UNIQUE,input_id INTEGER NOT NULL,PRIMARY KEY(root_id,ordinal));
          CREATE TABLE IF NOT EXISTS campaign_collections(campaign_id INTEGER PRIMARY KEY, request TEXT NOT NULL, request_hash TEXT NOT NULL);
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
          CREATE TABLE IF NOT EXISTS profitability_inputs(attempt INTEGER NOT NULL,kind TEXT NOT NULL,
          revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 3),input_hash TEXT NOT NULL,
          data TEXT NOT NULL,operator TEXT NOT NULL,created_ms INTEGER NOT NULL,
          PRIMARY KEY(attempt,kind,revision),UNIQUE(attempt,kind,input_hash));
          CREATE TRIGGER IF NOT EXISTS profitability_inputs_no_update BEFORE UPDATE ON profitability_inputs
          BEGIN SELECT RAISE(ABORT,'profitability inputs are immutable'); END;
          CREATE TRIGGER IF NOT EXISTS profitability_inputs_no_delete BEFORE DELETE ON profitability_inputs
          BEGIN SELECT RAISE(ABORT,'profitability inputs are immutable'); END;
        ''')
    def __enter__(self):return self
    def __exit__(self,*args):self.c.close()

    @classmethod
    def read_collection_programs(cls,path):
        p=Path(path)
        if not p.exists():return []
        if p.is_symlink():raise ValueError('unsafe factory store')
        obj=object.__new__(cls);obj.c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=5);obj.c.row_factory=sqlite3.Row
        try:
            obj.c.execute('BEGIN')
            return obj.collection_programs_snapshot()
        finally:obj.c.close()

    def add_profitability_input(self,attempt,kind,data,operator):
        from services.research.profitability_inputs import validate_input
        validate_input(kind,data)
        if not isinstance(operator,str) or not 1<=len(operator.strip())<=128:raise ValueError('operator required')
        identity=digest(data)
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            if self.get(attempt)['state']!='COMPLETED':raise ValueError('completed frozen experiment required')
            old=self.c.execute('SELECT revision FROM profitability_inputs WHERE attempt=? AND kind=? AND input_hash=?',
                               (attempt,kind,identity)).fetchone()
            if old:return {'kind':kind,'revision':old[0],'input_hash':identity}
            count=self.c.execute('SELECT COUNT(*) FROM profitability_inputs WHERE attempt=? AND kind=?',(attempt,kind)).fetchone()[0]
            if count>=3:raise ValueError('three immutable input revisions already registered')
            self.c.execute('INSERT INTO profitability_inputs VALUES(?,?,?,?,?,?,?)',
                           (attempt,kind,count+1,identity,encode(data),operator,now_ms()))
            self.event(attempt,'PROFITABILITY_INPUT',{'kind':kind,'revision':count+1,'input_hash':identity,'operator':operator})
        return {'kind':kind,'revision':count+1,'input_hash':identity}

    @classmethod
    def profitability_inputs(cls,path,attempt,*,include_data=False,input_kind=None,revision=None):
        p=Path(path)
        if not p.exists():return []
        if p.is_symlink():raise ValueError('unsafe factory store')
        c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=5);c.row_factory=sqlite3.Row
        try:
            if not c.execute("SELECT name FROM sqlite_master WHERE name='profitability_inputs'").fetchone():return []
            columns='p.kind,p.revision,p.input_hash,p.operator,p.created_ms'+(',p.data' if include_data else '')
            latest=' AND p.revision=(SELECT MAX(q.revision) FROM profitability_inputs q WHERE q.attempt=p.attempt AND q.kind=p.kind)' if include_data else ''
            params=(attempt,)
            if input_kind is not None and revision is not None:
                latest=' AND p.kind=? AND p.revision=?';params=(attempt,input_kind,revision)
            rows=[dict(r) for r in c.execute('SELECT '+columns+' FROM profitability_inputs p WHERE p.attempt=?'+latest+' ORDER BY p.kind,p.revision',params)]
            for row in rows:
                if include_data:
                    row['data']=json.loads(row['data'])
                    if digest(row['data'])!=row['input_hash']:raise ValueError('input version integrity failed')
            return rows
        finally:c.close()
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

    def register_collection(self,config,*,input_id,seconds,operator,batches=1,decision_policy=None):
        from services.research.gold_factory_campaign import validate_campaign
        config=validate_campaign(config)
        if type(batches) is not int or not 1<=batches<=6:raise ValueError('invalid collection batch budget')
        if (type(input_id) is not int or not 1<=input_id<=1000 or input_id==config['input_id']
                or type(seconds) is not int or not 15<=seconds<=60
                or type(operator) is not str or not 1<=len(operator.strip())<=128):
            raise ValueError('invalid gold collection request')
        request={'config':config,'input_id':input_id,'seconds':seconds,'operator':operator}
        if decision_policy is not None:
            if decision_policy not in ('FIXED_TEMPLATE_RETROSPECTIVE_WINDOW_V1','FIRST_RECEIVED_WATERMARK_V1','AI_DESIGNED_WATERMARK_V1','AI_DESIGNED_ZONE_V1'):
                raise ValueError('invalid collection decision policy')
            request['decision_policy']=decision_policy
        if batches>1:request['collection_batches']=batches
        identity=digest(request)
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            old=self.get_collection(config['campaign_id'])
            if old is not None:
                if old['request_hash']!=identity:raise ValueError('collection campaign identity already reserved')
                return old
            if self.c.execute('SELECT 1 FROM campaigns WHERE campaign_id=?',(config['campaign_id'],)).fetchone():
                raise ValueError('campaign already exists')
            if self.c.execute('SELECT COUNT(*) FROM campaign_collections').fetchone()[0]>=100:
                raise ValueError('collection request capacity reached')
            self.c.execute('INSERT INTO campaign_collections VALUES(?,?,?)',(config['campaign_id'],encode(request),identity))
            if batches>1:
                self.c.execute('INSERT INTO collection_programs VALUES(?,?,?)',(config['campaign_id'],batches,'RUNNING'))
                self.c.execute('INSERT INTO collection_program_batches VALUES(?,?,?,?)',(config['campaign_id'],0,config['campaign_id'],input_id))
            self.event(None,'CAMPAIGN_COLLECTION_REQUEST',{'campaign_id':config['campaign_id'],'request_hash':identity})
        return self.get_collection(config['campaign_id'])

    def get_collection(self,campaign_id):
        if not self.c.execute("SELECT 1 FROM sqlite_master WHERE name='campaign_collections' AND type='table'").fetchone():return None
        row=self.c.execute('SELECT * FROM campaign_collections WHERE campaign_id=?',(campaign_id,)).fetchone()
        if row is None:return None
        result=dict(row);result['request']=json.loads(result['request'])
        if digest(result['request'])!=result['request_hash']:raise ValueError('collection request changed')
        return result

    @classmethod
    def read_collections(cls,path):
        p=Path(path)
        if not p.exists():return []
        if p.is_symlink():raise ValueError('unsafe factory store')
        obj=object.__new__(cls);obj.c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=5);obj.c.row_factory=sqlite3.Row
        try:
            obj.c.execute('BEGIN')
            if not obj.c.execute("SELECT 1 FROM sqlite_master WHERE name='campaign_collections' AND type='table'").fetchone():return []
            return [obj.get_collection(r[0]) for r in obj.c.execute('SELECT campaign_id FROM campaign_collections ORDER BY campaign_id DESC LIMIT 100')]
        finally:obj.c.close()

    def create_campaign(self,config,*,operator,now,collection_hash=None):
        from services.research.ai_factory_campaign import validate_campaign
        config=validate_campaign(config)
        if not isinstance(operator,str) or not 1<=len(operator.strip())<=128:raise ValueError('invalid operator')
        if config.get('trigger')=='drift':
            champion=self.get(config['champion_attempt'])
            if champion['state']!='COMPLETED' or not champion['result'].get('model_hash'):raise ValueError('frozen champion required')
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            collection=self.get_collection(config['campaign_id'])
            if collection is not None and collection_hash!=collection['request_hash']:
                raise ValueError('campaign id reserved for collection')
            old=self.c.execute('SELECT config,operator FROM campaigns WHERE campaign_id=?',(config['campaign_id'],)).fetchone()
            if old:
                if old[0]!=encode(config) or old[1]!=operator:raise ValueError('campaign identity already exists')
                return
            if self.c.execute('SELECT COUNT(*) FROM campaigns').fetchone()[0]>=100:raise ValueError('campaign registry capacity reached')
            self.c.execute('INSERT INTO campaigns VALUES(?,?,?,?,?,?)',(config['campaign_id'],encode(config),digest(config),self.collection_initial_state(config['campaign_id']),operator,now))
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

    def _gold_scope(self, config, snapshot=None):
        snapshot = self.campaign_snapshot() if snapshot is None else snapshot
        peers = {c['campaign_id']:c['config'] for c in snapshot['campaigns']
                 if c['config'].get('market') == 'XAUUSDT'
                 and c['config'].get('input_sha256') == config['input_sha256']}
        rounds = sorted((r for r in snapshot['rounds'] if r['campaign_id'] in peers),
                        key=lambda r:r['attempt'])
        return peers, rounds

    def reserve_campaign(self,campaign_id,*,now,trigger_key=None,execution_feedback=None):
        from services.research.ai_factory import validate_config
        from services.research.ai_factory_campaign import validate_campaign
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            self.verify_campaigns()
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
            gold = config.get('market') == 'XAUUSDT'
            feedback_attempt = None
            history_rounds = rounds
            if gold:
                peers, history_rounds = self._gold_scope(config)
                automatic=config.get('execution_feedback_mode')=='after_first_round' and bool(rounds)
                feedback_hash=config.get('execution_feedback_sha256')
                if automatic:
                    if execution_feedback is None:return None
                    from services.research.gold_execution_feedback import is_completed_episode
                    if (not is_completed_episode(execution_feedback) or
                        execution_feedback.get('research_identity',{}).get('attempt')!=rounds[-1]['attempt']):
                        raise ValueError('gold automatic feedback must follow preceding round')
                    feedback_hash=execution_feedback.get('result_hash')
                if automatic or feedback_hash is not None:
                    from services.research.gold_execution_feedback import research_trigger
                    decision=research_trigger(dict(config,execution_feedback_sha256=feedback_hash),execution_feedback,now=now)
                    if not decision['ready']:return None
                    identity=execution_feedback['research_identity']
                    origin=self.get(identity['attempt'])
                    if (origin['state']!='COMPLETED' or origin['config'].get('input_sha256')!=config['input_sha256'] or
                        not any(t.get('trial_id')==identity['trial_id'] and t.get('candidate_id')==identity['candidate_id']
                                for t in self.trials(identity['attempt']))):
                        raise ValueError('gold feedback original trial mismatch')
                    feedback_attempt = identity['attempt']
                    trigger_key=decision['key']
                    if any(r['trigger_key']==trigger_key for r in history_rounds):return None
                if any(r['state'] == 'RESERVED' for r in history_rounds):return None
                if len(history_rounds) >= 6:
                    self.c.execute("UPDATE campaigns SET state='EXHAUSTED' WHERE campaign_id=?",(campaign_id,));return None
                if history_rounds:
                    previous = history_rounds[-1]
                    interval = max(config['interval_hours'], peers[previous['campaign_id']]['interval_hours'])
                    if now-previous['finished_ms'] < interval*3600000:return None
            excluded=[];memory=[]
            for prior in history_rounds:
                memory.extend({'candidate_id':t['candidate_id'],'score':t.get('metrics',{}).get('score'),'status':t['status']} for t in self.trials(prior['attempt']) if t.get('candidate_id') is not None)
                excluded.extend(t['candidate_id'] for t in self.trials(prior['attempt']) if t.get('candidate_id') is not None)
            excluded=sorted(set(excluded))
            universe = 6 if gold else 72
            if any(type(n) is not int or not 0 <= n < universe for n in excluded):
                raise ValueError('campaign candidate scope corrupt')
            if universe-len(excluded)<config['budget']:
                self.c.execute("UPDATE campaigns SET state='EXHAUSTED' WHERE campaign_id=?",(campaign_id,));return None
            attempt=self.c.execute('SELECT COALESCE(MAX(attempt),0)+1 FROM sessions').fetchone()[0]
            if gold:
                from services.research.gold_research_memory import campaign_memory
                research_memory=campaign_memory(self,campaign_id,now=now,feedback_attempt=feedback_attempt)
                session=validate_config({'attempt':attempt,
                    **{k:config[k] for k in ('market','mode','budget','objective','input_id','input_sha256')},
                    'remaining_candidates':[n for n in range(6) if n not in excluded],
                    **({'research_memory':research_memory} if research_memory else {}),
                    **({'execution_feedback_sha256':feedback_hash} if feedback_hash is not None else {})})
            else:
                session=validate_config({'attempt':attempt,'mode':config['mode'],'budget':config['budget'],'objective':config['objective'],
                    **({'research_protocol':config['research_protocol']} if 'research_protocol' in config else {}),
                    'search_plan':{'version':2,'excluded_ids':excluded,'source_ids':config['source_ids'],'memory':memory}})
            self.c.execute('INSERT INTO sessions(attempt,config,config_hash,state,command,updated) VALUES(?,?,?,?,?,?)',
                (attempt,encode(session),digest(session),'READY','run',now))
            self.event(attempt,'CREATED',session)
            ordinal=len(rounds)
            self.event(attempt,'CAMPAIGN_ROUND',{'campaign_id':campaign_id,'ordinal':ordinal,'parent_attempts':[r['attempt'] for r in history_rounds]})
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
            if config.get('market') == 'XAUUSDT':
                peer_configs, scope = self._gold_scope(config, snapshot)
                feedback_keys=[r['trigger_key'] for r in scope if 'execution_feedback_sha256' in peer_configs[r['campaign_id']] or
                    peer_configs[r['campaign_id']].get('execution_feedback_mode')=='after_first_round' and r['ordinal']>0]
                if len(feedback_keys)!=len(set(feedback_keys)):
                    raise ValueError('gold execution feedback consumed more than once')
                pending = [r for r in scope if r['state'] == 'RESERVED']
                if len(scope) > 6 or len(pending) > 1 or (pending and pending[0] != scope[-1]):
                    raise ValueError('gold cross-campaign budget or reservation corrupt')
            for row in rounds:
                session=validate_config(self.get(row['attempt'])['config'])
                if config.get('market') == 'XAUUSDT':
                    consumed = {trial['candidate_id'] for prior in scope if prior['attempt'] < row['attempt']
                                for trial in self.trials(prior['attempt']) if trial.get('candidate_id') is not None}
                    automatic=config.get('execution_feedback_mode')=='after_first_round' and row['ordinal']>0
                    expected_feedback=row['trigger_key'] if automatic else config.get('execution_feedback_sha256')
                    if automatic and (type(expected_feedback) is not str or len(expected_feedback)!=64):
                        raise ValueError('gold automatic feedback round binding corrupt')
                    if (any(session.get(k)!=config[k] for k in ('market','objective','mode','budget','input_id','input_sha256'))
                            or session.get('execution_feedback_sha256')!=expected_feedback
                            or ('execution_feedback_sha256' in config and row['trigger_key']!=config['execution_feedback_sha256'])
                            or session['remaining_candidates'] != [n for n in range(6) if n not in consumed]):
                        raise ValueError('gold campaign session binding corrupt')
                    receipts = self.trials(row['attempt'])
                    if len(receipts) > 1 or (row['state'] == 'COMPLETED' and len(receipts) != 1):
                        raise ValueError('gold campaign trial budget corrupt')
                    for trial in receipts:
                        if trial.get('status') not in ('PASSED', 'FAILED'):
                            raise ValueError('gold campaign trial status corrupt')
                        candidate=trial.get('candidate_id')
                        if candidate is None and trial['status'] != 'FAILED':
                            raise ValueError('gold campaign successful choice missing')
                        if candidate is not None:
                            if type(candidate) is not int or candidate not in session['remaining_candidates']:
                                raise ValueError('gold campaign candidate reused or corrupt')
                elif (any(session[k]!=config[k] for k in ('objective','mode','budget')) or session.get('search_plan',{}).get('source_ids')!=config['source_ids']
                        or session.get('research_protocol')!=config.get('research_protocol')):
                    raise ValueError('campaign session binding corrupt')
                if row['state'] not in ('RESERVED','COMPLETED','FAILED','CANCELLED'):raise ValueError('campaign round state corrupt')
                if (row['state']=='RESERVED')!=(row['finished_ms'] is None):raise ValueError('campaign terminal time corrupt')
        if any(r['campaign_id'] not in campaigns for r in snapshot['rounds']):raise ValueError('orphan campaign round')
