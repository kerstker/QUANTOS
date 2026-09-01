import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MemoryRouter } from '@/lib/router';
import { ToastProvider } from '@/ui/toast';
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

  it('shows UNAVAILABLE when the fail-closed health client rejects a malformed body', async () => {
    harness.runnerEnabled = true;
    harness.healthMock.mockRejectedValue(new Error('RUNNER_HEALTH_INVALID:LIVE_BOUNDARY'));

    renderPage();

    expect(await screen.findByText('RESEARCH_RUNNER_UNAVAILABLE')).toBeInTheDocument();
    expect(screen.getByText('RUNNER_HEALTH_INVALID:LIVE_BOUNDARY')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /ashare_technical_search/ })).not.toBeInTheDocument();
  });
});
