"""Pure original project/run bindings, shared by capture and retained-cut validation.

The registry argument is a read-only view of original rows; this module has no
store, authority, mutation or IO capability.
"""
from quantos.contracts import registered_research_run as contract
from quantos.contracts.research_evidence_json import canonical, _decode
from quantos.contracts.registered_research_registration import (
    registration_evidence, initial_archive_manifest, native_trial_identity,
)

def require(value, reason):
    if not value:
        raise ValueError('PROJECT_RESEARCH_' + reason)



def _bind(runs, population, registry_history, registry):
    candidates = {(row['search_id'], row['config_hash']): row for row in population['search_candidates']}
    provenance = {row['trial_id']: row for row in population['provenance']}
    pairs, used_provenance, used_searches, definitions = set(), set(), set(), set()
    attempts = []
    for invocation, run in runs.items():
        plan = run['plan']
        if 'forward_design' in plan and 'input.json' in run['documents']:
            from quantos.contracts.gold_forward_evaluation_design import bind_input
            bind_input(plan['forward_design'], plan, run['documents']['input.json'])
        if plan['schema'] in contract.EVALUATED_SOURCE_REANALYSIS_SCHEMAS and 'input.json' in run['documents']:
            from quantos.contracts.gold_native_source import bind_plan
            bind_plan(plan, run['documents']['input.json'])
        elif plan['schema'] in contract.EVALUATED_SCHEMAS and 'input.json' in run['documents']:
            from quantos.contracts.registered_technical_input import project_manifest_inputs
            from quantos.contracts.backtest_identity import decode_identity_payload
            material = project_manifest_inputs(run['documents']['input.json'])
            dataset = decode_identity_payload(material['values']['data_identity'])['dataset_identity']
            require(plan['evaluation_core']['dataset_manifest_sha256'] == contract.digest(dataset), 'EVALUATION_DATASET')
        sid = plan['search']['search_id']
        require(sid not in used_searches, 'DUPLICATE_SEARCH_PLAN')
        used_searches.add(sid)
        started = [row for row in registry_history['events'] if row['search_id'] == sid and row['status'] == 'STARTED']
        if started:
            require(len(started) == 1 and all(started[0][key] == plan['search'][key]
                for key in ('search_id', 'search_kind', 'source', 'dataset_version'))
                and started[0]['recorded_at'] == plan['search']['recorded_at_ms'], 'SEARCH_PLAN')
        all_registered = True
        for trial in plan['trials']:
            pair = (sid, trial['config_hash'])
            require(pair not in pairs, 'DUPLICATE_ATTEMPT')
            pairs.add(pair)
            candidate = candidates.get(pair)
            original = provenance.get(trial['provenance_trial_id'])
            rows = run['ledger']['history'].get(trial['native_trial_id'], [])
            if candidate is not None:
                definitions.add(trial['config_hash'])
                require(started and candidate['registered_at'] >= started[0]['recorded_at'], 'CANDIDATE_TIME')
            if original is not None:
                record = _decode(original['record_json'].encode())
                require(candidate is not None and original['research_session_id'] == sid
                    and original['config_hash'] == trial['config_hash']
                    and record['actor_id'] == plan['actor_id']
                    and record['actor_type'] == 'AUTOMATED_SEARCH'
                    and record['artifact_hashes'] and record['artifact_hashes'][0] == contract.digest(plan),
                    'PROVENANCE_PLAN')
                used_provenance.add(trial['provenance_trial_id'])
            else:
                all_registered = False
            if rows:
                require(candidate is not None and original is not None, 'NATIVE_REGISTRATION_MISSING')
                require(rows[0]['at'] >= max(candidate['registered_at'], record['started_at']), 'NATIVE_REGISTRATION_TIME')
            status = rows[-1]['status'] if rows else ('REGISTERED_NOT_STARTED' if original else
                'PROVENANCE_NOT_PERSISTED' if candidate else 'NOT_REGISTERED')
            attempts.append(dict(invocation_id=invocation, native_trial_id=trial['native_trial_id'],
                search_id=sid, config_hash=trial['config_hash'], candidate_id=trial['candidate_id'],
                candidate_seq=candidate['candidate_seq'] if candidate else None,
                candidate_hash=candidate['candidate_hash'] if candidate else None,
                provenance_trial_id=original['trial_id'] if original else None, status=status))
        documents = run['documents']
        if 'registration.json' in documents:
            require(all_registered, 'REGISTRATION_RECEIPT_ROWS')
            actual = registration_evidence(plan, registry)
            require(canonical(actual) == canonical(documents['registration.json']), 'REGISTRATION_RECEIPT')
        else:
            require(not {'invocation.json', 'outcome.json'} & set(documents), 'REGISTRATION_RECEIPT_MISSING')
            run['partial'].append('REGISTRATION_RECEIPT_NOT_PERSISTED')
        _witnesses(run, [row for row in registry_history['events'] if row['search_id'] == sid])
    return attempts, dict(
        definitions=sorted({row['config_hash'] for row in population['trials']}-definitions),
        search_candidates=[row['candidate_seq'] for pair,row in candidates.items() if pair not in pairs],
        provenance=sorted(set(provenance)-used_provenance),
        searches=sorted({row['search_id'] for row in registry_history['events']}-used_searches))



