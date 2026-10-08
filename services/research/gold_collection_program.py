"""Bounded fresh-data batches in the existing factory workflow database."""


class CollectionPrograms:
    def collection_programs_snapshot(self):
        if not self.c.execute("SELECT 1 FROM sqlite_master WHERE name='collection_programs' AND type='table'").fetchone():return []
        result=[]
        for row in self.c.execute('SELECT * FROM collection_programs ORDER BY root_id DESC LIMIT 100'):
            item=dict(row);root=self.get_collection(item['root_id'])
            if root is None or root['request'].get('collection_batches')!=item['max_batches']:
                raise ValueError('collection program policy mismatch')
            batches=[dict(b) for b in self.c.execute('SELECT * FROM collection_program_batches WHERE root_id=? ORDER BY ordinal',(item['root_id'],))]
            if not 1<=len(batches)<=item['max_batches']<=6 or [b['ordinal'] for b in batches]!=list(range(len(batches))):
                raise ValueError('collection program budget mismatch')
            for batch in batches:
                entry=self.get_collection(batch['campaign_id'])
                expected={**root['request'],'input_id':batch['input_id'],
                          'config':{**root['request']['config'],'campaign_id':batch['campaign_id']}}
                if entry is None or entry['request']!=expected:raise ValueError('collection batch request mismatch')
            result.append({**item,'batches':batches})
        return result

    def advance_collection_program(self,root_id,*,now,input_id,failed=False):
        from services.research.ai_factory_store import encode,digest
        if type(now) is not int or now<0 or type(input_id) is not int or not 1<=input_id<=1000:
            raise ValueError('invalid collection scheduling context')
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            program=next((p for p in self.collection_programs_snapshot() if p['root_id']==root_id),None)
            if program is None or program['state']!='RUNNING':return None
            batches=program['batches'];last=batches[-1]
            previous=self.c.execute('SELECT state FROM campaigns WHERE campaign_id=?',(last['campaign_id'],)).fetchone()
            if failed:
                self.c.execute('INSERT OR IGNORE INTO collection_batch_failures VALUES(?,?)',(last['campaign_id'],now))
            failure=self.c.execute('SELECT observed_ms FROM collection_batch_failures WHERE campaign_id=?',(last['campaign_id'],)).fetchone()
            if previous is None and failure is None:return self.get_collection(last['campaign_id'])
            if previous is not None and previous[0]=='STOPPED':
                self.c.execute("UPDATE collection_programs SET state='STOPPED' WHERE root_id=?",(root_id,));return None
            if failure is None and previous[0] not in ('COMPLETED','EXHAUSTED'):return None
            if len(batches)==program['max_batches']:
                has_failure=self.c.execute('SELECT 1 FROM collection_batch_failures f JOIN collection_program_batches b ON b.campaign_id=f.campaign_id WHERE b.root_id=?',(root_id,)).fetchone()
                self.c.execute('UPDATE collection_programs SET state=? WHERE root_id=?',('EXHAUSTED' if has_failure else 'COMPLETED',root_id));return None
            root=self.get_collection(root_id)['request']
            finished=failure[0] if failure is not None else self.c.execute('SELECT MAX(finished_ms) FROM campaign_rounds WHERE campaign_id=?',(last['campaign_id'],)).fetchone()[0]
            if finished is None or now-finished<root['config']['interval_hours']*3600000:return None
            used={self.get_collection(r[0])['request']['input_id'] for r in self.c.execute('SELECT campaign_id FROM campaign_collections')}
            if input_id in used or input_id==root['config']['input_id']:raise ValueError('collection input id already reserved')
            count=self.c.execute('SELECT COUNT(*) FROM campaign_collections').fetchone()[0]
            cid=self.c.execute('SELECT COALESCE(MAX(campaign_id),0)+1 FROM (SELECT campaign_id FROM campaigns UNION ALL SELECT campaign_id FROM campaign_collections)').fetchone()[0]
            if count>=100 or cid>1000:raise ValueError('collection program capacity exhausted')
            request={**root,'input_id':input_id,'config':{**root['config'],'campaign_id':cid}}
            self.c.execute('INSERT INTO campaign_collections VALUES(?,?,?)',(cid,encode(request),digest(request)))
            self.c.execute('INSERT INTO collection_program_batches VALUES(?,?,?,?)',(root_id,len(batches),cid,input_id))
            self.event(None,'COLLECTION_BATCH_RESERVED',{'root_id':root_id,'ordinal':len(batches),'campaign_id':cid,'input_id':input_id})
            return self.get_collection(cid)

    def control_collection_program(self,root_id,action,*,operator):
        if action not in ('pause','resume','stop') or type(operator) is not str or not operator.strip():
            raise ValueError('invalid collection program control')
        state={'pause':'PAUSED','resume':'RUNNING','stop':'STOPPED'}[action]
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            row=self.c.execute('SELECT state FROM collection_programs WHERE root_id=?',(root_id,)).fetchone()
            if row is None or row[0] in ('STOPPED','COMPLETED','EXHAUSTED'):raise ValueError('collection program not active')
            self.c.execute('UPDATE collection_programs SET state=? WHERE root_id=?',(state,root_id))
            self.c.execute("UPDATE campaigns SET state=? WHERE state IN ('RUNNING','PAUSED') AND campaign_id IN (SELECT campaign_id FROM collection_program_batches WHERE root_id=?)",(state,root_id))
            self.event(None,'COLLECTION_PROGRAM_CONTROL',{'root_id':root_id,'action':action,'operator':operator})

    def collection_initial_state(self,campaign_id):
        row=self.c.execute('SELECT p.state FROM collection_programs p JOIN collection_program_batches b ON b.root_id=p.root_id WHERE b.campaign_id=?',(campaign_id,)).fetchone()
        return row[0] if row is not None and row[0] in ('PAUSED','STOPPED') else 'RUNNING'
