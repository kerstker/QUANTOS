import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const command = {
  idempotency_key: '11111111-1111-4111-8111-111111111111',
  target_version: 7,
  business_reason: 'review alpha evidence',
  expected_impact: {
    scope: 'RESEARCH_PROMOTION_ONLY' as const,
    target_id: 'a1',
    approval_state: 'REQUESTED',
    research_state: 'UNCHANGED',
    live_trading_authorized: false as const,
    order_submission_allowed: false as const,
    capital_scaling_allowed: false as const,
    risk_limit_change: false as const,
    kill_switch_reset: false as const,
  },
  risk_diff: ['approval_state: ABSENT -> REQUESTED'],
  preview_hash: 'a'.repeat(64),
};

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

    await openPromotionRequest('a1', command);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer signed-oidc-token');
    expect(headers['X-Gov-Token']).toBeUndefined();
    expect(headers['X-Operator']).toBeUndefined();
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ alpha_id: 'a1', ...command });
  });

  it('executePromotionRequest sends the same verified identity protocol', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, request_id: 'promo-1', alpha_id: 'a1', dst: 'APPROVED', burned: true, reasons: [], audit_ok: true, evidence_notes: [] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { executePromotionRequest, setGovernanceAccessToken } = await import('./promotion-api');
    setGovernanceAccessToken('signed-oidc-token');

    await executePromotionRequest('promo-1', command);

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

    await expect(reviewPromotionRequest('promo-1', command)).rejects.toThrow('reviewer must differ');
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
