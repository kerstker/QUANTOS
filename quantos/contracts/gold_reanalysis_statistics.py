"""Historical Gold study declarations; no independently locked OOS or authority."""
from quantos.contracts import research_statistical_review as common
from quantos.contracts.research_evaluation_plan import GOLD_REANALYSIS_SCHEMA, statistical_sample, validate_core

SCHEMA = 'QUANTOS_GOLD_REANALYSIS_STATISTICAL_POLICY_V1'
METHOD = 'NATIVE_GOLD_RECEIPT_AVAILABLE_EQUITY_GRID_V1'
BENCHMARK = 'GOLD_NO_ORDER_ORIGINAL_ACCOUNT_V1'


def validate(value):
    result = common._validate_statistical_policy(value, schema=SCHEMA, methods=(METHOD,))
    common.require(result['profile'] == 'ROBUST_RESEARCH_SIGNIFICANCE_V1', 'GOLD_BACKTEST_PROFILE')
    common.require(result['market_scope'] == ['XAUUSDT']
        and result['benchmark_policy']['id'] == BENCHMARK, 'GOLD_BACKTEST_SCOPE')
    return result


def benchmark(core):
    definition = dict(schema='QUANTOS_GOLD_EMBEDDED_CASH_BENCHMARK_V1', id=BENCHMARK,
        native_adapter='GOLD_BAR_FORWARD_V1', source_identity_sha256=core['source_identity_sha256'],
        dataset_manifest_sha256=core['dataset_manifest_sha256'], input_sha256=core['input_sha256'])
    return dict(id=BENCHMARK, definition_sha256=common.digest(definition), input_sha256=core['input_sha256'])


def bind_core(policy, core):
    policy = validate(policy)
    validate_core(core)
    common.require(core['schema'] == GOLD_REANALYSIS_SCHEMA
        and core['selection']['method'] == 'PREDECLARED_CANDIDATE'
        and core['selection']['validation_policy_sha256'] == common.digest(policy), 'GOLD_BACKTEST_CORE_POLICY')
    common.require(core['source_identity_sha256'] == policy['source_identity_sha256']
        and core['dataset_manifest_sha256'] == policy['dataset_manifest_sha256']
        and core['registered_at_ms'] >= policy['study_scope']['registered_at_ms']
        and statistical_sample(core) == policy['sample'], 'GOLD_BACKTEST_CORE_DATA')
    selected = [row for row in core['cohort'] if row['candidate_id'] == core['selection']['candidate_id']]
    common.require(len(selected) == 1 and selected[0]['candidate_id'] == policy['selection_policy']['candidate_id']
        and selected[0]['config_hash'] == policy['selection_policy']['definition_sha256'], 'GOLD_BACKTEST_SELECTION')
    common.require(core['benchmark'] == benchmark(core)
        and core['benchmark'] == {k:policy['benchmark_policy'][k] for k in core['benchmark']}, 'GOLD_BACKTEST_BENCHMARK')
    common.require(policy['split_policy']['maximum_information_horizon_ms'] >=
        core['sample']['end_ms']-core['sample']['start_ms']+core['sample']['frequency_ms'],
        'GOLD_BACKTEST_INFORMATION_HORIZON')
    return policy
