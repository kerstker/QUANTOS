"""Recompute Gold reviews from complete native originals and original kernels.

Future dataset bytes are bound on arrival, never assigned a pre-window hash.
The embedded cash account is evidence, never an invented search attempt.
"""

import copy
from decimal import Decimal

from quantos.contracts import gold_forward_evaluation_design as design
from quantos.contracts import gold_project_statistics as contract
from quantos.contracts import research_statistical_review as common
from quantos.contracts import gold_reanalysis_statistics as backtest
from quantos.contracts.decimal_arithmetic import ledger_decimal_context
from quantos.contracts.registered_research_run import GOLD, EVALUATED_SOURCE_REANALYSIS_SCHEMAS
from services.validation import gold_native_statistics as native
from services.validation import project_research_statistics as original
from services.validation.project_research_evidence_artifact import last_project_fact_ms
from services.validation.project_research_population import validate_project_cut


def _identity(row):
    return {key: row[key] for key in common.IDENTITY_FIELDS}


def _historical(policy):
    return policy.get('schema') == backtest.SCHEMA


def _scoped(policy):
    return _historical(policy) or policy['schema'] in design.SCOPED_REVIEW_SCHEMAS


def _sample(policy):
    return copy.deepcopy(policy['sample']) if _historical(policy) else {
        key:policy['metrics'][key] for key in ('start_ms', 'end_ms', 'frequency_ms')}


def _declaration(run, policy=None):
    plan = run['plan']
    if plan['schema'] in EVALUATED_SOURCE_REANALYSIS_SCHEMAS:
        common.require(isinstance(policy, dict), 'GOLD_BACKTEST_POLICY_REQUIRED')
        return backtest.bind_core(policy, plan['evaluation_core'])
    actual = plan['forward_design']
    common.require(policy is None or common.canonical(policy) == common.canonical(actual), 'GOLD_POLICY_BINDING')
    return actual


def _bound(run, policy):
    plan = run['plan']
    if _historical(policy):
        if (plan['schema'] not in EVALUATED_SOURCE_REANALYSIS_SCHEMAS
                or plan['evaluation_core']['selection']['validation_policy_sha256'] != common.digest(policy)):
            return False
        backtest.bind_core(policy, plan['evaluation_core'])
        return True
    actual = plan.get('forward_design')
    return actual is not None and actual['schema'] in design.FULL_REVIEW_SCHEMAS and (
        contract.study_sha256(actual) == contract.study_sha256(policy))


def _complete(cut, invocation_id):
    run = cut["runs"][invocation_id]
    rows = [row for row in cut["attempts"] if row["invocation_id"] == invocation_id]
    return not run["partial"] and len(rows) == len(run["plan"]["trials"]) and all(
        row["status"] == "PASSED" for row in rows)


def _benchmark(cut, invocation_id, reports):
    record = result = None
    if _complete(cut, invocation_id):
        run = cut["runs"][invocation_id]
        report, _ = native._report(run, reports=reports)
        record = run["originals"]["archive/report.json"]["sha256"]
        result = native._simulation(report, None, streamed="result_storage" in run["plan"])["result_hash"]
    return dict(kind="ORIGINAL_EMBEDDED_CASH_ACCOUNT", id="GOLD_NO_ORDER_ORIGINAL_ACCOUNT_V1",
        invocation_id=invocation_id, original_record_sha256=record, native_result_sha256=result)


def project_cohort(*, project_cut, invocation_id, reports=None, policy=None):
    cut = validate_project_cut(project_cut)
    from quantos.contracts.project_chunked_results import full_report_population
    full_report_population(cut, reports)
    policy = _declaration(cut['runs'][invocation_id], policy)
    study = contract.study_sha256(policy)
    rows = sorted((_identity(row) for row in cut["attempts"]),
        key=lambda row: (row["invocation_id"], row["native_trial_id"]))
    selected = None
    selection = policy['selection_policy'] if _historical(policy) else policy['selection']
    if selection["method"] == "PREDECLARED_CANDIDATE":
        selected = next(row for row in rows if row["invocation_id"] == invocation_id
            and original._selected(cut, row)[2]["candidate_id"] == selection["candidate_id"])
    value = dict(schema=contract.COHORT_SCHEMA, study_sha256=study,
        policy_sha256=common.digest(policy), project_cut_sha256=cut["cut_sha256"],
        frozen_at_ms=last_project_fact_ms(cut), invocation_id=invocation_id, attempts=rows,
        selected_attempt=selected, benchmark_attempt=_benchmark(cut, invocation_id, reports))
    if _scoped(policy):
        included, excluded = [], []
        for row in rows:
            other = cut['runs'][row['invocation_id']]
            declaration = other['plan'].get('forward_design')
            inside = (_bound(other, policy) if _historical(policy) else
                declaration is not None and common.digest(declaration) == value['policy_sha256'])
            (included if inside else excluded).append(row)
        value.update(schema=contract.BACKTEST_COHORT_SCHEMA if _historical(policy) else contract.SCOPED_COHORT_SCHEMA, comparison=dict(
            schema=contract.COMPARISON_SCHEMA, declaration_sha256=value["policy_sha256"],
            declared_candidate_count=len(cut['runs'][invocation_id]['plan']['trials']) if _historical(policy)
                else len(policy['cohort']), included_attempts=included, excluded_attempts=excluded))
    return contract.validate_cohort(value)


