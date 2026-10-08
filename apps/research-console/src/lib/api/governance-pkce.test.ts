import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const REDIRECT_URI = 'http://localhost:3000/research/promotion-queue';
const ISSUER = 'https://identity.example';

function stubPkceEnvironment(): void {
  vi.stubEnv('VITE_GOVERNANCE_OIDC_ISSUER', ISSUER);
  vi.stubEnv(
    'VITE_GOVERNANCE_OIDC_AUTHORIZATION_ENDPOINT',
    `${ISSUER}/oauth2/authorize`,
  );
  vi.stubEnv('VITE_GOVERNANCE_OIDC_TOKEN_ENDPOINT', `${ISSUER}/oauth2/token`);
  vi.stubEnv('VITE_GOVERNANCE_OIDC_CLIENT_ID', 'quantos-research-console');
  vi.stubEnv('VITE_GOVERNANCE_OIDC_REDIRECT_URI', REDIRECT_URI);
  vi.stubEnv('VITE_GOVERNANCE_OIDC_AUDIENCE', 'quantos-promotion-api');
  vi.stubEnv('VITE_GOVERNANCE_OIDC_SCOPES', 'openid promotion.read');
}

function deterministicCrypto(): Crypto {
  let counter = 1;
  return {
    getRandomValues<T extends ArrayBufferView | null>(array: T): T {
      if (array instanceof Uint8Array) {
        for (let index = 0; index < array.length; index += 1) {
          array[index] = counter % 256;
          counter += 1;
        }
      }
      return array;
    },
    subtle: {
      digest: vi.fn().mockResolvedValue(new Uint8Array(32).fill(7).buffer),
    },
  } as unknown as Crypto;
}

