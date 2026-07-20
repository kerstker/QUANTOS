/**
 * Client for the promotion_api (port 8460) — the multi-party promotion governance surface.
 * Three externally verified OIDC subjects walk REQUESTED→REVIEWED→APPROVED→CONSUMED.
 * The access token is RFC 8705-bound to a client certificate by the backend; JavaScript
 * never reads or sends the certificate itself.
 *
 * Honest boundary: the short-lived access token is held in memory only. Authorization
 * Code + PKCE is implemented in governance-pkce.ts; external IdP and mTLS deployment
 * evidence remain M4 work.
 */

export const GOVERNANCE_BASE = (import.meta.env.VITE_GOVERNANCE_URL || '').replace(/\/$/, '');

export function governanceEnabled(): boolean {
  return GOVERNANCE_BASE.length > 0;
}

let governanceAccessToken = '';
export function getGovernanceAccessToken(): string {
  return governanceAccessToken;
}
export function setGovernanceAccessToken(token: string): void {
  governanceAccessToken = token.trim();
}
export function clearGovernanceAccessToken(): void {
  governanceAccessToken = '';
}

export interface PromotionEvidenceSummary {
  backtest_sharpe: number | null;
  paper_sharpe: number | null;
  eval_trial_count: number | null;
  eval_catalog_evidence_hash: string | null;
  eval_run_id: string;
  notes: string[];
}

export interface PromotionCandidate {
  alpha_id: string;
  state: string;
  evidence: PromotionEvidenceSummary;
  precheck_hints: string[];
}

export interface PromotionCandidatesPayload {
  registry_present: boolean;
  candidates: PromotionCandidate[];
  catalog_trial_count?: number;
  note?: string;
}

export interface PromotionRequestSummary {
  request_id: string;
  subject_id: string;
  kind: string;
  created_at: number;
  n_steps: number;
  state: string;
  last_actor: string;
  last_ts: number;
  actors: string[];
}

export interface PromotionStep {
  state: string;
  actor_id: string;
  timestamp: number;
  note: string;
}

export interface PromotionRequestDetail extends PromotionRequestSummary {
  steps: PromotionStep[];
}

export interface PromotionExecuteOutcome {
  ok: boolean;
  request_id: string;
  alpha_id: string;
  dst: string;
  burned: boolean;
  reasons: string[];
  audit_ok: boolean;
  evidence_notes: string[];
}

async function govFetch<T>(path: string, init?: { method?: 'GET' | 'POST'; body?: unknown }): Promise<T> {
  if (!GOVERNANCE_BASE) throw new Error('VITE_GOVERNANCE_URL is not configured');
  const method = init?.method ?? 'GET';
  const token = getGovernanceAccessToken().trim();
  if (!token) throw new Error('OIDC access token is not configured');
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (method === 'POST') {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(new URL(path, GOVERNANCE_BASE), {
    method, headers,
    body: init?.body != null ? JSON.stringify(init.body) : undefined,
  });
  if (!res.ok) {
    let detail = `promotion api returned ${res.status}`;
    try {
      const j: unknown = await res.json();
      const d = (j as { detail?: unknown }).detail;
      if (typeof d === 'string') detail = d;
    } catch { /* keep status text */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const P = '/api/v1/governance/promotion';

export function fetchPromotionCandidates(): Promise<PromotionCandidatesPayload> {
  return govFetch<PromotionCandidatesPayload>(`${P}/candidates`);
}

export function fetchPromotionRequests(): Promise<{ requests: PromotionRequestSummary[] }> {
  return govFetch<{ requests: PromotionRequestSummary[] }>(`${P}/requests`);
}

export function fetchPromotionRequest(requestId: string): Promise<PromotionRequestDetail> {
  return govFetch<PromotionRequestDetail>(`${P}/requests/${encodeURIComponent(requestId)}`);
}

export function openPromotionRequest(alphaId: string, note = ''): Promise<{ request_id: string; subject_id: string; state: string }> {
  return govFetch(`${P}/requests`, { method: 'POST', body: { alpha_id: alphaId, note } });
}

export function reviewPromotionRequest(requestId: string, note = ''): Promise<PromotionRequestDetail> {
  return govFetch(`${P}/requests/${encodeURIComponent(requestId)}/review`, { method: 'POST', body: { note } });
}

export function approvePromotionRequest(requestId: string, note = ''): Promise<PromotionRequestDetail> {
  return govFetch(`${P}/requests/${encodeURIComponent(requestId)}/approve`, { method: 'POST', body: { note } });
}

export function rejectPromotionRequest(requestId: string, note = ''): Promise<PromotionRequestDetail> {
  return govFetch(`${P}/requests/${encodeURIComponent(requestId)}/reject`, { method: 'POST', body: { note } });
}

export function executePromotionRequest(requestId: string): Promise<PromotionExecuteOutcome> {
  return govFetch(`${P}/requests/${encodeURIComponent(requestId)}/execute`, { method: 'POST', body: {} });
}
