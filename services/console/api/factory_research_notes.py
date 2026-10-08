"""Human-readable research facts and episode lineage; never a search tool."""
import json
from services.console.api.forward_dashboard import _read
from services.research.ai_factory_episodes import ResearchEpisodes


def research_notes(directory):
    verdict=None;episodes=[];role_runs=[];path=directory/'research-verdict.json'
    plans=sorted(directory.glob('research-roles/*/plan.json'))
    if len(plans)>18:raise ValueError('research role inventory exceeds budget')
    for path in plans:
        plan=_read(path)
        if plan['roles'] not in (['PROPOSE'],['PROPOSE','COUNTER_PROPOSE','SYNTHESIZE']):raise ValueError('unknown research roles')
        calls=[]
        for index,role in enumerate(plan['roles']):
            receipt=path.parent/'roles'/f'{index}.json'
            if receipt.exists():
                row=_read(receipt);proposal=row.get('proposal',{})
                calls.append({'role':role,'status':row['status'],'reason':row.get('reason'),
                    'elapsed_ms':row.get('wall_elapsed_ms'),'candidate_id':proposal.get('candidate_id')})
            elif receipt.with_suffix('.started.json').exists():calls.append({'role':role,'status':'UNFINISHED','reason':'RESERVED_WITHOUT_RECEIPT','elapsed_ms':None,'candidate_id':None})
        role_runs.append({'definition':path.parent.name,'protocol':plan['protocol'],'calls':calls})
    path=directory/'research-verdict.json'
    if path.exists():
        verdict=_read(path)
        if (verdict.get('scope')!='OUTER_EXPLANATION' or verdict.get('authoritative') is not False
                or verdict.get('search_feedback_allowed') is not False or verdict.get('economic_eligible') is not False):
            raise ValueError('invalid research explanation boundary')
    path=directory/'research-episodes.db'
    if path.exists():
        with ResearchEpisodes(path,read_only=True) as store:
            store.c.execute('BEGIN')
            head=store.verify()
            episodes=[json.loads(row[0]) for row in store.c.execute('SELECT payload FROM episodes ORDER BY seq DESC LIMIT 72')]
    else:head=None
    return {'verdict':verdict,'episodes':episodes,'history':head,'role_runs':role_runs,'scope':'HUMAN_READ_ONLY',
        'outer_results_available_to_search':False,'authoritative':False}
