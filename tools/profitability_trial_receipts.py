"""Use the existing append-only trial ledger for every bounded comparison case."""
from contextlib import contextmanager
from services.research.ai_factory_store import digest
from services.research.trial_ledger import TrialLedger
from tools.btc_factory_research import _event


@contextmanager
def registered_trial(output,registration,case):
    identity=digest({'registration':registration,'case':case})
    record={'trial_id':identity,'alpha_id':digest(case),
            'family_id':digest({'stage':registration.get('stage',registration.get('schema')),'scope':case.get('scope')}),
            'exact_definition_id':identity,'validation_policy_digest':digest(registration)}
    with TrialLedger(str(output/'trials.db')) as ledger:
        ledger.verify()
        _event(ledger,record,'PLANNED');_event(ledger,record,'RUNNING')
        try:yield identity
        except BaseException:
            _event(ledger,record,'FAILED');raise
        else:_event(ledger,record,'PASSED')
