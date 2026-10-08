import {it, expect} from 'vitest';
import {render, screen} from '@testing-library/react';
import {GoldBarResults} from './GoldBarResults';
import type {GoldBarReport, GoldProjectStatisticalReport} from '@/lib/types/research';

function statisticalFixture(): GoldBarReport {
  const series = {status:'INSUFFICIENT' as const, reasons:['INSUFFICIENT_DURATION','FULL_RUN_TERMINAL_NOT_COVERED'],
    native_result_sha256:'a'.repeat(64), available_history_sha256:'b'.repeat(64), full_run_covered:false,
    grid:{schema_version:'PERPETUAL_FIXED_FREQUENCY_EQUITY_V1' as const,
      calendar:'ELAPSED_UTC_MILLISECONDS_365_DAY_YEAR' as const, frequency_ms:10000, anchor_ms:0,
      points:[{timestamp:0,equity:'1000',net_return:null},{timestamp:10000,equity:'999.2',net_return:'-0.0008'}],
      excluded_tail_ms:0,annualized_sharpe:null,statistics_status:'INSUFFICIENT_DURATION',
      external_cash_flows:'UNSUPPORTED_NO_EXTERNAL_FLOWS_IN_SIMULATION' as const},
    metrics:{initial_equity:'1000',ending_equity:'999',net_equity_change:'-1',fees:'0.2',funding:'-0.8',
      reconciliation_residual:'0',max_observed_drawdown:'0.001',frequency_ms:10000,sample_count:1,
      annualized_sharpe:null,statistics_status:'INSUFFICIENT_DURATION'}};
  const baseline = {...series, metrics:{...series.metrics,ending_equity:'1000',net_equity_change:'0',fees:'0',funding:'0'},
    grid:{...series.grid,points:[{timestamp:0,equity:'1000',net_return:null},{timestamp:10000,equity:'1000',net_return:'0'}]}};
  const report: GoldBarReport = {candidates:[],trial_ids:['d','e','f'],fee_rate:'.001',
    fee_policy:'EXPLICIT_UNCALIBRATED_SIMULATION_RATE',economic_eligible:false,authoritative:false,submission_enabled:false,
    native_statistics:{schema:'QUANTOS_GOLD_NATIVE_STATISTICS_V1',status:'TECHNICAL_VALIDATION_ONLY',evidence_kind:'FORWARD',
      method:'NATIVE_GOLD_RECEIPT_AVAILABLE_EQUITY_GRID_V1',native_invocation_id:'a'.repeat(64),plan_sha256:'b'.repeat(64),
      input_sha256:'c'.repeat(64),source_identity_sha256:'d'.repeat(64),declaration_sha256:'e'.repeat(64),
      dataset_manifest_sha256:'f'.repeat(64),original_record_sha256:'1'.repeat(64),project_cut_sha256:'2'.repeat(64),
      projection_sha256:'3'.repeat(64),sample:{start_ms:0,end_ms:10000,frequency_ms:10000},
      attempts: ['channel','trend','reversion'].map((candidate_id,i)=>({...series,
        candidate_id:candidate_id as 'channel'|'trend'|'reversion',native_trial_id:['d','e','f'][i],config_hash:String(i)})),
      benchmark:{...baseline,id:'GOLD_NO_ORDER_ORIGINAL_ACCOUNT_V1'},statistical_review:'NOT_IMPLEMENTED',
      population:{attempt_count:4,unique_definition_count:3,coverage:'COMPLETE',unfinished_runs:['pending'],
        all_attempts:[{invocation_id:'previous',native_trial_id:'failed-trial',config_hash:'failed-config',status:'FAILED'}]},
      limitations:['NO_RESEARCH_OR_EXECUTION_AUTHORITY'],economic_authority:false,promotion_approved:false,submission_enabled:false}};
  return report;
}

it('shows the receipt available grid and original cash benchmark without statistical approval', () => {
  render(<GoldBarResults report={statisticalFixture()}/>);
  expect(screen.getByRole('img',{name:/黄金定频权益与现金基准/})).toBeInTheDocument();
  expect(screen.getAllByText('无订单现金基准').length).toBeGreaterThan(0);
  expect(screen.getByText(/统计点按原收据可知时间取样/)).toBeInTheDocument();
  expect(screen.getByText(/本次网格未覆盖完整运行终态/)).toBeInTheDocument();
  expect(screen.getByText(/样本不足，统计评审尚未完成/)).toBeInTheDocument();
  expect(screen.getByText('failed-trial')).toBeInTheDocument();
  expect(screen.getByText('FAILED')).toBeInTheDocument();
  expect(screen.queryByText('统计通过')).not.toBeInTheDocument();
});

