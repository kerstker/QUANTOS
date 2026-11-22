import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  assertConsoleSnapshot,
  assertMftExecutionDelta,
  assertMftExecutionSnapshot,
} from './research-console-api';
import { MFT_CONSOLE_SCHEMA } from '@/workbench/contracts';
import { makeMftSnapshot } from '@/workbench/__tests__/mft-fixtures';

// The frontend trusts the backend via a compile-time cast and silently falls
// back to mock on error. assertConsoleSnapshot is the runtime line of defense:
// a body without the read-only snapshot markers must throw so the page shows an
// error + honest mock fallback instead of rendering a drifted/forged payload.
describe('assertConsoleSnapshot', () => {
  const valid = { source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0', stats: {} };

  it('returns the body when both contract markers are present', () => {
    expect(assertConsoleSnapshot(valid, '/x')).toBe(valid);
  });

  it('throws when source marker is wrong (e.g. a proxy/HTML error page)', () => {
    expect(() => assertConsoleSnapshot({ read_only: true }, '/x')).toThrow(/non-snapshot/);
    expect(() => assertConsoleSnapshot({ source: 'SOMETHING_ELSE', read_only: true }, '/x')).toThrow();
  });

  it('admits a specialised source only when the caller supplies that endpoint-specific marker', () => {
    const hourly = { source: 'HOURLY_ALPHA_SQLITE_AUTHORITIES', read_only: true };
    expect(() => assertConsoleSnapshot(hourly, '/api/v1/console/hourly-alpha')).toThrow();
    expect(assertConsoleSnapshot(
      hourly,
      '/api/v1/console/hourly-alpha',
      ['HOURLY_ALPHA_SQLITE_AUTHORITIES'],
    )).toBe(hourly);
    expect(() => assertConsoleSnapshot(
      hourly,
      '/api/v1/console/mft-execution',
      ['MFT_READ_MODEL_SQLITE_AUTHORITY'],
    )).toThrow();

    const mft = { source: 'MFT_READ_MODEL_SQLITE_AUTHORITY', read_only: true };
    expect(assertConsoleSnapshot(
      mft,
      '/api/v1/console/mft-execution',
      ['MFT_READ_MODEL_SQLITE_AUTHORITY'],
    )).toBe(mft);
  });

  it('throws when read_only is not strictly true', () => {
    expect(() => assertConsoleSnapshot({ source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: 'true' }, '/x')).toThrow();
    expect(() => assertConsoleSnapshot({ source: 'BACKEND_COMPUTED_SNAPSHOT' }, '/x')).toThrow();
  });

  it('throws on null / non-object bodies', () => {
    expect(() => assertConsoleSnapshot(null, '/x')).toThrow();
    expect(() => assertConsoleSnapshot('<!doctype html>', '/x')).toThrow();
    expect(() => assertConsoleSnapshot(undefined, '/x')).toThrow();
  });

  it('names the failing path in the error for debuggability', () => {
    expect(() => assertConsoleSnapshot({}, '/api/v1/console/overview')).toThrow(/overview/);
  });
});

describe('MFT execution API guards', () => {
  it('accepts the dedicated MFT authority source without weakening generic snapshots', () => {
    const snapshot = makeMftSnapshot();
    expect(assertMftExecutionSnapshot(snapshot)).toEqual(snapshot);
    expect(() => assertConsoleSnapshot(snapshot, '/api/v1/console/mft-execution')).toThrow();
  });

  it('rejects malformed resnapshot responses and unknown delta schemas', () => {
    expect(() => assertMftExecutionDelta({
      schema_version: MFT_CONSOLE_SCHEMA,
      status: 'RESNAPSHOT_REQUIRED',
      reason: 'CURSOR_GAP',
      refetch: '/wrong',
      read_only: true,
    })).toThrow();
    expect(() => assertMftExecutionDelta({
      schema_version: 'quantos.mft-execution-console.v2',
      status: 'RESNAPSHOT_REQUIRED',
      reason: 'CURSOR_GAP',
      refetch: '/api/v1/console/mft-execution',
      read_only: true,
    })).toThrow(/UNKNOWN_SCHEMA/);
  });
});

describe('fetchResearchRunsCompare', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it('propagates REAL and TECHNICAL_VALIDATION_ONLY when all compared runs are valid', async () => {
    vi.stubEnv('VITE_API_URL', 'http://127.0.0.1:8400');
    vi.resetModules();
    const { fetchResearchRunsCompare: compare } = await import('./research-console-api');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
      const url = String(input);
      const id = url.includes('r1') ? 'r1' : 'r2';
      return new Response(JSON.stringify({
        source: 'BACKEND_COMPUTED_SNAPSHOT',
        read_only: true,
        api_version: '0.1.0',
        snapshot_status: 'REAL',
        evidence_status: 'TECHNICAL_VALIDATION_ONLY',
        run: { run_id: id, kind: 'btc_factory', status: 'done', created_ts: 1, params: {} },
      }), { status: 200 });
    });

    const result = await compare(['r1', 'r2']);
    expect(result.runs.map(r => r.run_id)).toEqual(['r1', 'r2']);
    expect(result.snapshot_status).toBe('REAL');
    expect(result.evidence_status).toBe('TECHNICAL_VALIDATION_ONLY');
    expect(result.invalidation_code).toBeUndefined();
    expect(result.authoritative).toBe(false);
  });

  it('marks compare payload DATA_BLOCKED when any run carries an invalidation_code', async () => {
    vi.stubEnv('VITE_API_URL', 'http://127.0.0.1:8400');
    vi.resetModules();
    const { fetchResearchRunsCompare: compare } = await import('./research-console-api');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
      const url = String(input);
      const invalidated = url.includes('legacy');
      return new Response(JSON.stringify({
        source: 'BACKEND_COMPUTED_SNAPSHOT',
        read_only: true,
        api_version: '0.1.0',
        snapshot_status: invalidated ? 'DATA_BLOCKED' : 'REAL',
        evidence_status: invalidated ? 'DIAGNOSTIC_ONLY' : 'TECHNICAL_VALIDATION_ONLY',
        invalidation_code: invalidated ? 'INVALIDATED_PERP_LEDGER_V1' : undefined,
        run: { run_id: invalidated ? 'legacy' : 'fresh', kind: 'btc_factory', status: 'done', created_ts: 1, params: {} },
      }), { status: 200 });
    });

    const result = await compare(['fresh', 'legacy']);
    expect(result.snapshot_status).toBe('DATA_BLOCKED');
    expect(result.invalidation_code).toBe('INVALIDATED_PERP_LEDGER_V1');
    expect(result.evidence_status).toBe('DIAGNOSTIC_ONLY');
  });
});

