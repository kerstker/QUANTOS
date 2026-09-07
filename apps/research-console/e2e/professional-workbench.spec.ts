import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';

import { MFT_CONSOLE_SCHEMA, type MftDelta, type MftSnapshot } from '../src/workbench/contracts';
import { makeMftSnapshot } from '../src/workbench/__tests__/mft-fixtures';
import { installStudioApi } from './studio-fixtures';
import { canonicalMftJson, stableMftHash } from '../src/workbench/mft-reducer';

const OBSERVED_AT = 1_786_122_000_000;
const HASH_A = 'a'.repeat(64);
const HASH_B = 'b'.repeat(64);

const runtimeContract = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REPOSITORY_EVIDENCE', contract_version: 'console-runtime.v1',
  service: 'research-console-api', instance_id: 'wave3-acceptance', process_id: 1,
  build_sha: '4a990ea000000000000000000000000000000000', build_sha_known: true,
  event_schema_catalog: {
    sha256: 'c'.repeat(64), event_type_count: 1,
    latest_versions: { 'quantos.audit': 'v1' },
  },
  safety_boundary: {
    read_only_console: true, authorized_level: 'L2_DRY_RUN_ONLY',
    live_trading_authorized: false, order_submission_allowed: false,
    broker_connectivity: false,
  },
  generated_at_ms: OBSERVED_AT,
};

function hourlyAlpha(overrides: Record<string, unknown> = {}) {
  return {
    source: 'HOURLY_ALPHA_SQLITE_AUTHORITIES', read_only: true, api_version: 'v1',
    data_provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES', snapshot_id: HASH_A,
    snapshot_ids: [HASH_A], snapshot_status: 'REAL',
    data_quality: {
      status: 'REAL', provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES', snapshot_ids: [HASH_A],
    },
    schema_version: 'quantos.hourly-alpha-console.v1', as_of: 1_786_122_000,
    status: 'AVAILABLE', freshness: { status: 'CURRENT', age_seconds: 20 },
    watermark: { event_at: 1_786_121_980, batch_id: HASH_A },
    batch_funnel: {
      generated: 3, representatives: 2, validated: 1, blocked: 0,
      forward_decisions: 1, forward_realizations: 0,
    },
    candidate_states: [
      { ordinal: 0, candidate_id: HASH_A, state: 'VALIDATED', trial_id: HASH_B, artifact_commitment: HASH_A },
      { ordinal: 1, candidate_id: HASH_B, state: 'QUEUED', trial_id: null, artifact_commitment: null },
    ],
    stores: {
      workflow: { status: 'AVAILABLE' }, trials: { status: 'AVAILABLE', trials: 1 },
      forward: { status: 'AVAILABLE' },
    },
    safety: {
      read_only: true, readiness: 'L2_DRY_RUN_ONLY', allow_order_submission: false,
      allow_ai_direct_trading: false, require_human_approval: true,
    },
    ...overrides,
  };
}

function noHourlySnapshot() {
  return hourlyAlpha({
    snapshot_id: null, snapshot_ids: [], snapshot_status: 'NO_SNAPSHOT',
    data_quality: {
      status: 'NO_SNAPSHOT', provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES', snapshot_ids: [],
    },
    freshness: { status: 'NO_RUN', age_seconds: null }, watermark: null,
    batch_funnel: {
      generated: 0, representatives: 0, validated: 0, blocked: 0,
      forward_decisions: 0, forward_realizations: 0,
    },
    candidate_states: [],
  });
}

const validationEvidence = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REAL_CRYPTO_SERIES',
  selected_report: {
    run_id: 'run-wave3', alpha_id: HASH_A, decision: 'APPROVED',
    gates_passed: 5, gates_total: 6, reasons: [], leakage_pass: true,
    walk_forward_pass: true, multiple_testing_pass: true, costs_pass: true,
    capacity_pass: true, decay_pass: false, oos_sharpe: 1.1, oos_ic: 0.03,
    net_sharpe: 0.8,
  },
  reports: [] as Array<Record<string, unknown>>, lineage: [],
};
validationEvidence.reports = [validationEvidence.selected_report];

