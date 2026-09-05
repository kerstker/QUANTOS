import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from '@/lib/router';
import RunDetail from '../RunDetail';

const state = vi.hoisted(() => ({ kind: 'perpetual_replay', status: 'done', authoritative: false, resultId: 'run-1' }));
vi.mock('@/lib/router', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/router')>(), useParams: () => ({ id: 'run-1' }),
}));
vi.mock('@/lib/api/research-console-api', () => ({
  RESEARCH_CONSOLE_API_BASE: 'http://localhost:8400',
  fetchResearchRun: () => ({ run: { run_id: 'run-1', kind: state.kind, title: 'Replay test',
    status: state.status, created_ts: 0, code_version: 'fixture' } }),
  fetchTechnicalReplay: () => ({ authoritative: false, evidence_status: 'TECHNICAL_VALIDATION_ONLY',
    technical_replay: { run_id: state.resultId, authoritative: state.authoritative,
      evidence_status: 'TECHNICAL_VALIDATION_ONLY', metrics: { result_hash: 'verified-result-digest' },
      series: { net_equity: [{ label: '1000', value: 1000 }] }, caveats: ['exact audit record'] } }),
}));
vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: (fetcher: () => unknown) => ({ payload: fetcher(), usingBackend: true,
    apiState: 'connected', error: null, snapshotState: 'DIAGNOSTIC' }),
}));
vi.mock('@/lib/api/research-runner-api', () => ({
  runnerEnabled: () => true,
  cancelRun: async () => ({ run_id: 'run-1', cancelled: true }),
}));
function show() { return render(<MemoryRouter><RunDetail /></MemoryRouter>); }

describe('Separate technical replay result', () => {
  beforeEach(() => { state.kind = 'perpetual_replay'; state.status = 'done'; state.authoritative = false; state.resultId = 'run-1'; });
  it('shows verified configuration and order audit in the technical view', () => {
    show();
    expect(screen.getByText('verified-result-digest')).toBeInTheDocument();
    expect(screen.getByText('exact audit record')).toBeInTheDocument();
  });
  it.each(['legacy', 'authority', 'different-run'])('rejects %s data from the technical view', problem => {
    if (problem === 'legacy') state.kind = 'combo_book';
    if (problem === 'authority') state.authoritative = true;
    if (problem === 'different-run') state.resultId = 'other-run';
    show();
    expect(screen.queryByText('verified-result-digest')).not.toBeInTheDocument();
  });
  it('explains soft cancellation instead of claiming the computation stopped', async () => {
    state.status = 'running';
    show();
    fireEvent.click(screen.getByRole('button', { name: /取消|Cancel/ }));
    expect(await screen.findByText(/软取消|Soft cancellation/)).toBeInTheDocument();
  });
});