function reviewedStatisticalFixture(): GoldBarReport {
  const report = statisticalFixture();
  const value = report.native_statistics!;
  const review: GoldProjectStatisticalReport = {
    schema:'QUANTOS_GOLD_PROJECT_STATISTICAL_REPORT_V1',profile:'ROBUST_RESEARCH_SIGNIFICANCE_V1',
    statistical_decision:'FAIL',reasons:['SAMPLE_TOO_SHORT','FULL_RUN_TERMINAL_NOT_COVERED'],limitations:[],
    policy_sha256:value.declaration_sha256,cohort_sha256:'4'.repeat(64),project_cut_sha256:value.project_cut_sha256,
    study_sha256:'5'.repeat(64),series_sha256:'6'.repeat(64),as_of_ms:20000,
    population:{attempt_count:3,unique_definition_count:3,selection_attempt_count:3,mapped_attempt_count:3,
      registered_attempt_count:3,unmapped_attempt_count:0,unfinished_attempt_count:0,failed_attempt_count:0},
    attempts:value.attempts.map(a=>({identity:{invocation_id:value.native_invocation_id,native_trial_id:a.native_trial_id,
      search_id:'gold-study',provenance_trial_id:'gold-study:'+a.native_trial_id,config_hash:a.config_hash},
      native_status:'PASSED',series_status:'READY',series_sha256:'9'.repeat(64),policy_bound:true})),selected_attempt:null,
    benchmark_attempt:{kind:'ORIGINAL_EMBEDDED_CASH_ACCOUNT',id:'GOLD_NO_ORDER_ORIGINAL_ACCOUNT_V1',
      invocation_id:value.native_invocation_id,original_record_sha256:value.original_record_sha256,
      native_result_sha256:value.benchmark!.native_result_sha256},
    metrics:{dsr:null,observed_sr:null,var_sr:null,n_trials:3,cpcv:null,cpcv_thresholds:null,pbo:0.5,
      matrix_sha256:'7'.repeat(64),calendar_sha256:'8'.repeat(64),skew:null,kurtosis:null,
      sample_net_return:'-0.0008',benchmark_net_return:'0',benchmark_excess:'-0.0008',
      maximum_drawdown:'0.0008',selected_cash:null},
    checks:{dsr:'INSUFFICIENT',cpcv_positive:'INSUFFICIENT',cpcv_tail:'INSUFFICIENT',pbo:'FAIL',
      net_return:'FAIL',benchmark_excess:'FAIL',drawdown:'PASS'},qualification:'NOT_ASSESSED',
    independent_oos_verified:false,economic_authority:false,promotion_approved:false,submission_enabled:false};
  review.selected_attempt = review.attempts[0].identity;
  report.native_statistics = {...value,schema:'QUANTOS_GOLD_NATIVE_STATISTICS_V2',statistical_review:'COMPLETED',review,
    population:{attempt_count:3,unique_definition_count:3,coverage:'COMPLETE',unfinished_runs:[],
      all_attempts:value.attempts.map(a=>({invocation_id:value.native_invocation_id,native_trial_id:a.native_trial_id,
        config_hash:a.config_hash,status:'PASSED'}))}};
  return report;
}

it('shows a completed failed statistical review while preserving independent qualification', () => {
  const report = reviewedStatisticalFixture();
  render(<GoldBarResults report={report}/>);
  expect(screen.getByRole('region',{name:'黄金统计检验结果'})).toBeInTheDocument();
  expect(screen.getByText(/统计检验已执行，结论：未通过/)).toBeInTheDocument();
  expect(screen.getByText('样本未达到预先固定的30天要求')).toBeInTheDocument();
  expect(screen.getByText('0.5')).toBeInTheDocument();
  expect(screen.getByText(/独立风险、成本校准和锁定 OOS 仍待核验/)).toBeInTheDocument();
  expect(screen.queryByText('统计评审尚未完成')).not.toBeInTheDocument();
});