def _series(cut, row, policy, cache, reports):
    status, run, trial = original._selected(cut, row)
    actual = run['plan'].get('evaluation_core') if _historical(policy) else run['plan'].get('forward_design')
    bound = _bound(run, policy)
    value = dict(identity=copy.deepcopy(row), native_status=status["status"], series_status="INSUFFICIENT",
        policy_bound=bound, series_sha256=None)
    facts = dict(points=None, cash=None, dataset=None, input_sha256=run["plan"]["input_sha256"], reasons=[])
    if not bound:
        facts["reasons"].append("STATISTICAL_POLICY_NOT_PREREGISTERED_FOR_THIS_STUDY")
    if actual is None or run["plan"]["adapter"] != GOLD:
        facts["reasons"].append("GOLD_DECLARED_SERIES_UNAVAILABLE")
    elif not _complete(cut, row["invocation_id"]):
        facts["reasons"].append("NATIVE_ATTEMPT_NOT_COMPLETE")
    else:
        invocation = row["invocation_id"]
        if invocation not in cache:
            report, document = native._report(run, reports=reports)
            dataset = native._plain_dataset(report["dataset_identity"]) if "result_storage" in run["plan"] else report["dataset_identity"]
            settlements = native.funding_settlements(document, dataset,
                source_parts=getattr(reports, 'source_inputs', {}).get(invocation))
            cache[invocation] = report, settlements
        report, settlements = cache[invocation]
        sample = _sample(policy)
        streamed = "result_storage" in run["plan"]
        dataset = native._plain_dataset(report['dataset_identity']) if streamed else report['dataset_identity']
        if _historical(policy):
            episode = dataset['quote_manifest']
            end = max([episode['end_ms'], *(value['available_at_ms'] for value in settlements)])
            common.require(policy['split_policy']['maximum_information_horizon_ms'] >=
                end-episode['start_ms']+sample['frequency_ms'], 'GOLD_BACKTEST_EPISODE_HORIZON')
        simulation = native._simulation(report, trial["candidate_id"], streamed=streamed)
        projected, _, _ = native._series(simulation, sample, settlements, streamed=streamed,
            grid_budget=None if _historical(policy) else policy['statistical_policy'].get('computation_budget'),
            evidence_kind='BACKTEST' if _historical(policy) else 'FORWARD')
        facts['dataset'] = dataset['source_binding']['manifest_sha256'] if _historical(policy) else common.digest(dataset)
        facts["native_result_sha256"] = projected["native_result_sha256"]
        grid = projected["grid"]
        facts["cash"] = dict(full_run=projected["metrics"],
            positions_flat=not simulation["ending_state"]["positions"],
            sample_terminal_equity_matches=grid is not None
                and grid["points"][-1]["equity"] == projected["metrics"]["ending_equity"],
            post_sample_execution_count=sum(e["event_type"] == "TRADE" and e["timestamp"] > sample["end_ms"]
                for e in simulation["ledger_events"]),
            closed_at_sample_end=projected["full_run_covered"], sample_is_full_event_history=False)
        if grid is None:
            facts["reasons"].append("INSUFFICIENT_SAMPLE_COVERAGE")
        else:
            points = grid["points"]
            facts["points"] = points
            if len(points) >= 3 and all(Decimal(p["equity"]) > 0 for p in points):
                value["series_status"] = "READY"
            else:
                facts["reasons"].append("INSUFFICIENT_OR_NONPOSITIVE_EQUITY")
            covered = projected["full_run_covered"]
            if not covered:
                facts["reasons"].append("FULL_RUN_TERMINAL_NOT_COVERED")
    value["series_sha256"] = common.digest(facts)
    return value, facts


