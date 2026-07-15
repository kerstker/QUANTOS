import {
  clearGovernanceAccessToken,
  setGovernanceAccessToken,
} from './promotion-api';

const PKCE_VERSION = 1;
const MAX_TRANSACTION_AGE_MS = 10 * 60 * 1000;
const MAX_TOKEN_LIFETIME_SECONDS = 10 * 60;
const CALLBACK_PARAMETERS = [
  'code',
  'state',
  'error',
  'error_description',
  'error_uri',
  'iss',
  'session_state',
] as const;

export const PKCE_TRANSACTION_STORAGE_KEY = 'quantos:governance-pkce:v1';

export interface GovernancePkceConfig {
  issuer: string;
  authorizationEndpoint: string;
  tokenEndpoint: string;
  clientId: string;
  redirectUri: string;
  audience: string;
  scopes: string[];
}

interface PkceTransactionV1 {
  version: 1;
  state: string;
  nonce: string;
  verifier: string;
  createdAtMs: number;
  redirectUri: string;
  issuer: string;
  clientId: string;
}

export interface GovernancePkceRuntime {
  crypto?: Crypto;
  storage?: Storage;
  nowMs?: () => number;
  fetch?: typeof fetch;
  currentUrl?: () => string;
  replaceUrl?: (relativeUrl: string) => void;
}

export type GovernancePkceCompletion =
  | { status: 'NONE' }
  | { status: 'AUTHENTICATED'; expiresIn: number };

export class GovernancePkceError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = 'GovernancePkceError';
    this.code = code;
  }
}

function requireValue(value: string | undefined): string {
  const normalized = value?.trim() ?? '';
  if (!normalized) throw new GovernancePkceError('PKCE_CONFIGURATION_INVALID');
  return normalized;
}

function isLocalDevelopmentUrl(url: URL): boolean {
  return url.protocol === 'http:'
    && (url.hostname === 'localhost' || url.hostname === '127.0.0.1');
}

function parsePkceConfig(): GovernancePkceConfig {
  const issuer = requireValue(import.meta.env.VITE_GOVERNANCE_OIDC_ISSUER);
  const authorizationEndpoint = requireValue(
    import.meta.env.VITE_GOVERNANCE_OIDC_AUTHORIZATION_ENDPOINT,
  );
  const tokenEndpoint = requireValue(
    import.meta.env.VITE_GOVERNANCE_OIDC_TOKEN_ENDPOINT,
  );
  const clientId = requireValue(import.meta.env.VITE_GOVERNANCE_OIDC_CLIENT_ID);
  const redirectUri = requireValue(import.meta.env.VITE_GOVERNANCE_OIDC_REDIRECT_URI);
  const audience = requireValue(import.meta.env.VITE_GOVERNANCE_OIDC_AUDIENCE);
  const scopeValue = requireValue(import.meta.env.VITE_GOVERNANCE_OIDC_SCOPES);
  const scopes = [...new Set(scopeValue.split(/\s+/).filter(Boolean))];
  if (!scopes.includes('openid')) {
    throw new GovernancePkceError('PKCE_CONFIGURATION_INVALID');
  }

  let issuerUrl: URL;
  let authorizationUrl: URL;
  let tokenUrl: URL;
  let redirectUrl: URL;
  try {
    issuerUrl = new URL(issuer);
    authorizationUrl = new URL(authorizationEndpoint);
    tokenUrl = new URL(tokenEndpoint);
    redirectUrl = new URL(redirectUri);
  } catch {
    throw new GovernancePkceError('PKCE_CONFIGURATION_INVALID');
  }
  if (
    issuerUrl.protocol !== 'https:'
    || authorizationUrl.protocol !== 'https:'
    || tokenUrl.protocol !== 'https:'
    || authorizationUrl.origin !== issuerUrl.origin
    || tokenUrl.origin !== issuerUrl.origin
    || issuerUrl.search
    || issuerUrl.hash
  ) {
    throw new GovernancePkceError('PKCE_CONFIGURATION_INVALID');
  }
  const currentOrigin = window.location.origin;
  if (
    redirectUrl.origin !== currentOrigin
    || (redirectUrl.protocol !== 'https:' && !isLocalDevelopmentUrl(redirectUrl))
  ) {
    throw new GovernancePkceError('PKCE_CONFIGURATION_INVALID');
  }
  return {
    issuer,
    authorizationEndpoint,
    tokenEndpoint,
    clientId,
    redirectUri,
    audience,
    scopes,
  };
}

