import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useResource } from './data';
afterEach(() => vi.useRealTimers());
describe('Studio resource lifecycle', () => {
  it('does not display an old route response after navigation', async () => {
    let resolveOld!: (value: string) => void;
    const first = new Promise<string>(resolve => { resolveOld = resolve; });
    const { result, rerender } = renderHook(({ id }) => useResource(id, () => id === 'a' ? first : Promise.resolve('new data')), { initialProps: { id: 'a' } });
    rerender({ id: 'b' });
    await waitFor(() => expect(result.current.data).toBe('new data'));
    await act(async () => { resolveOld('old data'); await first; });
    expect(result.current.data).toBe('new data');
  });
  it('aborts in-flight work on unmount', () => {
    let signal: AbortSignal | undefined;
    const { unmount } = renderHook(() => useResource('x', s => { signal = s; return new Promise(() => { }); }));
    unmount();
    expect(signal?.aborted).toBe(true);
  });
  it('turns a stalled endpoint into a recoverable timeout', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useResource('slow', () => new Promise(() => { })));
    await act(async () => { await vi.advanceTimersByTimeAsync(15001); });
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toContain('超时');
  });
});
