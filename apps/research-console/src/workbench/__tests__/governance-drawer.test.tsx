import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  RuntimeContractPayload,
  SystemCompletenessPayload,
  TopTierRequirement,
} from '@/lib/types/research';
import { GovernanceDrawer } from '@/workbench/drawers/GovernanceDrawer';

const snapshotMock = vi.hoisted(() => vi.fn());

vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: snapshotMock,
}));

const runtimePayload: RuntimeContractPayload = {
  source: 'runtime-contract',
  read_only: true,
  api_version: '1.0.0',
  contract_version: 'console-runtime.v1',
  service: 'research-console',
  instance_id: 'test-instance',
  process_id: 42,
  build_sha: '1234567890abcdef',
  build_sha_known: true,
  event_schema_catalog: {
    sha256: 'abcdef1234567890',
    event_type_count: 10,
    latest_versions: {},
  },
  safety_boundary: {
    read_only_console: true,
    authorized_level: 'L2_DRY_RUN_ONLY',
    live_trading_authorized: false,
    order_submission_allowed: false,
    broker_connectivity: false,
  },
  generated_at_ms: 1,
};

function requirement(overrides: Partial<TopTierRequirement> = {}): TopTierRequirement {
  return {
    id: 'GOV.IDENTITY.STRONG_AUTH',
    domain: 'governance',
    tier: 'A',
    target_maturity: 'M4',
    current_maturity: 'M2',
    classification: 'NEEDS_EXTERNAL',
    description: 'Strong operator identity',
    required_evidence: ['external identity receipt'],
    red_line: true,
    checker: 'strong_identity',
    next_action: 'Retain an external identity attestation.',
    status: 'FAILED',
    meets_target: false,
    evidence: ['evidence/strong_identity.json'],
    detail: 'External identity authority is not yet retained.',
    ...overrides,
  };
}

function completenessPayload(
  overrides: Partial<SystemCompletenessPayload> = {},
): SystemCompletenessPayload {
  const blocker = requirement();
  return {
    source: 'evidence/top_tier_assessment.json',
    read_only: true,
    api_version: '1.0.0',
    data_provenance: 'TOP_TIER_EVIDENCE_ASSESSMENT',
    assessment: { schema_version: 1, verdict: 'NOT_TOP_TIER' },
    inventory: {
      domains: [],
      total_services_modules: 320,
      total_services_loc: 80_000,
      n_test_files: 400,
      n_test_functions: 2_000,
      n_console_pages: 24,
      n_console_endpoints: 30,
    },
    summary: {
      total: 22,
      passed: 15,
      partial: 1,
      failed: 4,
      unverified: 0,
      manual_evidence_required: 2,
      red_lines_failed: 1,
    },
    domains: [],
    requirements: [
      requirement({
        id: 'FRONTEND.EVIDENCE.SINGLE_SOURCE',
        domain: 'frontend_console',
        status: 'PASS',
        meets_target: true,
        red_line: false,
        classification: 'L2_SAFE_NOW',
        current_maturity: 'M4',
        next_action: '',
        detail: 'Machine assessment is the single source.',
      }),
      blocker,
    ],
    red_lines: [blocker],
    safety_boundary: {
      read_only_console: true,
      execution_boundary: 'LIVE_TRADING_DISALLOWED',
      authorized_level: 'L2_DRY_RUN_ONLY',
      live_trading_authorized: false,
    },
    ...overrides,
  };
}

function connected<T>(payload: T) {
  return {
    payload,
    apiState: 'connected' as const,
    error: null,
    usingBackend: true,
  };
}

function renderDrawer(assessment: SystemCompletenessPayload) {
  snapshotMock
    .mockReturnValueOnce(connected(runtimePayload))
    .mockReturnValueOnce(connected(assessment));
  return render(<GovernanceDrawer />);
}

describe('GovernanceDrawer system completeness evidence', () => {
  beforeEach(() => snapshotMock.mockReset());

  it('renders the machine assessment and controlled blocker queue without a percentage score', () => {
    renderDrawer(completenessPayload());

    const evidence = screen.getByRole('region', { name: 'system completeness evidence' });
    expect(evidence).toHaveTextContent('TOP_TIER_EVIDENCE_ASSESSMENT');
    expect(evidence).toHaveTextContent('NOT_TOP_TIER');
    expect(evidence).toHaveTextContent('15/22');
    expect(evidence).toHaveTextContent('GOV.IDENTITY.STRONG_AUTH');
    expect(evidence).toHaveTextContent('Retain an external identity attestation.');
    expect(evidence.textContent).not.toMatch(/\d+(?:\.\d+)?%/);
  });

  it('fails closed when the payload provenance is not the governed assessment', () => {
    renderDrawer(completenessPayload({ data_provenance: 'LEGACY_HANDWRITTEN_SCORE' }));

    const evidence = screen.getByRole('region', { name: 'system completeness evidence' });
    expect(evidence).toHaveTextContent('SYSTEM_COMPLETENESS_UNVERIFIED');
    expect(evidence).not.toHaveTextContent('NOT_TOP_TIER');
    expect(evidence).not.toHaveTextContent('15/22');
  });
});
