import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MemoryRouter } from '@/lib/router';
import { ToastProvider } from '@/ui/toast';
import { triggerRun } from '@/lib/api/research-runner-api';
import ResearchRuns from '../ResearchRuns';

const harness = vi.hoisted(() => ({
  snapshotCalls: vi.fn(),
  runnerEnabled: false,
  healthMock: vi.fn(),
  payload: {
    source: 'BACKEND_COMPUTED_SNAPSHOT',
    read_only: true,
    api_version: '0.1.0',
    data_provenance: 'RESEARCH_RUN_REGISTRY',
    evidence_status: 'DIAGNOSTIC_ONLY',
    runs: [
      {
        run_id: 'run-active', kind: 'ashare_technical_search', title: 'Hidden active job',
        status: 'running', created_ts: 1, headline: { label: null, value: null },
        verdict: 'RUNNING', params: {}, code_version: null,
      },
      {
        run_id: 'run-done', kind: 'data_refresh', title: 'Completed visible job',
        status: 'done', created_ts: 2, headline: { label: 'rows', value: 10 },
        verdict: 'DIAGNOSTIC', params: {}, code_version: null,
      },
    ],
  },
}));

vi.mock('@/lib/api/research-console-api', () => ({ fetchResearchRuns: vi.fn() }));
vi.mock('@/lib/api/research-runner-api', () => ({
  fetchRunnerHealth: harness.healthMock,
  getOperator: () => 'console-operator',
  getRunnerToken: () => '',
  runnerEnabled: () => harness.runnerEnabled,
  setOperator: vi.fn(),
  setRunnerToken: vi.fn(),
  triggerRun: vi.fn(),
}));
vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: (_fetcher: unknown, deps: ReadonlyArray<unknown>) => {
    harness.snapshotCalls(deps);
    return {
      payload: harness.payload,
      apiState: 'connected',
      error: null,
      usingBackend: true,
      snapshotState: 'DIAGNOSTIC',
      actionsAllowed: false,
    };
  },
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/research/runs']}>
      <ToastProvider><ResearchRuns /></ToastProvider>
    </MemoryRouter>,
  );
}

describe('ResearchRuns polling', () => {
  beforeEach(() => {
    harness.snapshotCalls.mockClear();
    harness.runnerEnabled = false;
    harness.healthMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps polling for an active registry run even when the table filter hides it', () => {
    vi.useFakeTimers();
    renderPage();
    fireEvent.change(screen.getByPlaceholderText('筛选 run id / kind / verdict'), {
      target: { value: 'Completed visible' },
    });
    expect(screen.queryByText('Hidden active job')).not.toBeInTheDocument();
    expect(screen.getByText('Completed visible job')).toBeInTheDocument();

    const callsBeforeTick = harness.snapshotCalls.mock.calls.length;
    act(() => vi.advanceTimersByTime(3_000));

    expect(harness.snapshotCalls.mock.calls.length).toBeGreaterThan(callsBeforeTick);
    const lastCall = harness.snapshotCalls.mock.calls[harness.snapshotCalls.mock.calls.length - 1];
    expect(lastCall[0]).toEqual([1]);
  });

  it.each(['btc_mechanism', 'perpetual_alpha', 'orderblock_research', 'btc_factory'])('submits the explicit attempt for %s', async kind => {
    harness.runnerEnabled = true;
    harness.healthMock.mockResolvedValue({ mode: 'L2_DRY_RUN_ONLY', live_disallowed: true, kinds: [kind] });
    vi.mocked(triggerRun).mockResolvedValue({ run_id: 'new-run', kind: 'btc_mechanism', status: 'queued' });
    renderPage();
    const button = await screen.findByRole('button', { name: new RegExp(kind) });
    fireEvent.change(screen.getByLabelText(/Runner token/), { target: { value: 'test-only' } });
    fireEvent.change(screen.getByLabelText(/实验编号/), { target: { value: '0' } });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/实验编号/), { target: { value: '7' } });
    fireEvent.click(button);
    expect(triggerRun).toHaveBeenLastCalledWith(kind, { attempt: 7 });
  });

  it('sends explicit resume and forward operations', async () => {
    harness.runnerEnabled = true;
    harness.healthMock.mockResolvedValue({ mode: 'RESEARCH_RUNNER', live_disallowed: true, kinds: ['btc_factory', 'btc_forward'] });
    vi.mocked(triggerRun).mockResolvedValue({ run_id: 'new-run', kind: 'btc_factory', status: 'queued' });
    renderPage();
    const factory = await screen.findByRole('button', { name: /btc_factory/ });
    fireEvent.change(screen.getByLabelText(/Runner token/), { target: { value: 'test-only' } });
    fireEvent.click(screen.getByLabelText(/恢复同编号/));
    fireEvent.click(factory);
    expect(triggerRun).toHaveBeenLastCalledWith('btc_factory', { attempt: 1, resume: true });
    const forward = await screen.findByRole('button', { name: /btc_forward/ });
    fireEvent.change(screen.getByLabelText('前向操作'), { target: { value: '1' } });
    await act(async () => {});
    fireEvent.click(forward);
    expect(triggerRun).toHaveBeenLastCalledWith('btc_forward', { attempt: 1, action: 1 });
  });

  it('shows UNAVAILABLE when the fail-closed health client rejects a malformed body', async () => {
    harness.runnerEnabled = true;
    harness.healthMock.mockRejectedValue(new Error('RUNNER_HEALTH_INVALID:LIVE_BOUNDARY'));

    renderPage();

    expect(await screen.findByText('RESEARCH_RUNNER_UNAVAILABLE')).toBeInTheDocument();
    expect(screen.getByText('RUNNER_HEALTH_INVALID:LIVE_BOUNDARY')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /ashare_technical_search/ })).not.toBeInTheDocument();
  });
});
