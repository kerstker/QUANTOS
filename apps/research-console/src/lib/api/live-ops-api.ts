/**
 * Client for tools/live_ops_api.py (port 8440) — the read-only operations surface.
 * Extracted from LiveDesk's inline fetches (2026-07-24) so the Go-Live wizard can
 * run the same armed-preview drill through the same token mechanism.
 *
 * Governance boundary (mirrors the backend, machine-enforced there):
 * - The only mutating-ish call exposed here is POST /api/v1/liveops/preview. Its
 *   argv is server-fixed and NEVER carries `--submit`; for mainnet-only venues the
 *   backend appends `--arm-live` = ARMED PREVIEW (real account read-only passthrough,
 *   PreviewVenue structurally cannot place orders). Real submission stays human-CLI-only.
 * - Auth = X-Ops-Token matching key/live_ops_token.txt. The token is runtime input
 *   held in sessionStorage only (per-tab, cleared on close) — deliberately NOT a
 *   VITE_ build-time variable, which Vite would bake into the JS bundle in plaintext
 *   for anyone who can fetch the served assets.
 */

import { useState } from 'react';

export const OPS_URL: string = (import.meta.env.VITE_OPS_URL || 'http://127.0.0.1:8440').replace(/\/$/, '');
export const OPS_TOKEN_STORAGE_KEY = 'quantos_ops_token';

/** sessionStorage-backed ops token (shared key: entering it on Live Desk also
    unlocks the Go-Live preview drill, and vice versa). */
export function useOpsToken(): [string, (t: string) => void] {
  const [token, setTokenState] = useState<string>(() => {
    try { return sessionStorage.getItem(OPS_TOKEN_STORAGE_KEY) || ''; } catch { return ''; }
  });
  const setToken = (t: string) => {
    setTokenState(t);
    try { sessionStorage.setItem(OPS_TOKEN_STORAGE_KEY, t); } catch { /* private-mode etc */ }
  };
  return [token, setToken];
}

/** The desk/wizard default strategy per venue, mirroring the backend registries
    (tools/live_track_advance.py): driver CLI default is crowd_contra; MEXC spot is
    registered with btc_spot_trend. */
export function venueDefaultStrategy(venue: string): string {
  return venue === 'mexc' ? 'btc_spot_trend' : 'crowd_contra';
}

/* ---------- POST /api/v1/liveops/preview types -----------------------------
   Shapes verified against tools/live_ops_api.py::preview (response envelope)
   and tools/live_track_advance.py::main (status JSON, :1593-1628). ---------- */

/** One "order the pipeline WOULD have placed" (PreviewVenue.place_order record,
    serialized at tools/live_track_advance.py:1623-1625). Quantity is the order's
    venue-unit quantity as computed by the driver; there is no notional field in
    the payload — the frontend never invents one. */
export interface LiveOpsWouldSubmitOrder {
  instrument_id: string;
  /** OrderSide value: 'BUY' | 'SELL'. */
  side: string;
  quantity: number;
  /** 'MARKET' | 'LIMIT' (driver default market; limit carries limit_price). */
  order_type: string;
  limit_price: number | null;
}

/** Parsed driver status JSON for a preview run (subset the console renders;
    extra driver fields are passed through untouched by the backend). */
export interface LiveOpsPreviewStatus {
  ran_at: number;
  mode: string;
  tier: string;
  strategy: string;
  venue: string;
  armed: boolean;
  batch_skipped: string | null;
  n_submitted: number;
  n_pending_review: number;
  n_rejected_by_gate: number;
  n_dropped_by_filters: number;
  n_submission_errors: number;
  n_frozen: number;
  funding_check?: { status: string; n_frozen: number; frozen: string[] } | null;
  alerts?: string[];
  parity?: {
    paper_source: string;
    n_paper_legs: number;
    n_with_live_order: number;
    max_abs_gap_notional: number | null;
    unexpected_live_positions: string[];
  } | null;
  /** Preview-only key (absent on submit/historical snapshots). */
  would_submit?: LiveOpsWouldSubmitOrder[];
}

export interface LiveOpsPreviewResponse {
  ok: boolean;
  returncode: number | null;
  venue: string;
  strategy: string;
  mode: 'preview';
  armed: boolean;
  /** null when the driver status snapshot is missing/stale — render honestly. */
  status: LiveOpsPreviewStatus | null;
  stdout_tail: string;
}

/** HTTP failure from the ops API; `status` lets pages distinguish 401/409/400. */
export class LiveOpsHttpError extends Error {
  constructor(public readonly status: number, public readonly body: string) {
    super(`ops API HTTP ${status}: ${body.slice(0, 200)}`);
    this.name = 'LiveOpsHttpError';
  }
}

/** Run one armed-preview drill. Synchronous server-side (the driver takes
    ~10-60s doing real account reads); single-flight — 409 while another
    run/preview is in flight. 401 without a valid X-Ops-Token. */
export async function postLiveOpsPreview(
  venue: string, strategy: string, token: string,
): Promise<LiveOpsPreviewResponse> {
  const r = await fetch(`${OPS_URL}/api/v1/liveops/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Ops-Token': token } : {}) },
    body: JSON.stringify({ venue, strategy }),
  });
  if (!r.ok) throw new LiveOpsHttpError(r.status, await r.text());
  return (await r.json()) as LiveOpsPreviewResponse;
}