const experiment = {
  experiment_id: 'exp-wave3', alpha_id: HASH_A, expression: 'carry',
  owner_agent: 'ResearchAgentLoop', stage: 'VALIDATION', decision: 'APPROVED',
  created_at: 1_786_121_900, execution_mode: 'L2_DRY_RUN_ONLY',
  config: {
    universe: 'CRYPTO', horizon_days: 1, cost_bps: 10, seed: 0,
    data_snapshot_id: 'dataset-wave3', config_hash: HASH_A,
  },
  metrics: {
    ic: 0.03, rank_ic: 0.03, sharpe: 0.8, turnover: 0.2,
    capacity_estimate: 12_000_000, composite_score: 0.7,
  },
  reproducibility_hash: HASH_B,
  artifacts: [{
    artifact_id: 'artifact-wave3', kind: 'EVALUATION', status: 'SEALED',
    uri: 'experiment://exp-wave3', content_hash: HASH_A,
  }],
  lineage: { upstream: ['factor_engine'], downstream: ['validation'] },
};

const experimentsEvidence = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'EXPERIMENT_REGISTRY', registry_mode: 'READ_ONLY_RESEARCH_REGISTRY',
  summary: {
    total_experiments: 1, approved: 1, rejected: 0, needs_more_data: 0,
    artifact_count: 1, reproducible: 1,
  },
  experiments: [experiment], stage_counts: { VALIDATION: 1 },
  lineage_policy: {
    requires_config_hash: true, requires_data_snapshot: true,
    requires_metric_artifact: true, mutable_records: false,
  },
};

const alphaZoo = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'EXPERIMENT_REGISTRY',
  rows: [{
    alpha_id: HASH_A, name: 'carry', generator: 'FactorGarden', status: 'VALIDATED',
    cluster_id: 'carry-family', tags: ['hourly'], net_sharpe: 0.8,
    deflated_sharpe: 0.7, ic: 0.03, icir: 0.8, paper_ic: 0.02,
    paper_sharpe: 0.6, n_paper_periods: 8,
  }],
  dimensions: [],
};

const alphaLifecycle = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REAL_ALPHA_LIFECYCLE', snapshot_present: true,
  artifact: 'ALPHA_LIFECYCLE_COCKPIT', generated_at_ms: OBSERVED_AT,
  readiness: 'L2_DRY_RUN_ONLY',
  alphas: [{
    alpha_id: HASH_A, display_name: 'carry', derived_stage: 'VALIDATED',
    lifecycle_state: 'VALIDATED', stages: {}, warnings: [],
  }],
  stage_legend: {}, honesty_note: 'Read-only lifecycle fixture bound to the acceptance input.',
};

const portfolio = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REAL_CRYPTO_SERIES', snapshot_status: 'REAL',
  simulation: {
    run_id: 'sim-wave3', status: 'COMPLETED', metrics: { sharpe: 1.2, total_return: 3 },
    equity_curve: [{ day: 0, equity: 100 }, { day: 1, equity: 103 }],
  },
  weights: {
    weights_id: 'weights-wave3', weights: { 'alpha-1': 0.4, 'alpha-2': 0.6 },
    gross_exposure: 1, expected_sharpe: 1.2, expected_ic: 0.03,
    method: 'RISK_PARITY', optimizer_real: true,
  },
  risk_contributions: [
    { alpha_id: 'alpha-1', weight: 0.4, annualized_volatility: 0.2, contribution: 0.5 },
    { alpha_id: 'alpha-2', weight: 0.6, annualized_volatility: 0.1, contribution: 0.5 },
  ],
  return_attribution: [
    { alpha_id: 'alpha-1', weight: 0.4, alpha_total_return: 5, contribution_return: 2 },
    { alpha_id: 'alpha-2', weight: 0.6, alpha_total_return: 1.6667, contribution_return: 1 },
  ],
  analytics_method: 'BACKEND_RESEARCH_SIMULATION_ATTRIBUTION', lineage: [],
};

const riskReport = {
  run_id: 'risk-wave3', portfolio_id: 'portfolio-alpha-1', decision: 'RISK_REJECTED',
  gates_passed: 1, gates_total: 9, reasons: ['FULL_INPUTS_REQUIRED'],
  gate_results: { market: true, concentration: false }, reproducibility_hash: HASH_A,
};

const risk = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REAL_CRYPTO_SERIES', snapshot_status: 'REAL', selected_alpha_id: 'alpha-1',
  alpha: {
    candidate_id: 'alpha-1', expression: 'carry', ic: 0.03, rank_ic: 0.03,
    icir: 0.8, sharpe: 1.2, turnover: 0.2, decay_half_life: 12,
    capacity_estimate: 12_000_000, correlation_penalty: 0.1,
    composite_score: 0.7, lifecycle_state: 'VALIDATED',
  },
  primary_report: riskReport, reports: [riskReport], lineage: [],
};

