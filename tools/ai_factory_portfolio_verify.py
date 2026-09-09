"""Read-only consistency checks for frozen portfolio research and checkpoints."""
from services.research.ai_factory_store import digest
from services.research.trial_ledger import TrialLedger
from tools.btc_factory_research import verified


def verify_portfolio(directory):
    plan_path=directory/'plan.json'
    if not plan_path.exists():
        if any(directory.glob('*.json')):raise ValueError('portfolio plan missing')
        return
    plan=verified(plan_path);plan_body={k:v for k,v in plan.items() if k!='result_hash'}
    plan_hash=digest(plan_body);receipts={}
    paths=sorted((directory/'cases').glob('*.json'))
    if len(paths)>22:raise ValueError('portfolio trial budget exceeded')
    if paths or (directory/'trials.db').exists():
        with TrialLedger(str(directory/'trials.db'),create=False) as ledger:
            ledger.verify()
            for path in paths:
                row=verified(path);identity=digest({'plan':plan_body,'case':row['case']})
                if row['trial_id']!=identity or path.stem!=identity:
                    raise ValueError('portfolio trial identity conflict')
                if ledger.load(identity).status!=row['status']:
                    raise ValueError('portfolio trial status conflict')
                receipts[identity]=row
    selection_path=directory/'selection.json';model_path=directory/'model.json';result_path=directory/'result.json'
    selection=verified(selection_path) if selection_path.exists() else None
    if selection and (selection['plan_hash']!=plan_hash or selection['scope']!='INNER_ONLY'):
        raise ValueError('portfolio selection identity conflict')
    model=verified(model_path) if model_path.exists() else None
    if model and (not selection or model['plan_hash']!=plan_hash
                  or model['quarters']!=selection['selected']['quarters']
                  or model['baseline_quarters']!=selection['baseline']['quarters']
                  or model['components']!=plan['components']
                  or any(model[k] is not False for k in ('authoritative','live_eligible','economic_eligible'))):
        raise ValueError('portfolio model identity conflict')
    if result_path.exists():
        result=verified(result_path)
        if (not model or not selection or result['model_hash']!=model['result_hash']
                or result['plan_hash']!=plan_hash or result['selected']!=selection['selected']
                or result['baseline']!=selection['baseline']
                or len(result['inner_trials'])!=plan['inner_candidate_budget']
                or len(result['outer_cases'])!=plan['outer_case_budget']
                or any(result[k] is not False for k in ('authoritative','live_eligible','economic_eligible'))):
            raise ValueError('portfolio result identity conflict')
        for row in result['inner_trials']+result['outer_cases']:
            receipt=receipts.get(row['trial_id'])
            if receipt is None or any(row.get(k)!=v for k,v in receipt.items()):
                raise ValueError('portfolio result/trial conflict')