def _witnesses(run, search_events):
    documents, plan = run['documents'], run['plan']
    invocation = documents.get('invocation.json')
    outcome = documents.get('outcome.json')
    risk_version = plan['schema'] == contract.RISK_EVALUATED_SCHEMA
    from quantos.contracts.registered_risk_replay import verify_lifecycle
    risk = verify_lifecycle(plan=plan, documents=documents, outcome=outcome)
    if risk_version and 'archive/report.json' in documents and risk is None and outcome is None:
        run['partial'].append('RISK_REPLAY_NOT_PERSISTED')
    rows = run['ledger']['history']
    running = documents.get('archive/running.json')
    manifest = documents.get('archive/manifest.json')
    if running is not None:
        expected = dict(schema='GOLD_BAR_RUNNING_ARCHIVE_V1' if plan['adapter'] == contract.GOLD
                        else 'QUANTOS_TECHNICAL_RUNNING_ARCHIVE_V1',
                        manifest=initial_archive_manifest(plan),
                        identities=[native_trial_identity(trial) for trial in plan['trials']])
        require(canonical(running) == canonical(expected), 'RUNNING_BINDING')
        require(set(rows) == {trial['native_trial_id'] for trial in plan['trials']}
            and all(len(events) >= 2 for events in rows.values()), 'RUNNING_LEDGER')
    else:
        require(invocation is None and outcome is None and manifest is None, 'RUNNING_MISSING')
        if any(len(events) >= 2 for events in rows.values()):
            run['partial'].append('RUNNING_RECORD_NOT_PERSISTED')
    if invocation is not None:
        require(type(invocation) is dict and set(invocation) ==
            {'schema', 'plan_sha256', 'registration_sha256', 'started_at_ms'}, 'INVOCATION_FIELDS')
        require(invocation['schema'] == 'QUANTOS_REGISTERED_RESEARCH_INVOCATION_V1'
            and invocation['plan_sha256'] == contract.digest(plan)
            and invocation['registration_sha256'] == run['originals']['registration.json']['sha256']
            and type(invocation['started_at_ms']) is int, 'INVOCATION_BINDING')
        require(set(rows) == {t['native_trial_id'] for t in plan['trials']}
            and all(len(events) >= 2 and events[1]['at'] <= invocation['started_at_ms'] for events in rows.values()),
            'INVOCATION_LEDGER')
    elif rows:
        run['partial'].append('INVOCATION_NOT_PERSISTED')
    if outcome is not None:
        outcome_fields = {'schema', 'plan_sha256', 'status', 'error_type', 'report_sha256', 'completed_at_ms'} | ({'risk_replay_sha256'} if risk_version else set())
        require(invocation is not None and type(outcome) is dict and set(outcome) == outcome_fields, 'OUTCOME_FIELDS')
        require(outcome['schema'] == ('QUANTOS_REGISTERED_RESEARCH_OUTCOME_V2' if risk_version else 'QUANTOS_REGISTERED_RESEARCH_OUTCOME_V1')
            and outcome['plan_sha256'] == contract.digest(plan) and outcome['status'] in ('PASSED', 'FAILED')
            and type(outcome['completed_at_ms']) is int
            and outcome['completed_at_ms'] >= invocation['started_at_ms'], 'OUTCOME_BINDING')
        if outcome['status'] == 'PASSED':
            require(outcome['error_type'] is None and 'archive/report.json' in run['originals']
                and outcome['report_sha256'] == run['originals']['archive/report.json']['sha256'], 'OUTCOME_REPORT')
        else:
            require(type(outcome['error_type']) is str and 0 < len(outcome['error_type']) <= 200
                and outcome['report_sha256'] is None and 'archive/report.json' not in documents, 'FAILED_REPORT')
        for events in rows.values():
            require(events[-1]['status'] in ('RUNNING', outcome['status']), 'OUTCOME_LEDGER')
            if events[-1]['status'] == outcome['status']:
                require(events[-1]['at'] == outcome['completed_at_ms'], 'OUTCOME_LEDGER_TIME')
        if any(events[-1]['status'] == 'RUNNING' for events in rows.values()):
            run['partial'].append('TERMINAL_LEDGER_NOT_PERSISTED')
        if manifest is None:
            run['partial'].append('MANIFEST_NOT_PERSISTED')
        else:
            _terminal_manifest(run, manifest, outcome)
        terminal = search_events[1:]
        if not terminal:
            run['partial'].append('SEARCH_TERMINAL_NOT_PERSISTED')
        else:
            require(len(terminal) == 1
                and terminal[0]['status'] == ('COMPLETED' if outcome['status'] == 'PASSED' else 'FAILED')
                and terminal[0]['recorded_at'] == outcome['completed_at_ms'], 'SEARCH_TERMINAL_BINDING')
            require(manifest is not None and all(events[-1]['status'] == outcome['status'] for events in rows.values()),
                    'SEARCH_TERMINAL_BEFORE_NATIVE')
    elif invocation is not None:
        run['partial'].append('OUTCOME_UNKNOWN')
        if manifest is not None:
            # Retain a lost outcome as unknown, while still checking the surviving
            # original manifest against the plan and unchanged report bytes.
            require(all(events[-1]['status'] in ('PASSED', 'FAILED') for events in rows.values())
                and len({events[-1]['status'] for events in rows.values()}) == 1, 'MANIFEST_LEDGER')
            terminal = next(iter(rows.values()))[-1]['status']
            _terminal_manifest(run, manifest, dict(status=terminal,
                report_sha256=run['originals'].get('archive/report.json', {}).get('sha256'),
                error_type=manifest.get('error_type') if type(manifest) is dict else None))



def _terminal_manifest(run, manifest, outcome):
    plan = run['plan']
    if plan['adapter'] == contract.GOLD:
        expected = dict(initial_archive_manifest(plan), terminal=outcome['status'])
        if outcome['status'] == 'PASSED':
            expected['report_sha256'] = outcome['report_sha256']
        else:
            require(type(outcome['error_type']) is str and 0 < len(outcome['error_type']) <= 200, 'FAILED_MANIFEST')
            expected['error_type'] = outcome['error_type']
    elif outcome['status'] == 'PASSED':
        report = run['documents'].get('archive/report.json')
        require(type(report) is dict and type(report.get('detail')) is dict, 'TECHNICAL_REPORT')
        expected = report['detail'].get('run_manifest_v2')
        from quantos.contracts.backtest_identity import RunManifestV2
        RunManifestV2.from_dict(expected)
    else:
        expected = dict(schema='QUANTOS_TECHNICAL_FAILED_MANIFEST_V1',
                        plan_sha256=contract.digest(plan), error_type=outcome['error_type'])
    require(canonical(manifest) == canonical(expected), 'MANIFEST_BINDING')


bind_project_runs = _bind