it('shows the predeclared comparison separately from retained global search attempts', () => {
  const report = reviewedStatisticalFixture();
  const value = report.native_statistics!;
  if (value.statistical_review !== 'COMPLETED') throw new Error('review fixture');
  const included = value.review.attempts.map(a=>a.identity);
  const excluded = included.map(a=>({...a,invocation_id:'previous',native_trial_id:'previous-'+a.native_trial_id}));
  report.native_statistics = {...value,review:{...value.review,schema:'QUANTOS_GOLD_PROJECT_STATISTICAL_REPORT_V2',
    comparison:{schema:'QUANTOS_GOLD_DECLARED_COMPARISON_V1',declaration_sha256:value.declaration_sha256,
      declared_candidate_count:3,included_attempts:included,excluded_attempts:excluded},
    attempts:[...value.review.attempts,...excluded.map(identity=>({identity,native_status:'FAILED',
      series_status:'OUTSIDE_COMPARISON_SCOPE' as const,series_sha256:'a'.repeat(64),policy_bound:false}))],
    population:{...value.review.population,attempt_count:6,selection_attempt_count:6,mapped_attempt_count:6,
      registered_attempt_count:6,failed_attempt_count:3},metrics:{...value.review.metrics,n_trials:6},
    reasons:[...value.review.reasons,'GLOBAL_DSR_VARIANCE_NOT_COMPARABLE']},
    population:{...value.population,attempt_count:6,all_attempts:[...value.population.all_attempts,
      ...excluded.map(a=>({invocation_id:a.invocation_id,native_trial_id:a.native_trial_id,
        config_hash:a.config_hash,status:'FAILED'}))]}};
  render(<GoldBarResults report={report}/>);
  expect(screen.getByText(/本次观察前固定的比较集含 3 次原尝试/)).toBeInTheDocument();
  expect(screen.getByText(/其余 3 次保留于完整历史/)).toBeInTheDocument();
  expect(screen.getByText(/检验保留全部 6 次尝试/)).toBeInTheDocument();
  expect(screen.getByText('跨窗口搜索尚缺可比较的全局显著性证据')).toBeInTheDocument();
  expect(screen.getByText('previous-d')).toBeInTheDocument();
});

it.each(['QUANTOS_REGISTERED_GOLD_COLLECTION_V2', 'QUANTOS_REGISTERED_GOLD_COLLECTION_V4'] as const)(
  'shows declaration %s while leaving independent timing and OOS unknown', (schema) => {
  const report: GoldBarReport = {candidates: [], trial_ids: ['d'.repeat(64),'e'.repeat(64),'f'.repeat(64)],
    fee_rate: '.001', fee_policy: 'EXPLICIT_UNCALIBRATED_SIMULATION_RATE',
    economic_eligible: false, authoritative: false, submission_enabled: false,
    native_registration: {schema, native_invocation_id:'a'.repeat(64),
      plan_sha256:'b'.repeat(64), input_sha256:'c'.repeat(64), registered_at_ms:2000000,
      attempt_count:3, native_trial_ids:['d'.repeat(64),'e'.repeat(64),'f'.repeat(64)],
      evaluation_plan:'WINDOW_DECLARED_UNVERIFIED', statistical_review:schema === 'QUANTOS_REGISTERED_GOLD_COLLECTION_V4'
        ? 'STATISTICAL_POLICY_DECLARED' : 'FORWARD_ADAPTER_UNAVAILABLE',
      risk_review:'NOT_INDEPENDENTLY_REPLAYED', locked_oos:'UNKNOWN', candidate_admission:'NOT_APPROVED',
      forward_declaration:{design_sha256:'9'.repeat(64), declared_at_ms:1, observation_start_ms:900000,
        closed_bars:[1800000], time_evidence:'LOCAL_CLOCK_NOT_INDEPENDENTLY_VERIFIED'},
      economic_authority:false, promotion_approved:false, submission_enabled:false}};
  render(<GoldBarResults report={report}/>);
  for (const text of ['观察窗口前已固定评估声明','声明时间尚未独立核验','锁定 OOS 未知','候选未准入','9'.repeat(64)]) {
    expect(screen.getByText(text)).toBeInTheDocument();
  }
  expect(screen.queryByText('评估计划未预登记')).not.toBeInTheDocument();
});

