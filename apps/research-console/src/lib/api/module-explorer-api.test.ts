import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CONSOLE_MODULES } from '@/workbench/module-registry';

describe('module explorer endpoint-specific source guards', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('VITE_API_URL', 'http://127.0.0.1:8400');
    vi.stubEnv('VITE_MOCK_MODE', 'false');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it.each([
    ['/api/v1/console/hourly-alpha', 'HOURLY_ALPHA_SQLITE_AUTHORITIES'],
    ['/api/v1/console/mft-execution', 'MFT_READ_MODEL_SQLITE_AUTHORITY'],
  ])('accepts the real %s source marker declared by its registry entry', async (path, source) => {
    const body = { source, read_only: true, api_version: 'v1' };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => body,
    }));
    const { fetchConsoleModuleSnapshot } = await import('./research-console-api');

    await expect(fetchConsoleModuleSnapshot(path, CONSOLE_MODULES)).resolves.toBe(body);
  });

  it('does not admit a specialised marker on any other registered endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        source: 'HOURLY_ALPHA_SQLITE_AUTHORITIES', read_only: true, api_version: 'v1',
      }),
    }));
    const { fetchConsoleModuleSnapshot } = await import('./research-console-api');

    await expect(fetchConsoleModuleSnapshot(
      '/api/v1/console/overview', CONSOLE_MODULES,
    )).rejects.toThrow(/non-snapshot/);
  });
});
