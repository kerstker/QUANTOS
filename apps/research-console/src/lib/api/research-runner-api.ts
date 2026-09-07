/**
 * Client for the GUARDED research_runner — the only mutating surface. POST /run triggers a whitelisted
 * research run (backtest / search / data refresh). It NEVER places orders or touches the trading path
 * (the backend whitelist + governance test enforce that). Disabled unless VITE_RUNNER_URL is configured,
 * so the UI degrades to read-only browsing when the runner is not running.
 */
const SAME_ORIGIN_MARKER = '__QUANTOS_SAME_ORIGIN__';
const configuredRunnerBase = (import.meta.env.VITE_RUNNER_URL || '').replace(/\/$/, '');
export const RESEARCH_RUNNER_BASE = configuredRunnerBase === SAME_ORIGIN_MARKER
  ? (typeof window === 'undefined' ? '' : `${window.location.origin}/__runner__`)
  : configuredRunnerBase;

function runnerUrl(path: string): URL {
  return new URL(path.replace(/^\//, ''), `${RESEARCH_RUNNER_BASE}/`);
}

export function runnerEnabled(): boolean {
  return RESEARCH_RUNNER_BASE.length > 0;
}

export interface TriggerResult { run_id: string; status: string; kind: string; operator?: string; }

export type RunnerKindPurpose = 'SELECTION_BEARING' | 'DIAGNOSTIC_ONLY';

export interface RunnerKindPolicy {
  purpose: RunnerKindPurpose;
  evidence_authority: string | null;
}

export interface RunnerHealth {
  mode: 'RESEARCH_RUNNER';
  live_disallowed: true;
  kinds: string[];
  quarantined_kinds?: Record<string, string>;
  kind_policies: Record<string, RunnerKindPolicy>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function invalidRunnerHealth(reason: string): never {
  throw new Error(`RUNNER_HEALTH_INVALID:${reason}`);
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

/** Validate the unauthenticated /health body before exposing any trigger kind. */
function assertRunnerHealth(value: unknown): RunnerHealth {
  if (!isRecord(value)) invalidRunnerHealth('OBJECT_REQUIRED');
  if (value.mode !== 'RESEARCH_RUNNER') invalidRunnerHealth('MODE');
  if (value.live_disallowed !== true) invalidRunnerHealth('LIVE_BOUNDARY');
  if (!Array.isArray(value.kinds)) invalidRunnerHealth('KINDS_ARRAY');

  const kinds: string[] = [];
  const seenKinds = new Set<string>();
  for (const rawKind of value.kinds) {
    if (!nonEmptyString(rawKind) || rawKind !== rawKind.trim()) {
      invalidRunnerHealth('KIND_NAME');
    }
    if (seenKinds.has(rawKind)) invalidRunnerHealth('KIND_DUPLICATE');
    seenKinds.add(rawKind);
    kinds.push(rawKind);
  }

  if (!isRecord(value.kind_policies)) invalidRunnerHealth('KIND_POLICIES_OBJECT');
  const kindPolicies: Record<string, RunnerKindPolicy> = {};
  for (const [kind, rawPolicy] of Object.entries(value.kind_policies)) {
    if (!nonEmptyString(kind) || kind !== kind.trim() || !isRecord(rawPolicy)) {
      invalidRunnerHealth('KIND_POLICY_ENTRY');
    }
    const purpose = rawPolicy.purpose;
    if (purpose !== 'DIAGNOSTIC_ONLY' && purpose !== 'SELECTION_BEARING') {
      invalidRunnerHealth('KIND_POLICY_PURPOSE');
    }
    const authority = rawPolicy.evidence_authority;
    if (authority !== null && !nonEmptyString(authority)) {
      invalidRunnerHealth('KIND_POLICY_AUTHORITY');
    }
    if (purpose === 'DIAGNOSTIC_ONLY' && authority !== null) {
      invalidRunnerHealth('DIAGNOSTIC_AUTHORITY_CONFLICT');
    }
    if (purpose === 'SELECTION_BEARING' && authority === null) {
      invalidRunnerHealth('SELECTION_AUTHORITY_MISSING');
    }
    kindPolicies[kind] = { purpose, evidence_authority: authority };
  }
  for (const kind of kinds) {
    if (!(kind in kindPolicies)) invalidRunnerHealth('ADVERTISED_POLICY_MISSING');
  }

  let quarantinedKinds: Record<string, string> | undefined;
  if (value.quarantined_kinds !== undefined) {
    if (!isRecord(value.quarantined_kinds)) invalidRunnerHealth('QUARANTINE_OBJECT');
    quarantinedKinds = {};
    for (const [kind, reason] of Object.entries(value.quarantined_kinds)) {
      if (!nonEmptyString(kind) || kind !== kind.trim() || !nonEmptyString(reason)) {
        invalidRunnerHealth('QUARANTINE_ENTRY');
      }
      if (seenKinds.has(kind)) invalidRunnerHealth('KIND_QUARANTINE_CONFLICT');
      quarantinedKinds[kind] = reason;
    }
  }

  return {
    mode: 'RESEARCH_RUNNER',
    live_disallowed: true,
    kinds,
    ...(quarantinedKinds ? { quarantined_kinds: quarantinedKinds } : {}),
    kind_policies: kindPolicies,
  };
}

// Rank #18: the runner requires an X-Operator tag on every trigger — a SELF-ASSERTED, UNAUTHENTICATED
// accountability label (not RBAC/authn). Persisted in localStorage so the operator keeps their tag.
const OPERATOR_KEY = 'quantos.operator';
export function getOperator(): string {
  try { return (localStorage.getItem(OPERATOR_KEY) || '').trim() || 'console-operator'; }
  catch { return 'console-operator'; }
}
export function setOperator(name: string): void {
  try { localStorage.setItem(OPERATOR_KEY, name.trim()); } catch { /* ignore */ }
}

// 2026-07-06 安全审查 punch-list:后端 /run + /cancel 现在要求本机共享密钥 `X-Runner-Token`
// (镜像 live_ops_api 的 `X-Ops-Token` / LiveDesk 的 useOpsToken)。这是一个真实密钥,不是问责标签
// ——只存 sessionStorage(关闭标签页即清空),绝不进 localStorage/构建产物。
const RUNNER_TOKEN_KEY = 'quantos_runner_token';
export function getRunnerToken(): string {
  try { return sessionStorage.getItem(RUNNER_TOKEN_KEY) || ''; } catch { return ''; }
}
export function setRunnerToken(token: string): void {
  try { sessionStorage.setItem(RUNNER_TOKEN_KEY, token); } catch { /* private-mode etc */ }
}

export async function fetchRunnerHealth(signal?: AbortSignal): Promise<RunnerHealth> {
  if (!RESEARCH_RUNNER_BASE) throw new Error('VITE_RUNNER_URL is not configured');
  const res = await fetch(runnerUrl('/health'), { method: 'GET', signal });
  if (!res.ok) throw new Error(`runner returned ${res.status}`);
  return assertRunnerHealth(await res.json());
}

export async function triggerRun(kind: string, params: Record<string, unknown> = {}, invocationId?: string): Promise<TriggerResult> {
  if (!RESEARCH_RUNNER_BASE) throw new Error('VITE_RUNNER_URL is not configured');
  const res = await fetch(runnerUrl('/run'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Operator': getOperator(), 'X-Runner-Token': getRunnerToken(), ...(invocationId ? { 'Idempotency-Key': invocationId } : {}) },
    body: JSON.stringify({ kind, params }),
  });
  if (!res.ok) {
    let detail = `runner returned ${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep status */ }
    throw new Error(detail);
  }
  return res.json();
}

export async function cancelRun(runId: string): Promise<{ run_id: string; cancelled: boolean }> {
  if (!RESEARCH_RUNNER_BASE) throw new Error('VITE_RUNNER_URL is not configured');
  // 2026-07-06 修复:后端 /cancel 现在要求 X-Operator(与 /run 对称的问责标签)+ X-Runner-Token
  // (本机共享密钥),少了任一个头都会出错(400/401)。
  const res = await fetch(runnerUrl('/cancel'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Operator': getOperator(), 'X-Runner-Token': getRunnerToken() },
    body: JSON.stringify({ run_id: runId }),
  });
  if (!res.ok) throw new Error(`runner returned ${res.status}`);
  return res.json();
}