const auditEvents = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REPOSITORY_EVIDENCE',
  events: [{
    event_id: 'event-wave3', event_type: 'quantos.audit', timestamp: OBSERVED_AT,
    source: 'acceptance', payload: { read_only: true },
  }],
  event_bus_health: {
    schema_version: 1, verdict: 'PASS', checked_at_ms: OBSERVED_AT,
    group_id: 'console', consumer_mode: 'READ_ONLY',
    integrity: {
      event_chain_valid: true, consumer_chain_valid: true, coordination_chain_valid: true,
    },
    active_workers: [], partition_count: 1, pending_events: 0,
    oldest_pending_age_ms: 0, dead_letter_count: 0, event_records: 1,
    consumer_journal_records: 1, coordination_journal_records: 1,
    allocated_bytes: 1, retention_mode: 'BOUNDED', alerts: [],
  },
};

const systemHealth = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REPOSITORY_EVIDENCE',
  services: [{ service: 'research-console-api', status: 'healthy', latency_ms: 2, error_rate: 0 }],
  gates: [], safety_checks: ['L2_DRY_RUN_ONLY', 'ORDER_SUBMISSION_BLOCKED'],
};

function operationalMft(overrides: Record<string, unknown> = {}): MftSnapshot {
  return makeMftSnapshot({
    as_of_ms: OBSERVED_AT,
    freshness: {
      status: 'CURRENT', age_ms: 500, policy_id: 'mft-console-freshness',
      policy_version: '1', max_age_ms: 7_200_000,
    },
    watermark: { sequence: 0, event_at_ms: OBSERVED_AT - 500, event_hash: '0'.repeat(64) },
    market_gap: {
      status: 'CONTIGUOUS', venue: 'BINANCE_TESTNET', account: 'PAPER',
      latest_closed_bar_ms: OBSERVED_AT - 60_000, missing_intervals: [],
    },
    scheduler: {
      status: 'LEADER', task_name: 'MFT_HOURLY', recovery_status: 'READY',
      last_success_ms: OBSERVED_AT - 60_000, next_rebalance_at_ms: OBSERVED_AT + 3_540_000,
    },
    intent_funnel: {
      status: 'READY', received: 1, validated: 1, risk_allowed: 1, blocked: 0, completed: 1,
    },
    oms: {
      status: 'READY', orders: [{
        client_order_id: 'paper-order-1', instrument_id: 'BTCUSDT', side: 'BUY',
        canonical_state: 'FILLED', order_quantity: 0.01, cumulative_filled_qty: 0.01,
      }],
    },
    fills: {
      status: 'READY', count: 1, items: [{
        fill_id: 'fill-1', client_order_id: 'paper-order-1', instrument_id: 'BTCUSDT',
        quantity: 0.01, price: 60_000,
      }],
    },
    positions: {
      status: 'READY', position_count: 1, items: [{ instrument: 'BTCUSDT', quantity: 0.01 }],
    },
    risk: {
      status: 'ALLOWED_TESTNET', kill_switch_latched: false, day_pnl: 25,
      equity: 100_025, gross_exposure: 600, drawdown: 0.001,
    },
    reconciliation: { status: 'IN_SYNC', requires_manual_review: false },
    tca: {
      status: 'READY', fill_ratio: 1, slippage_bps: 0.8, report_id: 'tca-wave3',
      microstructure_scope: 'TESTNET_MICROSTRUCTURE_NOT_PRODUCTION',
    },
    mandate_expiry: { status: 'VALID_TESTNET', expires_at_ms: OBSERVED_AT + 3_600_000 },
    ...overrides,
  });
}

function metadata(snapshot: MftSnapshot): Record<string, unknown> {
  const copy = JSON.parse(JSON.stringify(snapshot)) as Record<string, unknown>;
  for (const section of [
    'market_gap', 'scheduler', 'intent_funnel', 'oms', 'fills', 'positions',
    'risk', 'reconciliation', 'tca', 'mandate_expiry',
  ]) delete copy[section];
  return copy;
}

function emptyDelta(snapshot: MftSnapshot): MftDelta {
  return {
    source: 'MFT_READ_MODEL_SQLITE_AUTHORITY', read_only: true, api_version: 'v1',
    data_provenance: 'REAL_MFT_READ_MODEL', schema_version: MFT_CONSOLE_SCHEMA,
    status: 'OK', from_cursor: snapshot.cursor as string, cursor: snapshot.cursor as string,
    as_of_ms: snapshot.as_of_ms, events: [], has_more: false,
    result_metadata: metadata(snapshot),
  };
}

