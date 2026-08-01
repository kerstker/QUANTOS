import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useConsoleSnapshot } from './useConsoleSnapshot';

vi.mock('@/lib/api/research-console-api', () => ({
  RESEARCH_CONSOLE_API_BASE: 'http://127.0.0.1:8400',
  isResearchConsoleApiEnabled: () => true,
  assertConsoleSnapshot: <T,>(body: unknown, path: string): T => {
    if (
      typeof body !== 'object'
      || body === null
      || (body as Record<string, unknown>).source !== 'BACKEND_COMPUTED_SNAPSHOT'
      || (body as Record<string, unknown>).read_only !== true
    ) {
      throw new Error(`Console API ${path} returned a non-snapshot body`);
    }
    return body as T;
  },
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

  close() {
    this.closed = true;
  }
}

function Harness({ fetcher }: { fetcher: (signal: AbortSignal) => Promise<{ value: string }> }) {
  const result = useConsoleSnapshot(fetcher, [], {
    streamPath: '/api/v1/console/stream/dry-run',
  });
  return (
    <div>
      <span data-testid="state">{result.apiState}</span>
      <span data-testid="value">{result.payload?.value ?? 'none'}</span>
      <span data-testid="error">{result.error ?? 'none'}</span>
    </div>
  );
}

describe('useConsoleSnapshot event stream transport', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
  });

  it('uses SSE, retains the verified snapshot during reconnect, and closes cleanly', () => {
    const fetcher = vi.fn(async () => ({ value: 'fetch' }));
    const view = render(<Harness fetcher={fetcher} />);
    const source = FakeEventSource.instances[0];

    expect(fetcher).not.toHaveBeenCalled();
    expect(source.url).toBe('http://127.0.0.1:8400/api/v1/console/stream/dry-run');
    expect(screen.getByTestId('state')).toHaveTextContent('loading');

    act(() => {
      source.onmessage?.({
        data: JSON.stringify({
          source: 'BACKEND_COMPUTED_SNAPSHOT',
          read_only: true,
          value: 'live',
        }),
      } as MessageEvent<string>);
    });
    expect(screen.getByTestId('state')).toHaveTextContent('connected');
    expect(screen.getByTestId('value')).toHaveTextContent('live');

    act(() => source.onerror?.(new Event('error')));
    expect(screen.getByTestId('state')).toHaveTextContent('loading');
    expect(screen.getByTestId('value')).toHaveTextContent('live');
    expect(screen.getByTestId('error')).toHaveTextContent('reconnecting');

    view.unmount();
    expect(source.closed).toBe(true);
  });

  it('fails closed when the event body lacks snapshot markers', () => {
    render(<Harness fetcher={vi.fn(async () => ({ value: 'fetch' }))} />);
    const source = FakeEventSource.instances[0];

    act(() => {
      source.onmessage?.({ data: JSON.stringify({ value: 'unsafe' }) } as MessageEvent<string>);
    });

    expect(screen.getByTestId('state')).toHaveTextContent('error');
    expect(screen.getByTestId('value')).toHaveTextContent('none');
    expect(source.closed).toBe(true);
  });
});
