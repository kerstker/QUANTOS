import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const OBSERVED_AT = 1_785_485_080_000;

const runtimeContract = {
  source: 'BACKEND_COMPUTED_SNAPSHOT',
  read_only: true,
  api_version: '0.1.0',
  data_provenance: 'E2E_FIXED_RUNTIME_CONTRACT',
  instance_id: 'quantos-e2e',
  build_sha_known: true,
  build_sha: 'dd996e79df78c5a7529575d9d690aad3debfe451',
  event_schema_catalog: { sha256: 'a'.repeat(64) },
  safety_boundary: {
    read_only_console: true,
    authorized_level: 'L2_DRY_RUN_ONLY',
    live_trading_authorized: false,
    order_submission_allowed: false,
    broker_connectivity: false,
  },
};

function paperAlpha(
  alphaId: string,
  equity: number,
  netPnl: number,
  positions: Record<string, number>,
) {
  return {
    alpha_id: alphaId,
    status: 'PAPER_MARKED',
    equity,
    net_pnl: netPnl,
    funding_pnl: 250,
    positions,
    reconciliation_residual: 0,
  };
}

function formalRisk(
  alphaId: string,
  gatesPassed: number,
  capacityUsage: number,
  validationConfidence: number,
  decayFlags = 0,
) {
  return {
    alpha_id: alphaId,
    paper_execution: 'PAPER_BLOCKED',
    research_risk: {
      decision: 'RISK_REJECTED',
      status: 'REJECTED',
      gates_passed: gatesPassed,
      gates_run: 9,
      engines_run: {
        capacity: capacityUsage <= 0.8,
        model_alpha: false,
      },
      reasons: ['MODEL_ALPHA_CONFIDENCE_BELOW_FLOOR'],
      reports: {
        capacity: {
          passed: capacityUsage <= 0.8,
          capacity_usage: capacityUsage,
          bottleneck_alpha: 'ATOM',
        },
        model_alpha: {
          passed: false,
          alphas_with_decay: decayFlags,
          alphas_with_overfitting_flags: 0,
          alphas_with_leakage_flags: 0,
          min_validation_confidence: validationConfidence,
          min_paper_confidence: 0.125,
        },
      },
    },
  };
}