async function nextOrderSnapshot(prior: MftSnapshot): Promise<{ delta: MftDelta; snapshot: MftSnapshot }> {
  const previousHash = prior.watermark?.event_hash ?? '0'.repeat(64);
  const sequence = (prior.watermark?.sequence ?? 0) + 1;
  const payload = {
    status: 'READY', orders: [{
      client_order_id: 'paper-order-1', instrument_id: 'BTCUSDT', side: 'BUY',
      canonical_state: 'FILLED', order_quantity: 0.01, cumulative_filled_qty: 0.01,
    }],
  };
  const payloadHash = await stableMftHash(payload);
  const eventBase = {
    sequence, event_id: 'oms.order.completed', section: 'oms' as const,
    event_at_ms: OBSERVED_AT, payload, payload_hash_input: canonicalMftJson(payload),
    payload_hash: payloadHash, prev_hash: previousHash,
  };
  const event = {
    ...eventBase,
    event_hash: await stableMftHash({
      sequence: eventBase.sequence, event_id: eventBase.event_id, section: eventBase.section,
      event_at_ms: eventBase.event_at_ms, payload_hash: eventBase.payload_hash,
      prev_hash: eventBase.prev_hash,
    }),
  };
  const cursor = `${sequence}:${event.event_hash}`;
  const snapshot = operationalMft({
    oms: payload, snapshot_id: event.event_hash, snapshot_ids: [event.event_hash],
    data_quality: {
      status: 'REAL', provenance: 'REAL_MFT_READ_MODEL', snapshot_ids: [event.event_hash],
    },
    watermark: { sequence, event_at_ms: OBSERVED_AT, event_hash: event.event_hash }, cursor,
  });
  return {
    snapshot,
    delta: {
      source: 'MFT_READ_MODEL_SQLITE_AUTHORITY', read_only: true, api_version: 'v1',
      data_provenance: 'REAL_MFT_READ_MODEL', schema_version: MFT_CONSOLE_SCHEMA,
      status: 'OK', from_cursor: prior.cursor as string, cursor, as_of_ms: snapshot.as_of_ms,
      events: [event], has_more: false, result_metadata: metadata(snapshot),
    },
  };
}

interface ApiOptions {
  disableEventSource?: boolean;
  hourly?: Record<string, unknown>;
  snapshot?: unknown;
  snapshotHandler?: (route: Route) => Promise<void> | void;
  deltaHandler?: (route: Route) => Promise<void> | void;
  streamHandler?: (route: Route) => Promise<void> | void;
}

async function fulfill(route: Route, json: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(json) });
}

async function installReadOnlyApi(page: Page, options: ApiOptions = {}) {
  await installStudioApi(page);
  await page.addInitScript(({ now, disableEventSource }) => {
    Date.now = () => now;
    if (disableEventSource) {
      Object.defineProperty(window, 'EventSource', { configurable: true, value: undefined });
    }
  }, { now: OBSERVED_AT, disableEventSource: options.disableEventSource !== false });

  const snapshot = options.snapshot ?? operationalMft();
  await page.route('**/api/v1/console/runtime-contract', route => fulfill(route, runtimeContract));
  await page.route('**/api/v1/console/hourly-alpha', route => fulfill(route, options.hourly ?? hourlyAlpha()));
  await page.route('**/api/v1/console/validation', route => fulfill(route, validationEvidence));
  await page.route('**/api/v1/console/experiments', route => fulfill(route, experimentsEvidence));
  await page.route('**/api/v1/console/alpha-zoo', route => fulfill(route, alphaZoo));
  await page.route('**/api/v1/console/alpha-lifecycle', route => fulfill(route, alphaLifecycle));
  await page.route('**/api/v1/console/portfolio', route => fulfill(route, portfolio));
  await page.route('**/api/v1/console/risk-dashboard?*', route => fulfill(route, risk));
  await page.route('**/api/v1/console/events', route => fulfill(route, auditEvents));
  await page.route('**/api/v1/console/system-health', route => fulfill(route, systemHealth));
  await page.route('**/api/v1/console/mft-execution/delta?*', options.deltaHandler
    ?? (route => fulfill(route, emptyDelta(snapshot as MftSnapshot))));
  await page.route('**/api/v1/console/mft-execution', options.snapshotHandler
    ?? (route => fulfill(route, snapshot)));
  if (options.streamHandler) {
    await page.route('**/api/v1/console/stream/mft-execution?*', options.streamHandler);
  }
}

