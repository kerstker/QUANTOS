/**
 * Client for the GUARDED research_runner — the only mutating surface. POST /run triggers a whitelisted
 * research run (backtest / search / data refresh). It NEVER places orders or touches the trading path
 * (the backend whitelist + governance test enforce that). Disabled unless VITE_RUNNER_URL is configured,
 * so the UI degrades to read-only browsing when the runner is not running.
 */
export const RESEARCH_RUNNER_BASE = (import.meta.env.VITE_RUNNER_URL || '').replace(/\/$/, '');

export function runnerEnabled(): boolean {
  return RESEARCH_RUNNER_BASE.length > 0;
}

export interface TriggerResult { run_id: string; status: string; kind: string; }

export async function triggerRun(kind: string, params: Record<string, unknown> = {}): Promise<TriggerResult> {
  if (!RESEARCH_RUNNER_BASE) throw new Error('VITE_RUNNER_URL is not configured');
  const res = await fetch(new URL('/run', RESEARCH_RUNNER_BASE), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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
  const res = await fetch(new URL('/cancel', RESEARCH_RUNNER_BASE), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId }),
  });
  if (!res.ok) throw new Error(`runner returned ${res.status}`);
  return res.json();
}

// The runnable research kinds (mirror backend RUN_KINDS). Fast ones first for a snappy demo.
export const RUNNABLE_KINDS: ReadonlyArray<{ kind: string; label: string }> = [
  { kind: 'combo_book', label: '双因子组合' },
  { kind: 'oi_research', label: '持仓因子研究' },
  { kind: 'survivorship', label: '幸存者重测' },
  { kind: 'carry_strategy', label: '资金费 Carry' },
  { kind: 'factory_orthogonal', label: '自主正交发现' },
  { kind: 'factory_loop', label: 'loop-until-dry' },
];