export function getGovernancePkceConfig(): GovernancePkceConfig | null {
  try {
    return parsePkceConfig();
  } catch {
    return null;
  }
}

export function governancePkceEnabled(): boolean {
  return getGovernancePkceConfig() !== null;
}

function browserNowMs(): number {
  return Math.floor(performance.timeOrigin + performance.now());
}

function resolveRuntime(runtime: GovernancePkceRuntime) {
  return {
    crypto: runtime.crypto ?? globalThis.crypto,
    storage: runtime.storage ?? window.sessionStorage,
    nowMs: runtime.nowMs ?? browserNowMs,
    fetch: runtime.fetch ?? globalThis.fetch,
    currentUrl: runtime.currentUrl ?? (() => window.location.href),
    replaceUrl: runtime.replaceUrl
      ?? ((relativeUrl: string) => window.history.replaceState(null, '', relativeUrl)),
  };
}

function base64Url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function randomBase64Url(byteLength: number, cryptoValue: Crypto): string {
  const bytes = new Uint8Array(byteLength);
  cryptoValue.getRandomValues(bytes);
  return base64Url(bytes);
}

async function codeChallenge(verifier: string, cryptoValue: Crypto): Promise<string> {
  const digest = await cryptoValue.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(verifier),
  );
  return base64Url(new Uint8Array(digest));
}

function storeTransaction(storage: Storage, transaction: PkceTransactionV1): void {
  try {
    storage.setItem(PKCE_TRANSACTION_STORAGE_KEY, JSON.stringify(transaction));
  } catch {
    throw new GovernancePkceError('PKCE_STORAGE_UNAVAILABLE');
  }
}

function isTransaction(value: unknown): value is PkceTransactionV1 {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const candidate = value as Record<string, unknown>;
  const expectedKeys = [
    'clientId',
    'createdAtMs',
    'issuer',
    'nonce',
    'redirectUri',
    'state',
    'verifier',
    'version',
  ];
  if (Object.keys(candidate).sort().join('|') !== expectedKeys.join('|')) return false;
  return candidate.version === PKCE_VERSION
    && typeof candidate.state === 'string'
    && candidate.state.length >= 32
    && typeof candidate.nonce === 'string'
    && candidate.nonce.length >= 32
    && typeof candidate.verifier === 'string'
    && candidate.verifier.length >= 43
    && typeof candidate.createdAtMs === 'number'
    && Number.isSafeInteger(candidate.createdAtMs)
    && typeof candidate.redirectUri === 'string'
    && typeof candidate.issuer === 'string'
    && typeof candidate.clientId === 'string';
}

function consumeTransaction(storage: Storage): PkceTransactionV1 {
  let serialized: string | null;
  try {
    serialized = storage.getItem(PKCE_TRANSACTION_STORAGE_KEY);
    storage.removeItem(PKCE_TRANSACTION_STORAGE_KEY);
  } catch {
    throw new GovernancePkceError('PKCE_STORAGE_UNAVAILABLE');
  }
  if (!serialized) throw new GovernancePkceError('PKCE_TRANSACTION_MISSING');
  try {
    const parsed: unknown = JSON.parse(serialized);
    if (!isTransaction(parsed)) {
      throw new GovernancePkceError('PKCE_TRANSACTION_INVALID');
    }
    return parsed;
  } catch (error) {
    if (error instanceof GovernancePkceError) throw error;
    throw new GovernancePkceError('PKCE_TRANSACTION_INVALID');
  }
}

