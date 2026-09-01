export type ConsoleModuleGroup =
  | 'DATA'
  | 'ALPHA'
  | 'VALIDATION'
  | 'PORTFOLIO_RISK'
  | 'EXECUTION'
  | 'GOVERNANCE_SYSTEM';

export interface ConsoleModuleDefinition {
  id: string;
  label: string;
  group: ConsoleModuleGroup;
  endpoint: `/api/v1/console/${string}`;
  /** Exact non-default source markers admitted for this endpoint only. */
  acceptedSources?: readonly string[];
}

/**
 * Every parameter-free GET module exposed by the current console API. Dynamic
 * detail endpoints (run_id, expression checks and delta cursors) remain linked
 * from their owning page because they require a concrete object identity.
 */
export const CONSOLE_MODULES: readonly ConsoleModuleDefinition[] = [
  { id: 'data-catalog', label: '数据目录 / PIT 数据', group: 'DATA', endpoint: '/api/v1/console/data-catalog' },
  { id: 'crypto-live', label: '加密公开市场', group: 'DATA', endpoint: '/api/v1/console/crypto-live' },
  { id: 'mexc-market', label: 'MEXC 市场', group: 'DATA', endpoint: '/api/v1/console/mexc-market' },

  {
    id: 'hourly-alpha', label: 'Alpha 小时权威', group: 'ALPHA', endpoint: '/api/v1/console/hourly-alpha',
    acceptedSources: ['HOURLY_ALPHA_SQLITE_AUTHORITIES'],
  },
  { id: 'ashare-alpha-search', label: 'A股机构因子搜索', group: 'ALPHA', endpoint: '/api/v1/console/ashare-alpha-search' },
  { id: 'ashare-forward-observation', label: 'A股未触碰前向观察', group: 'ALPHA', endpoint: '/api/v1/console/ashare-forward-observation' },
  { id: 'ashare-alpha', label: 'A股 Alpha 快照', group: 'ALPHA', endpoint: '/api/v1/console/ashare-alpha' },
  { id: 'alpha-zoo', label: 'Alpha 资产库', group: 'ALPHA', endpoint: '/api/v1/console/alpha-zoo' },
  { id: 'alpha-lifecycle', label: 'Alpha 生命周期驾驶舱', group: 'ALPHA', endpoint: '/api/v1/console/alpha-lifecycle' },
  { id: 'alpha-families', label: 'Alpha 家族', group: 'ALPHA', endpoint: '/api/v1/console/alpha-families' },
  { id: 'alpha-graph', label: 'Alpha 血缘图', group: 'ALPHA', endpoint: '/api/v1/console/alpha-graph' },
  { id: 'alpha-factory', label: 'Alpha Factory', group: 'ALPHA', endpoint: '/api/v1/console/alpha-factory' },
  { id: 'ic-panel', label: 'IC 面板', group: 'ALPHA', endpoint: '/api/v1/console/ic-panel' },
  { id: 'population-optimizer', label: '种群优化', group: 'ALPHA', endpoint: '/api/v1/console/population-optimizer' },
  { id: 'dsl-explorer', label: 'DSL 检查器', group: 'ALPHA', endpoint: '/api/v1/console/dsl-explorer' },
  { id: 'research-findings', label: '研究结论', group: 'ALPHA', endpoint: '/api/v1/console/research-findings' },
  { id: 'research-runs', label: '研究运行登记', group: 'ALPHA', endpoint: '/api/v1/console/research-runs' },

  { id: 'validation', label: '验证门', group: 'VALIDATION', endpoint: '/api/v1/console/validation' },
  { id: 'experiments', label: '实验登记', group: 'VALIDATION', endpoint: '/api/v1/console/experiments' },
  { id: 'lifecycle-monitor', label: '生命周期监控', group: 'VALIDATION', endpoint: '/api/v1/console/lifecycle-monitor' },
  { id: 'backtest-results', label: '回测结果', group: 'VALIDATION', endpoint: '/api/v1/console/backtest-results' },
  { id: 'backtest-explainability', label: '回测可解释性', group: 'VALIDATION', endpoint: '/api/v1/console/backtest-explainability' },
  { id: 'scenario-stress', label: '场景压力测试', group: 'VALIDATION', endpoint: '/api/v1/console/scenario-stress' },
  { id: 'computation-lab', label: '计算实验室', group: 'VALIDATION', endpoint: '/api/v1/console/computation-lab' },
  { id: 'fx-research', label: '外汇研究', group: 'VALIDATION', endpoint: '/api/v1/console/fx-research' },
  { id: 'lance-1m-validation', label: 'Lance 1m 验证', group: 'VALIDATION', endpoint: '/api/v1/console/lance-1m-validation' },
  { id: 'monitoring-drift', label: '漂移监控', group: 'VALIDATION', endpoint: '/api/v1/console/monitoring-drift' },
  { id: 'paper-trading', label: 'Paper 跟踪', group: 'VALIDATION', endpoint: '/api/v1/console/paper-trading' },

  { id: 'portfolio', label: '组合构建', group: 'PORTFOLIO_RISK', endpoint: '/api/v1/console/portfolio' },
  { id: 'risk-dashboard', label: '研究风险', group: 'PORTFOLIO_RISK', endpoint: '/api/v1/console/risk-dashboard' },
  { id: 'safety-primitives', label: '安全原语', group: 'PORTFOLIO_RISK', endpoint: '/api/v1/console/safety-primitives' },

  {
    id: 'mft-execution', label: 'MFT 执行读模型', group: 'EXECUTION', endpoint: '/api/v1/console/mft-execution',
    acceptedSources: ['MFT_READ_MODEL_SQLITE_AUTHORITY'],
  },
  { id: 'dry-run', label: '干跑流水线', group: 'EXECUTION', endpoint: '/api/v1/console/dry-run' },
  { id: 'carry-readiness', label: 'Carry 就绪', group: 'EXECUTION', endpoint: '/api/v1/console/carry-readiness' },
  { id: 'mexc-strategy', label: 'MEXC 策略', group: 'EXECUTION', endpoint: '/api/v1/console/mexc-strategy' },
  { id: 'mexc-readiness', label: 'MEXC 就绪', group: 'EXECUTION', endpoint: '/api/v1/console/mexc-readiness' },
  { id: 'go-live-readiness', label: '上线就绪', group: 'EXECUTION', endpoint: '/api/v1/console/go-live-readiness' },
  { id: 'live-trading', label: '实盘路线状态', group: 'EXECUTION', endpoint: '/api/v1/console/live-trading' },
  { id: 'live-degradation', label: 'Live / Replay 衰减', group: 'EXECUTION', endpoint: '/api/v1/console/live-degradation' },
  { id: 'investor-snapshot', label: '投资者快照', group: 'EXECUTION', endpoint: '/api/v1/console/investor-snapshot' },
  { id: 'gonogo', label: 'Go / No-Go', group: 'EXECUTION', endpoint: '/api/v1/console/gonogo' },

  { id: 'overview', label: '系统总览', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/overview' },
  { id: 'agents', label: '研究 Agent', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/agents' },
  { id: 'events', label: '审计事件', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/events' },
  { id: 'system-health', label: '系统健康', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/system-health' },
  { id: 'runtime-contract', label: '运行时契约', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/runtime-contract' },
  { id: 'system-completeness', label: '系统完整度', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/system-completeness' },
  { id: 'architecture', label: '系统架构', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/architecture' },
  { id: 'phase-3a-readiness', label: 'Phase 3A 就绪', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/phase-3a-readiness' },
  { id: 'evidence-ledger', label: '证据账本', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/evidence-ledger' },
  { id: 'model-risk-review', label: '模型风险评审', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/model-risk-review' },
  { id: 'promotion-control', label: '晋级控制', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/promotion-control' },
  { id: 'reviewer-workflow', label: '复核工作流', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/reviewer-workflow' },
  { id: 'access-control', label: '访问控制', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/access-control' },
  { id: 'incident-response', label: '事件响应', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/incident-response' },
  { id: 'release-committee', label: '发布委员会', group: 'GOVERNANCE_SYSTEM', endpoint: '/api/v1/console/release-committee' },
] as const;

export const CONSOLE_MODULE_PATHS = CONSOLE_MODULES.map(item => item.endpoint);

export type ConsoleModuleState =
  | 'REAL'
  | 'TECHNICAL'
  | 'DIAGNOSTIC'
  | 'SYNTHETIC'
  | 'STALE'
  | 'CORRUPT_OR_UNREADABLE'
  | 'UNAVAILABLE'
  | 'UNKNOWN';

function stringField(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === 'string' ? value.trim().toUpperCase() : '';
}

/** Separate data visibility from selection/promotion authority. */
export function classifyConsoleModule(payload: Record<string, unknown>): ConsoleModuleState {
  const provenance = stringField(payload, 'data_provenance');
  const snapshot = stringField(payload, 'snapshot_status');
  const availability = stringField(payload, 'availability_mode');
  const evidence = stringField(payload, 'evidence_status');
  const status = stringField(payload, 'status');

  // A readable transport is not the same as a usable snapshot.  Keep stale
  // and corrupt stores distinct from both a clean REAL snapshot and an
  // absent/unprovisioned backend so the module explorer does not collapse
  // operationally different faults into UNKNOWN/UNAVAILABLE.
  if ([snapshot, availability, evidence, status].includes('CORRUPT_OR_UNREADABLE')) {
    return 'CORRUPT_OR_UNREADABLE';
  }
  if ([snapshot, availability, evidence, status].includes('STALE')) return 'STALE';
  const unavailableMarkers = ['UNAVAILABLE', 'NO_SNAPSHOT', 'DATA_BLOCKED', 'INVALID_ARTIFACT'];
  if (unavailableMarkers.includes(snapshot)
    || unavailableMarkers.includes(evidence)
    || unavailableMarkers.includes(status)
    || payload.snapshot_present === false
    || provenance === 'UNAVAILABLE'
    || [
      'NOT_PROVISIONED',
      'SEARCH_EVIDENCE_NOT_PUBLISHED',
      'FORWARD_OBSERVATION_NOT_STARTED',
    ].includes(availability)
    || (status === 'BLOCKED' && (provenance === '' || provenance === 'UNAVAILABLE'))) {
    return 'UNAVAILABLE';
  }
  if (provenance.includes('SYNTHETIC')) return 'SYNTHETIC';
  if (evidence === 'TECHNICAL_VALIDATION_ONLY') return 'TECHNICAL';
  if (evidence === 'DIAGNOSTIC_ONLY' || payload.authoritative === false) return 'DIAGNOSTIC';
  if (provenance === 'REAL' || provenance.startsWith('REAL_')
    || provenance === 'REPOSITORY_EVIDENCE'
    || provenance === 'EXPERIMENT_REGISTRY'
    || provenance === 'RESEARCH_RUN_REGISTRY') return 'REAL';
  return 'UNKNOWN';
}