test('nine studio destinations and keyboard search stay on research surfaces',async({page})=>{
 await installReadOnlyApi(page);
 for(const [path,heading] of [['/','研究工作台'],['/research/runs','实验运行'],['/studio/forward','前向观察'],['/studio/modules','数据与模块'],['/studio/system','系统状态'],['/studio/market','行情终端'],['/studio/analysis','研究分析'],['/studio/models','模型诊断'],['/studio/operations','运行监控']]){
  await page.goto(path);await expect(page.getByRole('heading',{name:heading,level:1,exact:true})).toBeVisible();
  await expect(page.getByTitle('L2_DRY_RUN_ONLY',{exact:true})).toHaveAttribute('title','L2_DRY_RUN_ONLY');
 }
 await page.keyboard.press('Control+k');await expect(page.getByLabel('全局搜索运行')).toBeFocused();
 await expect(page.getByRole('navigation',{name:'主导航'}).getByRole('link')).toHaveCount(9);
 await expect(page.getByRole('button',{name:'生成服务端影响预览'})).toHaveCount(0);
});

test('authoritative snapshot advances through a hash-bound delta without duplicate effects', async ({ page }) => {
  const initial = operationalMft();
  const advanced = await nextOrderSnapshot(initial);
  await installReadOnlyApi(page, {
    snapshot: initial,
    deltaHandler: route => fulfill(route, advanced.delta),
  });
  await page.goto('/workbench/execution');
  await expect(page.getByRole('heading', { name: '执行读模型', level: 1 })).toBeVisible();
  await expect(page.getByTestId('authority-event')).toHaveCount(1);
  await expect(page.getByTestId('authority-event')).toContainText('oms.order.completed');
  await expect(page.getByText('current authority sequence 1')).toBeVisible();
});

test('cursor gap performs one full resnapshot and resets session event history', async ({ page }) => {
  const initial = operationalMft();
  const advanced = await nextOrderSnapshot(initial);
  let gapsDelivered = 0;
  let reloadsDelivered = 0;
  await installReadOnlyApi(page, {
    snapshotHandler: route => {
      if (gapsDelivered > reloadsDelivered) {
        reloadsDelivered += 1;
        return fulfill(route, advanced.snapshot);
      }
      return fulfill(route, initial);
    },
    deltaHandler: route => {
      const url = new URL(route.request().url());
      if (url.searchParams.get('cursor') === initial.cursor) {
        gapsDelivered += 1;
        return fulfill(route, {
          schema_version: MFT_CONSOLE_SCHEMA, status: 'RESNAPSHOT_REQUIRED',
          reason: 'CURSOR_GAP', refetch: '/api/v1/console/mft-execution', read_only: true,
        });
      }
      return fulfill(route, emptyDelta(advanced.snapshot));
    },
  });
  await page.goto('/workbench/execution');
  await expect(page.getByRole('heading', { name: '执行读模型', level: 1 })).toBeVisible();
  await expect(page.getByText('NO_SESSION_DELTA_EVENTS · RESET_BY_RESNAPSHOT')).toBeVisible();
  expect(gapsDelivered).toBe(1);
  expect(reloadsDelivered).toBe(1);
});

const CONTRACT_FAILURES = [
  ['unknown schema', () => ({ ...operationalMft(), schema_version: 'quantos.mft-execution-console.v2' }), 'MFT_UNKNOWN_SCHEMA'],
  ['missing field', () => {
    const value = { ...operationalMft() } as Record<string, unknown>;
    delete value.tca;
    return value;
  }, 'MFT_SECTION_MISSING'],
  ['stale snapshot', () => operationalMft({
    status: 'BLOCKED', snapshot_status: 'STALE',
    data_quality: { status: 'STALE', provenance: 'REAL_MFT_READ_MODEL', snapshot_ids: ['0'.repeat(64)] },
    freshness: {
      status: 'STALE', age_ms: 8_000_000, policy_id: 'mft-console-freshness',
      policy_version: '1', max_age_ms: 7_200_000,
    },
    reason: 'FRESHNESS_STALE',
  }), 'FRESHNESS_STALE'],
] as const;

