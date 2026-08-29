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

export interface RunnerHealth {
  mode: string;
  live_disallowed: boolean;
  kinds: string[];
  quarantined_kinds?: Record<string, string>;
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
  return res.json();
}

export async function triggerRun(kind: string, params: Record<string, unknown> = {}): Promise<TriggerResult> {
  if (!RESEARCH_RUNNER_BASE) throw new Error('VITE_RUNNER_URL is not configured');
  const res = await fetch(runnerUrl('/run'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Operator': getOperator(), 'X-Runner-Token': getRunnerToken() },
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

// A typed, bounded param the UI may collect for a run (mirrors the backend's small param_schema). The
// runner re-validates every value fail-closed, so these are convenience defaults/bounds, not trust.
export interface RunParamSpec {
  name: string;
  type: 'int' | 'bool';
  label: string;
  default: number | boolean;
  min?: number;
  max?: number;
}

// The runnable research kinds (mirror backend RUN_KINDS). Fast ones first for a snappy demo. A kind with a
// non-empty `params` renders typed inputs in the trigger panel; the values are passed to POST /run.
export const RUNNABLE_KINDS: ReadonlyArray<{ kind: string; label: string; params?: RunParamSpec[] }> = [
  { kind: 'data_refresh', label: '公开持仓数据刷新', params: [
    { name: 'intraday', type: 'bool', label: '日内归档', default: false },
  ] },
];
