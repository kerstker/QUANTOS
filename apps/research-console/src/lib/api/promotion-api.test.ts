import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Strong identity: every request carries a short-lived OIDC bearer token. The
// actor comes from verified claims; the client must never send an operator label.
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

  it('governance access token is memory-only', async () => {
    const {
      clearGovernanceAccessToken,
      getGovernanceAccessToken,
      setGovernanceAccessToken,
    } = await import('./promotion-api');
    expect(getGovernanceAccessToken()).toBe('');
    setGovernanceAccessToken('signed-oidc-token');
    expect(getGovernanceAccessToken()).toBe('signed-oidc-token');
    expect(sessionStorage.length).toBe(0);
    expect(setGovernanceAccessToken.toString()).not.toContain('localStorage');
    expect(setGovernanceAccessToken.toString()).not.toContain('sessionStorage');
    clearGovernanceAccessToken();
    expect(getGovernanceAccessToken()).toBe('');
  });

  it('openPromotionRequest sends only standard Bearer identity', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ request_id: 'promo-1', subject_id: 's', state: 'REQUESTED' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { openPromotionRequest, setGovernanceAccessToken } = await import('./promotion-api');
    setGovernanceAccessToken('signed-oidc-token');

    await openPromotionRequest('a1', 'note');

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer signed-oidc-token');
    expect(headers['X-Gov-Token']).toBeUndefined();
    expect(headers['X-Operator']).toBeUndefined();
    expect(init.method).toBe('POST');
  });

  it('executePromotionRequest sends the same verified identity protocol', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, request_id: 'promo-1', alpha_id: 'a1', dst: 'APPROVED', burned: true, reasons: [], audit_ok: true, evidence_notes: [] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { executePromotionRequest, setGovernanceAccessToken } = await import('./promotion-api');
    setGovernanceAccessToken('signed-oidc-token');

    await executePromotionRequest('promo-1');

    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer signed-oidc-token');
    expect(headers['X-Operator']).toBeUndefined();
  });

  it('GET endpoints also carry Bearer identity', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ requests: [] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { fetchPromotionRequests, setGovernanceAccessToken } = await import('./promotion-api');
    setGovernanceAccessToken('signed-oidc-token');

    await fetchPromotionRequests();

    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer signed-oidc-token');
  });

  it('surfaces the backend detail message on non-2xx', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status: 409,
      json: async () => ({ detail: 'reviewer must differ from the requester' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { reviewPromotionRequest, setGovernanceAccessToken } = await import('./promotion-api');
    setGovernanceAccessToken('signed-oidc-token');

    await expect(reviewPromotionRequest('promo-1')).rejects.toThrow('reviewer must differ');
  });

  it('fails before fetch when no OIDC access token is present', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { fetchPromotionRequests } = await import('./promotion-api');
    await expect(fetchPromotionRequests()).rejects.toThrow('OIDC access token');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('throws when VITE_GOVERNANCE_URL is not configured (service is off by default)', async () => {
    vi.unstubAllEnvs();
    vi.resetModules();
    const { fetchPromotionRequests, governanceEnabled } = await import('./promotion-api');
    expect(governanceEnabled()).toBe(false);
    await expect(fetchPromotionRequests()).rejects.toThrow('VITE_GOVERNANCE_URL');
  });
});