it('separates original trial registration from missing evaluation and approval', () => {
  const report: GoldBarReport = {candidates: [], trial_ids: ['d'.repeat(64),'e'.repeat(64),'f'.repeat(64)],
    fee_rate: '.001', fee_policy: 'EXPLICIT_UNCALIBRATED_SIMULATION_RATE',
    economic_eligible: false, authoritative: false, submission_enabled: false,
    native_registration: {schema:'QUANTOS_REGISTERED_GOLD_COLLECTION_V1', native_invocation_id:'a'.repeat(64),
      plan_sha256:'b'.repeat(64), input_sha256:'c'.repeat(64), registered_at_ms:1,
      attempt_count:3, native_trial_ids:['d'.repeat(64),'e'.repeat(64),'f'.repeat(64)],
      evaluation_plan:'NOT_PREREGISTERED', statistical_review:'FORWARD_ADAPTER_UNAVAILABLE',
      risk_review:'NOT_INDEPENDENTLY_REPLAYED', locked_oos:'UNKNOWN', candidate_admission:'NOT_APPROVED',
      economic_authority:false, promotion_approved:false, submission_enabled:false}};
  render(<GoldBarResults report={report}/>);
  expect(screen.getByRole('region',{name:'原试验登记与评估状态'})).toBeInTheDocument();
  expect(screen.getByText('a'.repeat(64))).toBeInTheDocument();
  for (const label of ['评估计划未预登记','前向统计适配尚未接入','风险尚未独立复算','锁定 OOS 未知','候选未准入']) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
  expect(screen.getByText(/计算前登记了 3 次原试验/)).toBeInTheDocument();
});

it('distinguishes no signal, no fill and an open position without presenting profit', () => {
  const report: GoldBarReport = {
    candidates: [
      {candidate: 'channel', outcome: 'NO_SIGNAL', input_ready: true, order_count: 0, filled_order_count: 0, open_position_count: 0},
      {candidate: 'trend', outcome: 'NO_FILL', input_ready: true, order_count: 1, filled_order_count: 0, open_position_count: 0},
      {candidate: 'reversion', outcome: 'OPEN_AT_END', input_ready: true, order_count: 2, filled_order_count: 1, open_position_count: 1},
    ], trial_ids: ['a', 'b', 'c'], fee_rate: '0.0005', fee_policy: 'EXPLICIT_UNCALIBRATED_SIMULATION_RATE',
    economic_eligible: false, authoritative: false, submission_enabled: false,
  };
  render(<GoldBarResults report={report}/>);
  for (const label of ['通道突破', '均线交叉', '均值回归', '没有入场信号', '有交易意图，未成交', '仍有仓位或挂单']) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
  expect(screen.getByText(/尚未按账户校准/)).toBeInTheDocument();
  expect(screen.queryByText('净利润')).not.toBeInTheDocument();
  expect(screen.getAllByText('未知')).toHaveLength(9);
});

it('shows reconciled costs and floating losses without adding independent accounts', () => {
  const report: GoldBarReport = {
    candidates: [{candidate: 'channel', outcome: 'OPEN_AT_END', input_ready: true,
      order_count: 1, filled_order_count: 1, open_position_count: 1,
      account: {currency: 'USDT', initial_equity: '1000', ending_equity: '999.89182273',
        net_equity_change: '-0.10817727', fees: '0.0418866', funding: '0',
        max_observed_drawdown: '0.0123', reconciliation_residual: '0',
        valuation: 'MARK_TO_MARKET_INCLUDES_OPEN_POSITIONS'}}],
    trial_ids: ['a'], fee_rate: '0.0005', fee_policy: 'EXPLICIT_UNCALIBRATED_SIMULATION_RATE',
    economic_eligible: false, authoritative: false, submission_enabled: false,
  };
  render(<GoldBarResults report={report}/>);
  expect(screen.getByText('-0.10817727')).toBeInTheDocument();
  expect(screen.getByText('0.0418866')).toBeInTheDocument();
  expect(screen.getByText('1.23%')).toBeInTheDocument();
  expect(screen.getByText(/不能相加作为组合收益/)).toBeInTheDocument();
  expect(screen.getByText(/尚未按账户校准/)).toBeInTheDocument();
  expect(screen.queryByText('净利润')).not.toBeInTheDocument();
});

