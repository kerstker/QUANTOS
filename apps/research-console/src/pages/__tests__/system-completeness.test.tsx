import { render, screen } from '@testing-library/react';
import { MemoryRouter } from '@/lib/router';
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
      summary: { total: 4, passed: 1, partial: 3, failed: 0, unverified: 0,
        manual_evidence_required: 0, red_lines_failed: 0 },
      domains: [{ domain: 'governance_security', current_maturity: 'M2', requirements: 4,
        passed: 1, red_lines_failed: 0 }],
      requirements: [
        { id: 'GOV.APPROVAL.HASHCHAIN', domain: 'governance_security', tier: 'A',
          target_maturity: 'M3', current_maturity: 'M3', classification: 'L2_SAFE_NOW',
          description: 'approval chain', required_evidence: ['implementation'], red_line: false,
          checker: 'paths_exist', next_action: 'bind identity', status: 'PASS', meets_target: true,
          evidence: ['services/governance/approval.py'], detail: 'evidence present' },
        { id: 'GOV.IDENTITY.STRONG_AUTH', domain: 'governance_security', tier: 'B',
          target_maturity: 'M4', current_maturity: 'M3', classification: 'L2_SAFE_NOW',
          description: 'strong identity', required_evidence: ['governance'], red_line: true,
          checker: 'manual_evidence', next_action: 'implement OIDC',
          status: 'PARTIAL', meets_target: false, evidence: [], detail: 'external proof required' },
        { id: 'DATA.VENDOR.DEPTH', domain: 'data', tier: 'B',
          target_maturity: 'M4', current_maturity: 'M2', classification: 'NEEDS_EXTERNAL',
          description: 'licensed depth', required_evidence: ['external'], red_line: false,
          checker: 'vendor_depth', next_action: 'acquire licensed feed', status: 'PARTIAL',
          meets_target: false, evidence: ['evidence/vendor_data_baseline.json'],
          detail: 'licensed delivery missing' },
        { id: 'EXECUTION.MULTIVENUE.SOR', domain: 'execution', tier: 'B',
          target_maturity: 'M4', current_maturity: 'M3', classification: 'POST_L2',
          description: 'production SOR', required_evidence: ['production'], red_line: false,
          checker: 'sor', next_action: 'retain paper TCA until post-L2', status: 'PARTIAL',
          meets_target: false, evidence: ['evidence/dry_run_execution_baseline.json'],
          detail: 'production venue proof is post-L2' },
      ],
      red_lines: [],
      safety_boundary: { read_only_console: true, execution_boundary: 'LIVE_TRADING_DISALLOWED',
        authorized_level: 'L2_DRY_RUN_ONLY', live_trading_authorized: false },
      inventory: { domains: [], total_services_modules: 1, total_services_loc: 1,
        n_test_files: 1, n_test_functions: 1, n_console_pages: 1, n_console_endpoints: 1 },
    },
  }),
}));


describe('SystemCompleteness evidence assessment', () => {
  it('leads with a sorted pre-live blocker queue and keeps the full evidence matrix available', () => {
    render(<MemoryRouter><SystemCompleteness /></MemoryRouter>);
    expect(screen.getByText('实盘前阻断队列')).toBeInTheDocument();
    expect(screen.getByText('1 项现在处理')).toBeInTheDocument();
    expect(screen.getByText('1 项需要外部资源')).toBeInTheDocument();
    expect(screen.getByText('1 项在 L2 之后')).toBeInTheDocument();
    expect(screen.getAllByTestId('prelive-blocker').map(row => row.textContent)).toEqual([
      expect.stringContaining('GOV.IDENTITY.STRONG_AUTH'),
      expect.stringContaining('DATA.VENDOR.DEPTH'),
      expect.stringContaining('EXECUTION.MULTIVENUE.SOR'),
    ]);
    expect(screen.getByText('高级证据矩阵')).toBeInTheDocument();
    expect(screen.getByText('A 级')).toBeInTheDocument();
    expect(screen.getByText('B 级')).toBeInTheDocument();
    expect(screen.getByText('红线')).toBeInTheDocument();
    expect(screen.getByText(/机器可验证的仓库证据/)).toBeInTheDocument();
    expect(screen.getAllByText('GOV.IDENTITY.STRONG_AUTH').length).toBeGreaterThan(0);
    expect(screen.queryByText(/Completion Score/i)).not.toBeInTheDocument();
  });
});