function base64UrlJson(value: object): string {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function idToken(claims: object): string {
  return `${base64UrlJson({ alg: 'none' })}.${base64UrlJson(claims)}.signature`;
}

describe('governance Authorization Code + PKCE', () => {
  beforeEach(() => {
    vi.resetModules();
    stubPkceEnvironment();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it('builds an S256 authorization request without a client secret', async () => {
    const pkce = await import('./governance-pkce');
    const authorizationUrl = await pkce.beginGovernancePkceAuthorization({
      crypto: deterministicCrypto(),
      nowMs: () => 1_000,
      storage: sessionStorage,
    });
    const url = new URL(authorizationUrl);

    expect(url.origin + url.pathname).toBe(`${ISSUER}/oauth2/authorize`);
    expect(url.searchParams.get('response_type')).toBe('code');
    expect(url.searchParams.get('code_challenge_method')).toBe('S256');
    expect(url.searchParams.get('code_challenge')).toBeTruthy();
    expect(url.searchParams.get('state')).toBeTruthy();
    expect(url.searchParams.get('nonce')).toBeTruthy();
    expect(url.searchParams.get('client_secret')).toBeNull();
    expect(url.searchParams.get('audience')).toBe('quantos-promotion-api');

    const transaction = JSON.parse(
      sessionStorage.getItem(pkce.PKCE_TRANSACTION_STORAGE_KEY) ?? '{}',
    ) as Record<string, unknown>;
    expect(transaction.version).toBe(1);
    expect(transaction.createdAtMs).toBe(1_000);
    expect(Object.keys(transaction).sort()).toEqual([
      'clientId',
      'createdAtMs',
      'issuer',
      'nonce',
      'redirectUri',
      'state',
      'verifier',
      'version',
    ]);
    expect(JSON.stringify(transaction)).not.toContain('access_token');
  });

  it('exchanges the code once, validates OIDC binding, and keeps the token in memory', async () => {
    const pkce = await import('./governance-pkce');
    const promotion = await import('./promotion-api');
    const runtime = {
      crypto: deterministicCrypto(),
      nowMs: () => 1_000,
      storage: sessionStorage,
    };
    const authorizationUrl = new URL(
      await pkce.beginGovernancePkceAuthorization(runtime),
    );
    const state = authorizationUrl.searchParams.get('state') ?? '';
    const nonce = authorizationUrl.searchParams.get('nonce') ?? '';
    const transaction = JSON.parse(
      sessionStorage.getItem(pkce.PKCE_TRANSACTION_STORAGE_KEY) ?? '{}',
    ) as { verifier: string };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          access_token: 'signed-access-token',
          token_type: 'Bearer',
          expires_in: 300,
          id_token: idToken({
            iss: ISSUER,
            aud: 'quantos-research-console',
            nonce,
            iat: 0,
            exp: 301,
          }),
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const replaceUrl = vi.fn();

    const result = await pkce.completeGovernancePkceAuthorization({
      ...runtime,
      currentUrl: () => `${REDIRECT_URI}?code=authorization-code&state=${state}`,
      fetch: fetchMock,
      replaceUrl,
    });

    expect(result.status).toBe('AUTHENTICATED');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const request = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(request[0]).toBe(`${ISSUER}/oauth2/token`);
    expect(request[1].method).toBe('POST');
    const body = new URLSearchParams(String(request[1].body));
    expect(body.get('grant_type')).toBe('authorization_code');
    expect(body.get('code_verifier')).toBe(transaction.verifier);
    expect(body.get('client_secret')).toBeNull();
    expect(sessionStorage.getItem(pkce.PKCE_TRANSACTION_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.length).toBe(0);
    expect(promotion.getGovernanceAccessToken()).toBe('signed-access-token');
    expect(replaceUrl).toHaveBeenCalledWith('/research/promotion-queue');

    await expect(
      pkce.completeGovernancePkceAuthorization({
        ...runtime,
        currentUrl: () => `${REDIRECT_URI}?code=authorization-code&state=${state}`,
        fetch: fetchMock,
        replaceUrl,
      }),
    ).rejects.toMatchObject({ code: 'PKCE_TRANSACTION_MISSING' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('consumes the transaction before rejecting state mismatch or expiry', async () => {
    const pkce = await import('./governance-pkce');
    const runtime = {
      crypto: deterministicCrypto(),
      nowMs: () => 1_000,
      storage: sessionStorage,
    };
    await pkce.beginGovernancePkceAuthorization(runtime);
    const fetchMock = vi.fn();

    await expect(
      pkce.completeGovernancePkceAuthorization({
        ...runtime,
        currentUrl: () => `${REDIRECT_URI}?code=code&state=attacker-state`,
        fetch: fetchMock,
        replaceUrl: vi.fn(),
      }),
    ).rejects.toMatchObject({ code: 'PKCE_STATE_MISMATCH' });
    expect(sessionStorage.length).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();

    const authorizationUrl = new URL(
      await pkce.beginGovernancePkceAuthorization(runtime),
    );
    const state = authorizationUrl.searchParams.get('state') ?? '';
    await expect(
      pkce.completeGovernancePkceAuthorization({
        ...runtime,
        nowMs: () => 601_001,
        currentUrl: () => `${REDIRECT_URI}?code=code&state=${state}`,
        fetch: fetchMock,
        replaceUrl: vi.fn(),
      }),
    ).rejects.toMatchObject({ code: 'PKCE_TRANSACTION_EXPIRED' });
    expect(sessionStorage.length).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();

    const issuerBoundUrl = new URL(
      await pkce.beginGovernancePkceAuthorization(runtime),
    );
    const issuerBoundState = issuerBoundUrl.searchParams.get('state') ?? '';
    await expect(
      pkce.completeGovernancePkceAuthorization({
        ...runtime,
        currentUrl: () => (
          `${REDIRECT_URI}?code=code&state=${issuerBoundState}`
          + '&iss=https%3A%2F%2Fattacker.example'
        ),
        fetch: fetchMock,
        replaceUrl: vi.fn(),
      }),
    ).rejects.toMatchObject({ code: 'OIDC_AUTHORIZATION_ISSUER_MISMATCH' });
    expect(sessionStorage.length).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ['issuer', { iss: 'https://attacker.example' }],
    ['audience', { aud: 'other-client' }],
    ['nonce', { nonce: 'wrong' }],
  ])('rejects an ID token with wrong %s binding', async (_label, overrides) => {
    const pkce = await import('./governance-pkce');
    const runtime = {
      crypto: deterministicCrypto(),
      nowMs: () => 1_000,
      storage: sessionStorage,
    };
    const authorizationUrl = new URL(
      await pkce.beginGovernancePkceAuthorization(runtime),
    );
    const state = authorizationUrl.searchParams.get('state') ?? '';
    const nonce = authorizationUrl.searchParams.get('nonce') ?? '';
    const claims = {
      iss: ISSUER,
      aud: 'quantos-research-console',
      nonce,
      iat: 0,
      exp: 301,
      ...overrides,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          access_token: 'must-not-be-installed',
          token_type: 'Bearer',
          expires_in: 300,
          id_token: idToken(claims),
        }),
        { status: 200 },
      ),
    );

    await expect(
      pkce.completeGovernancePkceAuthorization({
        ...runtime,
        currentUrl: () => `${REDIRECT_URI}?code=code&state=${state}`,
        fetch: fetchMock,
        replaceUrl: vi.fn(),
      }),
    ).rejects.toMatchObject({ code: 'OIDC_ID_TOKEN_BINDING_INVALID' });
    const promotion = await import('./promotion-api');
    expect(promotion.getGovernanceAccessToken()).toBe('');
  });

  it('fails closed when IdP endpoints are not HTTPS', async () => {
    vi.stubEnv(
      'VITE_GOVERNANCE_OIDC_TOKEN_ENDPOINT',
      'http://identity.example/oauth2/token',
    );
    vi.resetModules();
    const pkce = await import('./governance-pkce');

    expect(pkce.governancePkceEnabled()).toBe(false);
    await expect(
      pkce.beginGovernancePkceAuthorization({
        crypto: deterministicCrypto(),
        nowMs: () => 1_000,
        storage: sessionStorage,
      }),
    ).rejects.toMatchObject({ code: 'PKCE_CONFIGURATION_INVALID' });
  });

  it('cleans callback secrets and consumes the transaction before rejecting config drift', async () => {
    const pkce = await import('./governance-pkce');
    const state = 's'.repeat(43);
    sessionStorage.setItem(pkce.PKCE_TRANSACTION_STORAGE_KEY, JSON.stringify({
      clientId: 'quantos-research-console',
      createdAtMs: 1_000,
      issuer: ISSUER,
      nonce: 'n'.repeat(43),
      redirectUri: REDIRECT_URI,
      state,
      verifier: 'v'.repeat(64),
      version: 1,
    }));
    vi.stubEnv(
      'VITE_GOVERNANCE_OIDC_TOKEN_ENDPOINT',
      'http://identity.example/oauth2/token',
    );
    vi.resetModules();
    const driftedPkce = await import('./governance-pkce');
    const replaceUrl = vi.fn();
    const fetchMock = vi.fn();

    await expect(
      driftedPkce.completeGovernancePkceAuthorization({
        currentUrl: () => (
          `${REDIRECT_URI}?code=authorization-code&state=${state}`
          + `&iss=${encodeURIComponent(ISSUER)}&keep=visible`
        ),
        fetch: fetchMock,
        nowMs: () => 1_000,
        replaceUrl,
        storage: sessionStorage,
      }),
    ).rejects.toMatchObject({ code: 'PKCE_CONFIGURATION_INVALID' });

    expect(replaceUrl).toHaveBeenCalledWith(
      '/research/promotion-queue?keep=visible',
    );
    expect(sessionStorage.length).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
