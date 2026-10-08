import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MFT_CONSOLE_SCHEMA, type MftSnapshot } from '../contracts';
import { useMftReadModel } from '../useMftReadModel';
import { makeMftSnapshot } from './mft-fixtures';

const api = vi.hoisted(() => ({
  fetchSnapshot: vi.fn(),
  fetchDelta: vi.fn(),
}));

vi.mock('@/lib/api/research-console-api', () => ({
  RESEARCH_CONSOLE_API_BASE: 'http://127.0.0.1:8400',
  fetchMftExecutionSnapshot: api.fetchSnapshot,
  fetchMftExecutionDelta: api.fetchDelta,
}));

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  closed = false;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.instances.push(this);
  }

  close() { this.closed = true; }
}

function metadata(snapshot: MftSnapshot): Record<string, unknown> {
  const copy = JSON.parse(JSON.stringify(snapshot)) as Record<string, unknown>;
  for (const section of [
    'market_gap', 'scheduler', 'intent_funnel', 'oms', 'fills', 'positions',
    'risk', 'reconciliation', 'tca', 'mandate_expiry',
  ]) delete copy[section];
  return copy;
}

function emptyDelta(snapshot: MftSnapshot) {
  return {
    source: snapshot.source,
    read_only: true,
    api_version: 'v1',
    data_provenance: 'REAL_MFT_READ_MODEL',
    schema_version: MFT_CONSOLE_SCHEMA,
    status: 'OK',
    from_cursor: snapshot.cursor,
    cursor: snapshot.cursor,
    as_of_ms: snapshot.as_of_ms,
    events: [],
    has_more: false,
    result_metadata: metadata(snapshot),
  };
}

function Harness() {
  const state = useMftReadModel();
  return (
    <div>
      <span data-testid="phase">{state.phase}</span>
      <span data-testid="cursor">{state.snapshot?.cursor ?? 'none'}</span>
      <span data-testid="reason">{state.reason ?? 'none'}</span>
    </div>
  );
}

describe('useMftReadModel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
    const snapshot = makeMftSnapshot();
    api.fetchSnapshot.mockResolvedValue(snapshot);
    api.fetchDelta.mockResolvedValue(emptyDelta(snapshot));
  });

  it('loads snapshot+delta, opens cursor-bound SSE, and retains state while reconnecting', async () => {
    const view = render(<Harness />);
    await waitFor(() => expect(screen.getByTestId('phase')).toHaveTextContent('LIVE'));
    const source = FakeEventSource.instances[0];
    expect(source.url).toContain('/api/v1/console/stream/mft-execution?cursor=');
    act(() => source.onerror?.(new Event('error')));
    expect(screen.getByTestId('phase')).toHaveTextContent('RECONNECTING');
    expect(screen.getByTestId('cursor')).not.toHaveTextContent('none');
    act(() => source.onopen?.(new Event('open')));
    expect(screen.getByTestId('phase')).toHaveTextContent('RECONNECTING');
    const snapshot = makeMftSnapshot();
    act(() => source.onmessage?.({
      data: JSON.stringify({
        source: 'MFT_READ_MODEL_SQLITE_AUTHORITY',
        read_only: true,
        schema_version: 'quantos.mft-stream-notice.v1',
        status: 'CURRENT',
        as_of_ms: snapshot.as_of_ms,
        cursor: snapshot.cursor,
        watermark: snapshot.watermark,
        freshness: { status: snapshot.freshness.status, age_ms: snapshot.freshness.age_ms },
        reason: null,
      }),
    } as MessageEvent<string>));
    expect(screen.getByTestId('phase')).toHaveTextContent('LIVE');
    view.unmount();
    expect(source.closed).toBe(true);
  });

  it('reloads a full snapshot after a malformed stream notice', async () => {
    const first = makeMftSnapshot();
    const hash = 'a'.repeat(64);
    const second = makeMftSnapshot({
      snapshot_id: hash,
      snapshot_ids: [hash],
      data_quality: { status: 'REAL', provenance: 'REAL_MFT_READ_MODEL', snapshot_ids: [hash] },
      watermark: { sequence: 1, event_at_ms: 4_000, event_hash: hash },
      cursor: `1:${hash}`,
      as_of_ms: 4_500,
    });
    api.fetchSnapshot.mockReset().mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    api.fetchDelta.mockImplementation(async (cursor: string) => (
      cursor === first.cursor ? emptyDelta(first) : emptyDelta(second)
    ));
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId('phase')).toHaveTextContent('LIVE'));
    const source = FakeEventSource.instances[0];
    await act(async () => {
      source.onmessage?.({ data: JSON.stringify({ schema_version: 'unknown' }) } as MessageEvent<string>);
    });
    await waitFor(() => expect(screen.getByTestId('cursor')).toHaveTextContent(second.cursor as string));
    expect(api.fetchSnapshot).toHaveBeenCalledTimes(2);
  });

  it('shows unavailable with no synthetic payload when the real snapshot fails', async () => {
    api.fetchSnapshot.mockReset().mockRejectedValue(new Error('Console API returned 429'));
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId('phase')).toHaveTextContent('UNAVAILABLE'));
    expect(screen.getByTestId('cursor')).toHaveTextContent('none');
    expect(screen.getByTestId('reason')).toHaveTextContent('HTTP_429');
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