for (const [name, makeValue, reason] of CONTRACT_FAILURES) {
  test(`fails closed on ${name} without mock fallback`, async ({ page }) => {
    await installReadOnlyApi(page, { snapshot: makeValue() });
    await page.goto('/workbench/execution');
    await expect(page.getByRole('heading', { name: '执行读模型', level: 1 })).toBeVisible();
    await expect(page.getByText(reason, { exact: true })).toBeVisible();
    await expect(page.getByTitle('L2_DRY_RUN_ONLY',{exact:true})).toHaveAttribute('title','L2_DRY_RUN_ONLY');
  await expect(page.getByRole('heading',{name:'执行状态',exact:true})).toHaveCount(0);
    await expect(page.locator('body')).not.toContainText('SYNTHETIC');
  });
}

test('classifies HTTP 429 as an explicit unavailable authority', async ({ page }) => {
  await installReadOnlyApi(page, {
    snapshotHandler: route => fulfill(route, { error: 'rate limited' }, 429),
  });
  await page.goto('/workbench/execution');
  await expect(page.getByText('HTTP_429',{exact:true})).toBeVisible();
  await expect(page.getByTitle('L2_DRY_RUN_ONLY',{exact:true})).toHaveAttribute('title','L2_DRY_RUN_ONLY');
  await expect(page.getByRole('heading',{name:'执行状态',exact:true})).toHaveCount(0);
});

test('classifies request timeout without retaining operational values', async ({ page }) => {
  test.setTimeout(25_000);
  await installReadOnlyApi(page, {
    snapshotHandler: async route => {
      await new Promise(resolve => setTimeout(resolve, 11_000));
      if (!route.request().isNavigationRequest()) {
        await fulfill(route, operationalMft()).catch(() => undefined);
      }
    },
  });
  await page.goto('/workbench/execution');
  await expect(page.getByText('TIMEOUT',{exact:true})).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTitle('L2_DRY_RUN_ONLY',{exact:true})).toHaveAttribute('title','L2_DRY_RUN_ONLY');
  await expect(page.getByRole('heading',{name:'执行状态',exact:true})).toHaveCount(0);
});

test('SSE disconnect becomes reconnecting and hides retained execution values', async ({ page }) => {
  await installReadOnlyApi(page, {
    disableEventSource: false,
    streamHandler: route => route.abort('connectionreset'),
  });
  await page.goto('/workbench/execution');
  await expect(page.getByText('连接已中断，正在恢复')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('STREAM_RECONNECTING',{exact:true})).toBeVisible();
  await expect(page.getByTitle('L2_DRY_RUN_ONLY',{exact:true})).toHaveAttribute('title','L2_DRY_RUN_ONLY');
  await expect(page.getByRole('heading',{name:'执行状态',exact:true})).toHaveCount(0);
});

test('empty hourly result remains an explicit no-run state',async({page})=>{
 await installReadOnlyApi(page,{hourly:noHourlySnapshot()});await page.goto('/studio/modules/hourly-alpha');
 await expect(page.getByRole('heading',{name:'数据与模块',level:1})).toBeVisible();
 await expect(page.getByText('当前没有快照',{exact:true})).toBeVisible();
 await expect(page.getByText('尚无真实运行结果；不会把缺失样本显示为零值。')).toBeVisible();
});

test('wide dark and narrow light workbench layouts are accessible and do not overflow', async ({ page }) => {
  await installReadOnlyApi(page);
  await page.goto('/workbench/execution');
  await expect(page.getByRole('heading', { name: '执行读模型', level: 1 })).toBeVisible();

  const darkPixels = await page.screenshot({ animations: 'disabled' });
  expect(darkPixels.byteLength).toBeGreaterThan(10_000);
  let results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(
    results.violations,
    results.violations.map(item => `${item.id}: ${item.help}`).join('\n'),
  ).toEqual([]);

  await page.getByRole('button', { name: '切换深色模式' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-studio-theme', 'dark');
  await page.setViewportSize({ width: 390, height: 844 });
  const geometry = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(geometry.document).toBeLessThanOrEqual(geometry.viewport);
  expect(geometry.body).toBeLessThanOrEqual(geometry.viewport);
  const lightPixels = await page.screenshot({ animations: 'disabled' });
  expect(lightPixels.byteLength).toBeGreaterThan(10_000);
  results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(
    results.violations,
    results.violations.map(item => `${item.id}: ${item.help}`).join('\n'),
  ).toEqual([]);
});