function cleanCallbackUrl(url: URL, replaceUrl: (relativeUrl: string) => void): void {
  for (const parameter of CALLBACK_PARAMETERS) url.searchParams.delete(parameter);
  const query = url.searchParams.toString();
  replaceUrl(`${url.pathname}${query ? `?${query}` : ''}${url.hash}`);
}

function decodeIdTokenClaims(idToken: string): Record<string, unknown> {
  const parts = idToken.split('.');
  if (parts.length !== 3) throw new GovernancePkceError('OIDC_ID_TOKEN_INVALID');
  try {
    const encoded = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '=');
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new GovernancePkceError('OIDC_ID_TOKEN_INVALID');
    }
    return parsed as Record<string, unknown>;
  } catch (error) {
    if (error instanceof GovernancePkceError) throw error;
    throw new GovernancePkceError('OIDC_ID_TOKEN_INVALID');
  }
}

function validateIdTokenBinding(
  idToken: string,
  transaction: PkceTransactionV1,
  config: GovernancePkceConfig,
  nowMs: number,
): void {
  const claims = decodeIdTokenClaims(idToken);
  const audience = claims.aud;
  const audienceMatches = audience === config.clientId
    || (Array.isArray(audience) && audience.includes(config.clientId));
  const nowSeconds = Math.floor(nowMs / 1000);
  if (
    claims.iss !== config.issuer
    || !audienceMatches
    || claims.nonce !== transaction.nonce
    || typeof claims.iat !== 'number'
    || !Number.isSafeInteger(claims.iat)
    || claims.iat > nowSeconds + 60
    || claims.iat < nowSeconds - MAX_TOKEN_LIFETIME_SECONDS
    || typeof claims.exp !== 'number'
    || !Number.isSafeInteger(claims.exp)
    || claims.exp <= nowSeconds
    || claims.exp > nowSeconds + MAX_TOKEN_LIFETIME_SECONDS
  ) {
    throw new GovernancePkceError('OIDC_ID_TOKEN_BINDING_INVALID');
  }
}

export async function beginGovernancePkceAuthorization(
  runtimeOverrides: GovernancePkceRuntime = {},
): Promise<string> {
  const config = parsePkceConfig();
  const runtime = resolveRuntime(runtimeOverrides);
  if (!runtime.crypto?.getRandomValues || !runtime.crypto.subtle) {
    throw new GovernancePkceError('PKCE_CRYPTO_UNAVAILABLE');
  }
  clearGovernanceAccessToken();
  const transaction: PkceTransactionV1 = {
    version: 1,
    state: randomBase64Url(32, runtime.crypto),
    nonce: randomBase64Url(32, runtime.crypto),
    verifier: randomBase64Url(64, runtime.crypto),
    createdAtMs: runtime.nowMs(),
    redirectUri: config.redirectUri,
    issuer: config.issuer,
    clientId: config.clientId,
  };
  const challenge = await codeChallenge(transaction.verifier, runtime.crypto);
  storeTransaction(runtime.storage, transaction);
  const authorizationUrl = new URL(config.authorizationEndpoint);
  authorizationUrl.searchParams.set('response_type', 'code');
  authorizationUrl.searchParams.set('response_mode', 'query');
  authorizationUrl.searchParams.set('client_id', config.clientId);
  authorizationUrl.searchParams.set('redirect_uri', config.redirectUri);
  authorizationUrl.searchParams.set('scope', config.scopes.join(' '));
  authorizationUrl.searchParams.set('audience', config.audience);
  authorizationUrl.searchParams.set('state', transaction.state);
  authorizationUrl.searchParams.set('nonce', transaction.nonce);
  authorizationUrl.searchParams.set('code_challenge', challenge);
  authorizationUrl.searchParams.set('code_challenge_method', 'S256');
  return authorizationUrl.toString();
}

