import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// POST /api/v1/liveops/preview client contract: fixed {venue, strategy} body
// (no flag injection possible from the browser), X-Ops-Token auth from
// sessionStorage (never a VITE_ build-time variable), typed HTTP errors so
// pages can distinguish 401 (token) / 409 (single-flight) / 400 (unknown).
describe('live-ops-api preview client', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('VITE_OPS_URL', 'http://127.0.0.1:8440');
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it('posts exactly {venue, strategy} with the ops token header', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true, returncode: 0, venue: 'binance_mainnet', strategy: 'crowd_contra',
        mode: 'preview', armed: true,
        status: {
          ran_at: 1784900000000, mode: 'preview', tier: 'AUTO', strategy: 'crowd_contra',
          venue: 'binance_mainnet', armed: true, batch_skipped: null,
          n_submitted: 0, n_pending_review: 0, n_rejected_by_gate: 0,
          n_dropped_by_filters: 0, n_submission_errors: 0, n_frozen: 0,
          would_submit: [
            { instrument_id: 'BTCUSDT', side: 'BUY', quantity: 0.001, order_type: 'MARKET', limit_price: null },
          ],
        },
        stdout_tail: '=== live_track (PREVIEW) ...',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { postLiveOpsPreview } = await import('./live-ops-api');

    const res = await postLiveOpsPreview('binance_mainnet', 'crowd_contra', 'tok-123');

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://127.0.0.1:8440/api/v1/liveops/preview');
    expect(init.method).toBe('POST');
    const headers = init.headers as Record<string, string>;
    expect(headers['X-Ops-Token']).toBe('tok-123');
    // 治理形状:body 只有 venue/strategy —— 浏览器侧绝无 submit/arm/refresh 注入面。
    expect(JSON.parse(init.body as string)).toEqual({ venue: 'binance_mainnet', strategy: 'crowd_contra' });
    expect(res.armed).toBe(true);
    expect(res.status?.would_submit).toHaveLength(1);
    expect(res.status?.would_submit?.[0].limit_price).toBeNull();
  });

  it('omits the token header when no token is stored', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal('fetch', fetchMock);
    const { postLiveOpsPreview } = await import('./live-ops-api');
    await postLiveOpsPreview('binance', 'crowd_contra', '');
    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers['X-Ops-Token']).toBeUndefined();
  });

  it('throws LiveOpsHttpError carrying the status for 401/409', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 401, text: async () => 'invalid ops token' })
      .mockResolvedValueOnce({ ok: false, status: 409, text: async () => 'single-flight' });
    vi.stubGlobal('fetch', fetchMock);
    const { postLiveOpsPreview, LiveOpsHttpError } = await import('./live-ops-api');

    const e1 = await postLiveOpsPreview('binance', 'crowd_contra', 'bad').catch(e => e);
    expect(e1).toBeInstanceOf(LiveOpsHttpError);
    expect((e1 as InstanceType<typeof LiveOpsHttpError>).status).toBe(401);

    const e2 = await postLiveOpsPreview('binance', 'crowd_contra', 'tok').catch(e => e);
    expect((e2 as InstanceType<typeof LiveOpsHttpError>).status).toBe(409);
  });

  it('venueDefaultStrategy mirrors the backend registries', async () => {
    const { venueDefaultStrategy } = await import('./live-ops-api');
    expect(venueDefaultStrategy('mexc')).toBe('btc_spot_trend');
    expect(venueDefaultStrategy('binance')).toBe('crowd_contra');
    expect(venueDefaultStrategy('binance_mainnet')).toBe('crowd_contra');
  });
});