@ledger_decimal_context()
def review_project_statistics(*, project_cut, policy, cohort, as_of_ms, reports=None):
    cut = validate_project_cut(project_cut)
    from quantos.contracts.project_chunked_results import full_report_population
    full_report_population(cut, reports)
    study = contract.study_sha256(policy)
    cohort = contract.validate_cohort(cohort)
    expected = project_cohort(project_cut=cut, invocation_id=cohort["invocation_id"], reports=reports, policy=policy)
    common.require(common.canonical(expected) == common.canonical(cohort), "GOLD_COMPLETE_COHORT")
    run = cut["runs"][cohort["invocation_id"]]
    _declaration(run, policy)
    declared_at = policy['study_scope']['registered_at_ms'] if _historical(policy) else policy['declared_at_ms']
    common.require((declared_at <= run['plan']['search']['recorded_at_ms'] if _historical(policy)
        else declared_at < run['plan']['search']['recorded_at_ms'])
        and run["plan"]["search"]["recorded_at_ms"]
        <= cohort["frozen_at_ms"] <= common.integer(as_of_ms), "GOLD_REVIEW_TIME")
    rules = copy.deepcopy(policy if _historical(policy) else policy['statistical_policy'])
    scoped = _scoped(policy)
    comparison = cohort.get("comparison")
    included = comparison["included_attempts"] if scoped else cohort["attempts"]
    common.integer(cut["attempt_count"], maximum=rules["trial_budget"]["max_attempts"])
    rules['sample'] = _sample(policy)
    original._work_budget(len(included), common.sample(rules["sample"], budget=rules.get('computation_budget')),
        rules["cpcv_policy"], rules["pbo_policy"]["block_count"], budget=rules.get('computation_budget'))
    reasons = set()
    if cut["population_coverage"] != "COMPLETE":
        reasons.add("PROJECT_POPULATION_UNMAPPED")
    if not cut["all_runs_finalized"]:
        reasons.add("PROJECT_RUNS_UNFINISHED")
    attempts, series, cache = [], {}, {}
    for row in cohort["attempts"]:
        if scoped and row not in included:
            status, other_run, _ = original._selected(cut, row)
            facts = dict(points=None, cash=None, dataset=None, input_sha256=other_run["plan"]["input_sha256"],
                reasons=[], comparison_scope="OUTSIDE_THIS_PREREGISTERED_DECLARATION")
            attempts.append(dict(identity=copy.deepcopy(row), native_status=status["status"],
                series_status="OUTSIDE_COMPARISON_SCOPE", policy_bound=False, series_sha256=common.digest(facts)))
            series[common.digest(row)] = facts
            continue
        attempt, facts = _series(cut, row, policy, cache, reports)
        attempts.append(attempt)
        series[common.digest(row)] = facts
        reasons.update(facts["reasons"])
    selected_key = common.digest(cohort["selected_attempt"]) if cohort["selected_attempt"] is not None else None
    if selected_key is None:
        reasons.add("NO_PREDECLARED_SELECTION")
    else:
        selection = policy['selection_policy'] if _historical(policy) else policy['selection']
        matching = [r for r in included if original._selected(cut, r)[2]["candidate_id"]
            == selection['candidate_id'] and next(a for a in attempts if a["identity"] == r)["policy_bound"]]
        common.require(matching and matching[0] == cohort["selected_attempt"], "GOLD_PREDECLARED_ATTEMPT_TIE")
    baseline_points = None
    if _complete(cut, cohort["invocation_id"]):
        report, settlements = cache[cohort["invocation_id"]]
        streamed = "result_storage" in run["plan"]
        baseline, _, _ = native._series(native._simulation(report, None, streamed=streamed), rules["sample"],
            settlements, streamed=streamed, grid_budget=rules.get('computation_budget'),
            evidence_kind='BACKTEST' if _historical(policy) else 'FORWARD')
        if baseline["grid"] is not None:
            baseline_points = baseline["grid"]["points"]
    if baseline_points is None:
        reasons.add("BENCHMARK_SAMPLE_UNAVAILABLE")
    metrics = dict(dsr=None, observed_sr=None, var_sr=None, n_trials=cut["attempt_count"], cpcv=None,
        cpcv_thresholds=None, pbo=None, matrix_sha256=None, calendar_sha256=None, skew=None, kurtosis=None,
        sample_net_return=None, benchmark_net_return=None, benchmark_excess=None, maximum_drawdown=None,
        selected_cash=series[selected_key]["cash"] if selected_key is not None else None)
    checks = {name: "INSUFFICIENT" for name in common.CHECKS}
    ready = [a for a in attempts if a["series_status"] == "READY" and a["policy_bound"]]
    complete = len(ready) == len(included) and len(ready) >= 2 and cut["population_coverage"] == "COMPLETE"
    if scoped and (len(included) != comparison["declared_candidate_count"]
            or {r["invocation_id"] for r in included} != {cohort["invocation_id"]}):
        complete = False
        reasons.add("DECLARATION_ATTEMPT_BUDGET_MISMATCH")
    global_variance_unknown = scoped and bool(comparison["excluded_attempts"])
    if global_variance_unknown:
        reasons.add("GLOBAL_DSR_VARIANCE_NOT_COMPARABLE")
    if complete:
        inputs = {(series[common.digest(a["identity"])]["input_sha256"],
            series[common.digest(a["identity"])]["dataset"]) for a in ready}
        if len(inputs) != 1:
            reasons.add("COMPARISON_COHORT_INPUT_MISMATCH")
        else:
            vectors = {common.digest(a["identity"]): [float(Decimal(p["net_return"]))
                for p in series[common.digest(a["identity"])]["points"][1:]] for a in ready}
            result, method_checks, method_reasons = original.review_native_methods(
                vectors, selected_key, rules, cut["attempt_count"])
            result["calendar_sha256"] = common.digest([p["timestamp"]
                for p in series[common.digest(ready[0]["identity"])]["points"]])
            if global_variance_unknown:
                # A variance fitted only to this declaration cannot certify
                # global search significance. Retain the whole search count,
                # but never display the local approximation as global DSR.
                result.update(dsr=None, var_sr=None)
                method_checks["dsr"] = "INSUFFICIENT"
            metrics.update(result)
            checks.update(method_checks)
            reasons.update(method_reasons)
    else:
        reasons.add("COMPLETE_COMPARABLE_CANDIDATE_MATRIX_REQUIRED")
    if selected_key is not None and any(a["identity"] == cohort["selected_attempt"]
            and a["series_status"] == "READY" for a in attempts):
        result, economic_checks, economic_reasons = original.review_native_economics(
            series[selected_key]["points"], baseline_points, rules["economic_checks"])
        metrics.update(result)
        checks.update(economic_checks)
        reasons.update(economic_reasons)
    if rules["sample"]["end_ms"] - rules["sample"]["start_ms"] < rules["economic_checks"]["minimum_duration_ms"]:
        reasons.add("SAMPLE_TOO_SHORT")
    decision = "FAIL" if "FAIL" in checks.values() else (
        "INSUFFICIENT" if reasons or "INSUFFICIENT" in checks.values() else "PASS")
    value = dict(schema=common.GOLD_BACKTEST_REPORT_SCHEMA if _historical(policy) else
        common.SCOPED_GOLD_REPORT_SCHEMA if scoped else common.GOLD_REPORT_SCHEMA, profile=rules["profile"],
        policy_sha256=common.digest(policy), cohort_sha256=common.digest(cohort), project_cut_sha256=cut["cut_sha256"],
        study_sha256=study, as_of_ms=as_of_ms, statistical_decision=decision, reasons=sorted(reasons),
        population=dict(attempt_count=cut["attempt_count"], unique_definition_count=cut["unique_definition_count"],
            selection_attempt_count=cut["attempt_count"], mapped_attempt_count=cut["mapped_attempt_count"],
            registered_attempt_count=cut["registered_attempt_count"], unmapped_attempt_count=len(cut["unmapped"]["search_candidates"]),
            unfinished_attempt_count=len(cut["unfinished_attempts"]), failed_attempt_count=sum(
                a["status"] == "FAILED" for a in cut["attempts"])), attempts=attempts,
        selected_attempt=copy.deepcopy(cohort["selected_attempt"]), benchmark_attempt=copy.deepcopy(cohort["benchmark_attempt"]),
        series_sha256=common.digest(series), metrics=metrics, checks=checks,
        limitations=sorted({"NO_INDEPENDENT_OOS_PROOF", "NO_RESEARCH_OR_EXECUTION_AUTHORITY",
            "FORWARD_NOT_HISTORICAL_OOS", "FIXED_GRID_IS_NOT_INTRAPERIOD_RISK_EVIDENCE",
            "ACCOUNT_COSTS_NOT_CALIBRATED", "LOCAL_CLOCK_NOT_INDEPENDENTLY_VERIFIED",
            "EMBEDDED_ORIGINAL_BASELINE_IS_NOT_A_SEARCH_TRIAL", "FULL_PROJECT_HISTORY_NOT_A_SHARED_FUTURE_DATASET"}),
        qualification="NOT_ASSESSED", independent_oos_verified=False, promotion_approved=False,
        submission_enabled=False, economic_authority=False)
    if scoped:
        value["comparison"] = copy.deepcopy(comparison)
    if _historical(policy):
        value['limitations'].remove('FORWARD_NOT_HISTORICAL_OOS')
        value['limitations'].remove('FULL_PROJECT_HISTORY_NOT_A_SHARED_FUTURE_DATASET')
        value['limitations'].append('FULL_INPUT_EPISODE_INFORMATION_HORIZON')
        value['limitations'] = sorted(value['limitations'])
    if policy['schema'] in design.LONG_REVIEW_SCHEMAS:
        value.update(schema=common.LONG_GOLD_REPORT_SCHEMA,
                     computation_budget=copy.deepcopy(rules['computation_budget']))
    return common.validate_report(value)
