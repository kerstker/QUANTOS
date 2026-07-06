/**
 * Client for the promotion_api (port 8460) — the multi-party promotion governance surface.
 * Three DISTINCT self-asserted operators walk REQUESTED→REVIEWED→APPROVED→CONSUMED; the approver
 * executes their own approval. Disabled unless VITE_GOVERNANCE_URL is configured, so the UI
 * degrades to an honest offline explanation when the service is not running (it is NOT started
 * by default — see the threat-model note in services/governance/promotion_api/main.py).
 *
 * 诚实边界(与后端 docstring 一字对齐):X-Operator 是自报问责标签、不是身份认证;持
 * gov token 的任何本机进程(含 AI agent)都能用三个自报标签走完三方批准。真身份认证 = Phase 14。
 */
import { getOperator } from './research-runner-api';

export const GOVERNANCE_BASE = (import.meta.env.VITE_GOVERNANCE_URL || '').replace(/\/$/, '');

export function governanceEnabled(): boolean {
  return GOVERNANCE_BASE.length > 0;
}

// 本机共享密钥 X-Gov-Token(key/governance_token.txt,首次启动 promotion_api 时生成)。
// 只存 sessionStorage(关标签页即清空),绝不进 localStorage/构建产物 —— 逐字镜像
// research-runner-api 的 runner-token 模式,独立 key(交易台/runner/晋级三种权限分离)。
const GOV_TOKEN_KEY = 'quantos-gov-token';
export function getGovToken(): string {
  try { return sessionStorage.getItem(GOV_TOKEN_KEY) || ''; } catch { return ''; }
}
export function setGovToken(token: string): void {
  try { sessionStorage.setItem(GOV_TOKEN_KEY, token); } catch { /* private-mode etc */ }
}

export interface PromotionEvidenceSummary {
  backtest_sharpe: number | null;
  paper_sharpe: number | null;
  eval_trial_count: number | null;
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
  // GET 数据端点同样要求 X-Gov-Token(后端全端点认证);X-Operator 只在 mutating 调用上有意义。
  const headers: Record<string, string> = { 'X-Gov-Token': getGovToken() };
  if (method === 'POST') {
    headers['Content-Type'] = 'application/json';
    headers['X-Operator'] = getOperator();
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
