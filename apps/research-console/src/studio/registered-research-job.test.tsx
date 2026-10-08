import {describe, expect, it, vi} from 'vitest';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter} from '@/lib/router';
import {Launch, RegisteredResearchResults} from './Research';
import {triggerRun} from '@/lib/api/research-runner-api';
import type {RegisteredResearchReport} from '@/lib/types/research';

vi.mock('@/lib/api/research-runner-api', () => ({
  getOperator: () => 'researcher', getRunnerToken: () => 'isolated-fixture',
  setOperator: vi.fn(), setRunnerToken: vi.fn(), cancelRun: vi.fn(),
  triggerRun: vi.fn().mockResolvedValue({run_id: 'native-job', status: 'queued'}),
}));

describe('registered native research handoff', () => {
  it.each(['registered_research', 'registered_research_recover'])('submits only the frozen invocation for %s', async kind => {
    vi.mocked(triggerRun).mockClear();
    render(<MemoryRouter><Launch initialKind={kind} health={{mode: 'RESEARCH_RUNNER',
      live_disallowed: true, kinds: [kind], kind_policies: {}}}/></MemoryRouter>);
    const button = screen.getByRole('button', {name: '提交研究任务'});
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText('冻结运行编号'), {target: {value: '../outside'}});
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText('冻结运行编号'), {target: {value: 'a'.repeat(64)}});
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(triggerRun).toHaveBeenCalledWith(kind,
      {native_invocation_id: 'a'.repeat(64)}, expect.any(String)));
    expect(screen.queryByLabelText('实验编号')).not.toBeInTheDocument();
  });

  it('shows rejected risk and missing research evidence separately from the original account', () => {
    const report: RegisteredResearchReport = {
      schema: 'QUANTOS_REGISTERED_RESEARCH_JOB_V1', status: 'TECHNICAL_VALIDATION_ONLY',
      plan: {invocation_id: 'a'.repeat(64), adapter: 'TECHNICAL_LINEAR_PERPETUAL_V2',
        search: {search_id: 'native', dataset_version: 'fixture', recorded_at_ms: 1},
        source_identity: {commit: 'c'.repeat(40), python_source_sha256: 'd'.repeat(64),
          dependency_lock_sha256: 'e'.repeat(64), python_version: '3.12.3', architecture: 'aarch64'},
        trials: [{candidate_id: 'fixture-alpha', config_hash: 'f'.repeat(64),
          native_trial_id: 'b'.repeat(64), provenance_trial_id: 'native:fixture'}], evaluation_core: {}},
      plan_sha256: 'f'.repeat(64), native_trial_id: 'b'.repeat(64), result_hash: '1'.repeat(64),
      account: {initial_equity: '1000', ending_equity: '1000.1299', residual: '0'},
      research_status: {oos: 'UNKNOWN', statistical_review: 'NOT_REVIEWED', risk_replay: 'REJECTED',
        independent_review: 'INDEPENDENT_REVIEW_REQUIRED', candidate_admission: 'NOT_APPROVED'},
      economic_authority: false, submission_enabled: false, promotion_approved: false,
    };
    render(<MemoryRouter><RegisteredResearchResults report={report}/></MemoryRouter>);
    expect(screen.getByText('锁定样本外验证')).toBeInTheDocument();
    expect(screen.getByText('风险复算')).toBeInTheDocument();
    expect(screen.getByText('1000.1299')).toBeInTheDocument();
    expect(screen.getByText('尚未评审')).toBeInTheDocument();
    expect(screen.getByText('未准入')).toBeInTheDocument();
    expect(screen.getByText('未通过')).toBeInTheDocument();
    expect(screen.queryByText('PASS')).not.toBeInTheDocument();
  });
});
