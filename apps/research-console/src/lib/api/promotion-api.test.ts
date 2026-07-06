import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// promotion_api 双头认证:每个 mutating 调用都必须带 X-Gov-Token(本机共享密钥,
// sessionStorage-only)+ X-Operator(自报问责标签,非认证);GET 数据端点也要 token
// (后端全端点认证)。镜像 research-runner-api.test.ts 的写法。
describe('promotion-api headers', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('VITE_GOVERNANCE_URL', 'http://127.0.0.1:8460');
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it('getGovToken/setGovToken round-trip via sessionStorage (session-only, own key)', async () => {
    const { getGovToken, setGovToken } = await import('./promotion-api');
    expect(getGovToken()).toBe('');
    setGovToken('gov-secret');
    expect(getGovToken()).toBe('gov-secret');
    expect(sessionStorage.getItem('quantos-gov-token')).toBe('gov-secret');
  });

  it('openPromotionRequest sends BOTH X-Gov-Token and X-Operator', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ request_id: 'promo-1', subject_id: 's', state: 'REQUESTED' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { openPromotionRequest, setGovToken } = await import('./promotion-api');
    setGovToken('gov-secret');

    await openPromotionRequest('a1', 'note');

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers['X-Gov-Token']).toBe('gov-secret');
    expect(headers['X-Operator']).toBeTruthy();
    expect(init.method).toBe('POST');
  });

  it('executePromotionRequest sends BOTH headers', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, request_id: 'promo-1', alpha_id: 'a1', dst: 'APPROVED', burned: true, reasons: [], audit_ok: true, evidence_notes: [] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { executePromotionRequest, setGovToken } = await import('./promotion-api');
    setGovToken('gov-secret');

    await executePromotionRequest('promo-1');

    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers['X-Gov-Token']).toBe('gov-secret');
    expect(headers['X-Operator']).toBeTruthy();
  });

  it('GET endpoints also carry X-Gov-Token (backend authenticates queue/detail reads too)', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ requests: [] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { fetchPromotionRequests, setGovToken } = await import('./promotion-api');
    setGovToken('gov-secret');

    await fetchPromotionRequests();

    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers['X-Gov-Token']).toBe('gov-secret');
  });

  it('surfaces the backend detail message on non-2xx', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status: 409,
      json: async () => ({ detail: 'reviewer must differ from the requester' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { reviewPromotionRequest } = await import('./promotion-api');

    await expect(reviewPromotionRequest('promo-1')).rejects.toThrow('reviewer must differ');
  });

  it('throws when VITE_GOVERNANCE_URL is not configured (service is off by default)', async () => {
    vi.unstubAllEnvs();
    vi.resetModules();
    const { fetchPromotionRequests, governanceEnabled } = await import('./promotion-api');
    expect(governanceEnabled()).toBe(false);
    await expect(fetchPromotionRequests()).rejects.toThrow('VITE_GOVERNANCE_URL');
  });
});