it.each(['STOPPED_FLAT', 'STOPPING_UNRESOLVED'] as const)('distinguishes the loss trigger from exit completion: %s', status => {
  const report: GoldBarReport = {
    candidates: [{candidate: 'channel', outcome: status === 'STOPPED_FLAT' ? 'FLAT_TERMINAL' : 'OPEN_AT_END',
      input_ready: true, order_count: 2, filled_order_count: status === 'STOPPED_FLAT' ? 2 : 1,
      open_position_count: status === 'STOPPED_FLAT' ? 0 : 1,
      loss_control: {schema: 'GOLD_RESEARCH_LOSS_CONTROL_V1', status,
        policy: {version: 'GOLD_RESEARCH_INITIAL_EQUITY_LOSS_V1', initial_equity: '1000',
          max_loss_fraction: '0.05', scope: 'ONE_SIMULATED_ACCOUNT_PER_TAPE',
          trigger_basis: 'MARKED_EQUITY_AFTER_ACTUAL_LEDGER_EVENTS', max_quote_age_ms: 30000},
        trigger: {timestamp: 1000, sequence: 2, equity: '950', reason: 'CUMULATIVE_LOSS_LIMIT'},
        exit_attempts: 1, economic_eligible: false, submission_enabled: false}}],
    trial_ids: ['a'], fee_rate: '0.0005', fee_policy: 'EXPLICIT_UNCALIBRATED_SIMULATION_RATE',
    economic_eligible: false, authoritative: false, submission_enabled: false,
  };
  render(<GoldBarResults report={report}/>);
  expect(screen.getByText(status === 'STOPPED_FLAT' ? '已触发5%停机，期末空仓' : '已触发5%停机，退出未完成')).toBeInTheDocument();
  expect(screen.getByText(/退出费用和行情跳空仍可能扩大亏损/)).toBeInTheDocument();
});

it.each([true, false])('shows shared costs and available or unknown target diagnostics: %s', withDiagnostics => {
  const report: GoldBarReport = {candidates: [], trial_ids: ['a','b','c','portfolio-trial'],
    fee_rate: '0.0005', fee_policy: 'EXPLICIT_UNCALIBRATED_SIMULATION_RATE',
    economic_eligible: false, authoritative: false, submission_enabled: false,
    portfolio: {trial_id:'portfolio-trial', result_hash:'h', order_count:2, filled_order_count:2,
      open_position_count:0, construction:{version:'GOLD_FIXED_NET_TARGET_PORTFOLIO_V1',
        quantities:{channel:'.01',trend:'.01',reversion:'.01'},max_notional:'150',separate_strategy_pnl_computed:false},
      account:{currency:'USDT',initial_equity:'1000',ending_equity:'949',net_equity_change:'-51',
        fees:'1',funding:'-50',max_observed_drawdown:'0.051',reconciliation_residual:'0',
        valuation:'MARK_TO_MARKET_INCLUDES_OPEN_POSITIONS'},
      loss_control:{schema:'GOLD_RESEARCH_LOSS_CONTROL_V1',status:'STOPPED_FLAT',
        policy:{version:'GOLD_RESEARCH_INITIAL_EQUITY_LOSS_V1',initial_equity:'1000',max_loss_fraction:'0.05',
          scope:'ONE_SIMULATED_ACCOUNT_PER_TAPE',trigger_basis:'MARKED_EQUITY_AFTER_ACTUAL_LEDGER_EVENTS',max_quote_age_ms:30000},
        trigger:{timestamp:1000,sequence:2,equity:'950',reason:'CUMULATIVE_LOSS_LIMIT'},exit_attempts:1,
        economic_eligible:false,submission_enabled:false},economic_eligible:false,submission_enabled:false}};
  if (withDiagnostics && report.portfolio) report.portfolio.target_diagnostics = {
    schema:'GOLD_TARGET_NETTING_DIAGNOSTIC_V1', measure:'STRATEGY_TARGET_OBSERVATIONS_NOT_TRADES',
    observation_count:30, all_zero_count:10, opposed_zero_count:15, opposed_nonzero_count:3,
    unopposed_nonzero_count:2, economic_eligible:false};
  render(<GoldBarResults report={report}/>);
  if (withDiagnostics) {
    expect(screen.getByText('相反目标完全抵消')).toBeInTheDocument();
    expect(screen.getByText('15')).toBeInTheDocument();
    expect(screen.getByText(/以下计数不是交易次数或时间占比/)).toBeInTheDocument();
  } else expect(screen.getByText('旧记录：组合目标诊断未知')).toBeInTheDocument();
  expect(screen.getByRole('region',{name:'共享资金组合实验'})).toBeInTheDocument();
  expect(screen.getByText('-51')).toBeInTheDocument();
  expect(screen.getByText('1 / -50')).toBeInTheDocument();
  expect(screen.getByText(/portfolio-trial/)).toBeInTheDocument();
});