export async function completeGovernancePkceAuthorization(
  runtimeOverrides: GovernancePkceRuntime = {},
): Promise<GovernancePkceCompletion> {
  const runtime = resolveRuntime(runtimeOverrides);
  const callbackUrl = new URL(runtime.currentUrl());
  const hasCallback = CALLBACK_PARAMETERS.some(parameter => (
    callbackUrl.searchParams.has(parameter)
  ));
  if (!hasCallback) return { status: 'NONE' };
  const returnedState = callbackUrl.searchParams.get('state') ?? '';
  const returnedIssuer = callbackUrl.searchParams.get('iss') ?? '';
  const authorizationCode = callbackUrl.searchParams.get('code') ?? '';
  const authorizationFailed = callbackUrl.searchParams.has('error');
  cleanCallbackUrl(callbackUrl, runtime.replaceUrl);
  clearGovernanceAccessToken();
  const transaction = consumeTransaction(runtime.storage);
  const config = parsePkceConfig();
  const nowMs = runtime.nowMs();
  if (
    transaction.createdAtMs > nowMs
    || nowMs - transaction.createdAtMs > MAX_TRANSACTION_AGE_MS
  ) {
    throw new GovernancePkceError('PKCE_TRANSACTION_EXPIRED');
  }
  if (
    transaction.redirectUri !== config.redirectUri
    || transaction.issuer !== config.issuer
    || transaction.clientId !== config.clientId
  ) {
    throw new GovernancePkceError('PKCE_CONFIGURATION_CHANGED');
  }
  if (!returnedState || returnedState !== transaction.state) {
    throw new GovernancePkceError('PKCE_STATE_MISMATCH');
  }
  if (returnedIssuer && returnedIssuer !== config.issuer) {
    throw new GovernancePkceError('OIDC_AUTHORIZATION_ISSUER_MISMATCH');
  }
  if (authorizationFailed) {
    throw new GovernancePkceError('OIDC_AUTHORIZATION_FAILED');
  }
  if (!authorizationCode || authorizationCode.length > 4096) {
    throw new GovernancePkceError('OIDC_AUTHORIZATION_CODE_INVALID');
  }
  const form = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: config.clientId,
    code: authorizationCode,
    redirect_uri: config.redirectUri,
    code_verifier: transaction.verifier,
  });
  let response: Response;
  try {
    response = await runtime.fetch(config.tokenEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
    });
  } catch {
    throw new GovernancePkceError('OIDC_TOKEN_EXCHANGE_FAILED');
  }
  if (!response.ok) throw new GovernancePkceError('OIDC_TOKEN_EXCHANGE_FAILED');
  let tokenResponse: unknown;
  try {
    tokenResponse = await response.json();
  } catch {
    throw new GovernancePkceError('OIDC_TOKEN_RESPONSE_INVALID');
  }
  if (!tokenResponse || typeof tokenResponse !== 'object' || Array.isArray(tokenResponse)) {
    throw new GovernancePkceError('OIDC_TOKEN_RESPONSE_INVALID');
  }
  const token = tokenResponse as Record<string, unknown>;
  if (
    typeof token.access_token !== 'string'
    || !token.access_token
    || token.access_token.length > 65_536
    || typeof token.token_type !== 'string'
    || token.token_type.toLowerCase() !== 'bearer'
    || typeof token.expires_in !== 'number'
    || !Number.isSafeInteger(token.expires_in)
    || token.expires_in <= 0
    || token.expires_in > MAX_TOKEN_LIFETIME_SECONDS
    || typeof token.id_token !== 'string'
    || !token.id_token
  ) {
    throw new GovernancePkceError('OIDC_TOKEN_RESPONSE_INVALID');
  }
  validateIdTokenBinding(token.id_token, transaction, config, nowMs);
  setGovernanceAccessToken(token.access_token);
  return { status: 'AUTHENTICATED', expiresIn: token.expires_in };
}

export function clearGovernancePkceSession(storage: Storage = window.sessionStorage): void {
  clearGovernanceAccessToken();
  try {
    storage.removeItem(PKCE_TRANSACTION_STORAGE_KEY);
  } catch {
    // The access token is already cleared; storage failure cannot restore authority.
  }
}
