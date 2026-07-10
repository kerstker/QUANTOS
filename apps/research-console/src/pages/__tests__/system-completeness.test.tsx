import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import SystemCompleteness from '@/pages/SystemCompleteness';


vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: () => ({
    usingBackend: true,
    apiState: 'ready',
    error: null,
    payload: {
      source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
      data_provenance: 'TOP_TIER_EVIDENCE_ASSESSMENT',
      assessment: { schema_version: 1, verdict: 'NOT_TOP_TIER' },
      summary: { total: 2, passed: 1, partial: 0, failed: 0, unverified: 0,
        manual_evidence_required: 1, red_lines_failed: 1 },
      domains: [{ domain: 'governance_security', current_maturity: 'M1', requirements: 2,
        passed: 1, red_lines_failed: 1 }],
      requirements: [
        { id: 'GOV.APPROVAL.HASHCHAIN', domain: 'governance_security', tier: 'A',
          target_maturity: 'M3', current_maturity: 'M3', classification: 'L2_SAFE_NOW',
          description: 'approval chain', required_evidence: ['implementation'], red_line: false,
          checker: 'paths_exist', next_action: 'bind identity', status: 'PASS', meets_target: true,
          evidence: ['services/governance/approval.py'], detail: 'evidence present' },
        { id: 'GOV.IDENTITY.STRONG_AUTH', domain: 'governance_security', tier: 'B',
          target_maturity: 'M4', current_maturity: 'M1', classification: 'L2_SAFE_NOW',
          description: 'strong identity', required_evidence: ['governance'], red_line: true,
          checker: 'manual_evidence', next_action: 'implement OIDC',
          status: 'MANUAL_EVIDENCE_REQUIRED', meets_target: false, evidence: [],
          detail: 'external proof required' },
      ],
      red_lines: [{ id: 'GOV.IDENTITY.STRONG_AUTH', domain: 'governance_security', tier: 'B',
        target_maturity: 'M4', current_maturity: 'M1', classification: 'L2_SAFE_NOW',
        description: 'strong identity', required_evidence: ['governance'], red_line: true,
        checker: 'manual_evidence', next_action: 'implement OIDC',
        status: 'MANUAL_EVIDENCE_REQUIRED', meets_target: false, evidence: [],
        detail: 'external proof required' }],
      safety_boundary: { read_only_console: true, execution_boundary: 'LIVE_TRADING_DISALLOWED',
        authorized_level: 'L2_DRY_RUN_ONLY', live_trading_authorized: false },
      inventory: { domains: [], total_services_modules: 1, total_services_loc: 1,
        n_test_files: 1, n_test_functions: 1, n_console_pages: 1, n_console_endpoints: 1 },
    },
  }),
}));


describe('SystemCompleteness evidence assessment', () => {
  it('renders tiers, red lines and evidence without hand-written scores', () => {
    render(<MemoryRouter><SystemCompleteness /></MemoryRouter>);
    expect(screen.getByText('Tier A')).toBeInTheDocument();
    expect(screen.getByText('Tier B')).toBeInTheDocument();
    expect(screen.getByText('Red Lines')).toBeInTheDocument();
    expect(screen.getByText(/Machine-verifiable repository evidence/i)).toBeInTheDocument();
    expect(screen.getAllByText('GOV.IDENTITY.STRONG_AUTH').length).toBeGreaterThan(0);
    expect(screen.queryByText(/Completion Score/i)).not.toBeInTheDocument();
  });
});