const dryRunSnapshot = {
  source: 'BACKEND_COMPUTED_SNAPSHOT',
  read_only: true,
  api_version: '0.1.0',
  data_provenance: 'REAL_DRY_RUN_OFFLINE_SNAPSHOT',
  snapshot_present: true,
  verdict: 'DRY_RUN_OK',
  n_runs: 648,
  latest: {},
  runs: [],
  paper_shadow: {
    source: 'AUTOMATED_SHADOW_PAPER_ARTIFACTS',
    read_only: true,
    snapshot_present: true,
    execution_class: 'AUTOMATED_NONPROMOTABLE_SHADOW_PAPER',
    status: 'RUNNING',
    generated_at: OBSERVED_AT,
    observed_at: OBSERVED_AT,
    provider: 'BINANCE_PUBLIC_MARK_PRICE',
    verdict: {
      shadow_paper_running: true,
      auto_rebalance_ready: true,
      paper_mark_ready: true,
      boundary_valid: true,
      official_paper_ready: false,
      promotion_eligible: false,
      forward_evidence_eligible: false,
      deployment_eligible: false,
      live_allowed: false,
      order_submission_allowed: false,
    },
    alphas: {
      carry_oi_majors: paperAlpha('carry_oi_majors', 949_509, -50_491, {
        ADA: 772_331, ATOM: 95_675, BTC: -2, DOT: 164_344,
      }),
      crowd_contra: paperAlpha('crowd_contra', 1_006_490, 6_490, {
        ATOM: 95_675, BTC: 2, SOL: -1_694, XRP: -116_975,
      }),
      top_contra: paperAlpha('top_contra', 991_645, -8_355, {
        ADA: 772_331, AVAX: 19_014, DOGE: -1_764_214, ETH: 65,
      }),
    },
    previews: {},
    history: [
      { alpha_id: 'carry_oi_majors', observed_at: OBSERVED_AT - 900_000, equity: 944_000, net_pnl: -56_000 },
      { alpha_id: 'crowd_contra', observed_at: OBSERVED_AT - 900_000, equity: 1_009_000, net_pnl: 9_000 },
      { alpha_id: 'top_contra', observed_at: OBSERVED_AT - 900_000, equity: 992_000, net_pnl: -8_000 },
      { alpha_id: 'carry_oi_majors', observed_at: OBSERVED_AT, equity: 949_509, net_pnl: -50_491 },
      { alpha_id: 'crowd_contra', observed_at: OBSERVED_AT, equity: 1_006_490, net_pnl: 6_490 },
      { alpha_id: 'top_contra', observed_at: OBSERVED_AT, equity: 991_645, net_pnl: -8_355 },
    ],
    totals: { equity: 2_947_644, net_pnl: -52_356, funding_pnl: 1_403 },
    paper_ledger: { n_rows: 6, n_decisions: 2 },
    persistence: { new_snapshots: 3, chain_valid: true },
    errors: {},
    safety: {
      paper_only: true,
      separate_from_official_paper: true,
      public_market_data_only: true,
      private_account_access: false,
      credentials_accessed: false,
      broker_connectivity: false,
      broker_order_submission: false,
    },
  },
  scheduler_watchdog: {
    snapshot_present: true,
    verdict: 'PASS',
    summary: { PASS: 2 },
    checks: [
      { task_name: 'QUANTOS_paper_shadow_auto', status: 'PASS', code: 'HEALTHY', detail: 'healthy' },
      { task_name: 'QUANTOS_paper_shadow_realtime', status: 'PASS', code: 'HEALTHY', detail: 'healthy' },
    ],
  },
  paper_health: {
    source: 'PAPER_HEALTH_CHECK_ARTIFACT',
    read_only: true,
    snapshot_present: true,
    generated_at: OBSERVED_AT,
    observed_at: OBSERVED_AT,
    age_ms: 0,
    max_age_ms: 300_000,
    readiness_class: 'PAPER_MARKS_ONLY_REPLAY',
    next_action: 'resolve_research_risk_and_create_new_decision',
    blockers: ['MODEL_ALPHA_CONFIDENCE_BELOW_FLOOR'],
    research_risk_bundle_complete: true,
    research_risk_bundles: {
      carry_oi_majors: { status: 'READY', missing: [], reasons: [] },
      crowd_contra: { status: 'READY', missing: [], reasons: [] },
      top_contra: { status: 'READY', missing: [], reasons: [] },
    },
    forward_evidence: {
      present: true,
      evidence_gate_periods: 8,
      complete_for_all_three: false,
      alphas: {
        carry_oi_majors: { n_forward_periods: 1, periods_to_gate: 7 },
        crowd_contra: { n_forward_periods: 1, periods_to_gate: 7 },
        top_contra: { n_forward_periods: 1, periods_to_gate: 7 },
      },
    },
    verdict: {
      paper_ready: false,
      paper_mark_ready: true,
      paper_preview_ready: false,
      backtest_reference_present_for_all_three: true,
      funding_data_complete_for_all_three: true,
      live_allowed: false,
      order_submission_allowed: false,
    },
  },
  paper_order_preview: {
    source: 'PAPER_ORDER_PREVIEW',
    read_only: true,
    snapshot_present: true,
    verdict: {
      paper_preview_ready: false,
      live_allowed: false,
      order_submission_allowed: false,
    },
    alphas: {
      carry_oi_majors: formalRisk('carry_oi_majors', 7, 0.998, 0),
      crowd_contra: formalRisk('crowd_contra', 8, 0.783, 0.02),
      top_contra: formalRisk('top_contra', 8, 0.4, 0.198, 1),
    },
    errors: {},
    safety: {
      paper_only: true,
      broker_connectivity: false,
      broker_order_submission: false,
      credentials_accessed: false,
    },
  },
  data_refresh: { outcome: 'PASS', failed_total: 0, alerts: [] },
  route_tca_history: { status: 'LONGITUDINAL_READY', record_count: 2_540 },
  safety_note: 'L2 only.',
};

async function installFixedApi(page: Page) {
  await page.addInitScript(fixedNow => {
    Date.now = () => fixedNow;
  }, OBSERVED_AT + 30_000);
  await page.route('**/api/v1/console/runtime-contract', route => route.fulfill({ json: runtimeContract }));
  await page.route('**/api/v1/console/dry-run', route => route.fulfill({ json: dryRunSnapshot }));
}

test.beforeEach(async ({ page }) => {
  await installFixedApi(page);
  await page.goto('/research/dry-run');
  await expect(page.getByRole('heading', { name: '全自动影子纸面驾驶舱' })).toBeVisible();
  await expect(page.getByText('SCHEDULED_ACTIVE')).toBeVisible();
});

test('paper cockpit is accessible and never overflows the institutional desktop viewport', async ({ page }) => {
  const geometry = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(geometry.document).toBeLessThanOrEqual(geometry.viewport);
  expect(geometry.body).toBeLessThanOrEqual(geometry.viewport);

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(
    results.violations,
    results.violations.map(item => `${item.id}: ${item.help}`).join('\n'),
  ).toEqual([]);
});

test('paper cockpit visual contract', async ({ page }) => {
  await page.getByTestId('session-clock').evaluate(element => {
    element.style.visibility = 'hidden';
  });
  await expect(page).toHaveScreenshot('paper-cockpit.png');
});
