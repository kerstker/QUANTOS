import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// 2026-07-06 安全审查 punch-list 续:后端 /cancel 在 878a751 里改成要求 X-Operator
// (与 /run 的问责要求对称),但前端 cancelRun() 从未跟着更新——RunDetail 页的撤销按钮
// 会一直收到 400,这是一个真实的、由早前那次修复自身引入的回归。
describe('research-runner-api cancelRun', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('VITE_RUNNER_URL', 'http://127.0.0.1:8420');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('sends the same X-Operator accountability header triggerRun sends', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ run_id: 'r1', cancelled: true }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { cancelRun } = await import('./research-runner-api');

    await cancelRun('r1');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers['X-Operator']).toBeTruthy();
  });
});

// --- 2026-07-06 安全审查 punch-list:后端 /run + /cancel 现在要求 X-Runner-Token(本机共享密钥,
// 镜像 live_ops_api),前端必须带上——否则两个按钮都会 401。
describe('research-runner-api runner token', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('VITE_RUNNER_URL', 'http://127.0.0.1:8420');
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it('getRunnerToken/setRunnerToken round-trip via sessionStorage (session-only, mirrors ops token)', async () => {
    const { getRunnerToken, setRunnerToken } = await import('./research-runner-api');
    expect(getRunnerToken()).toBe('');
    setRunnerToken('secret-abc');
    expect(getRunnerToken()).toBe('secret-abc');
  });

  it('triggerRun sends the stored X-Runner-Token header', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ run_id: 'r1', status: 'queued', kind: 'data_refresh' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { triggerRun, setRunnerToken } = await import('./research-runner-api');
    setRunnerToken('secret-abc');

    await triggerRun('data_refresh', { intraday: false });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers['X-Runner-Token']).toBe('secret-abc');
  });

  it('cancelRun sends the stored X-Runner-Token header', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ run_id: 'r1', cancelled: true }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { cancelRun, setRunnerToken } = await import('./research-runner-api');
    setRunnerToken('secret-abc');

    await cancelRun('r1');

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers['X-Runner-Token']).toBe('secret-abc');
  });
});

describe('research-runner-api health contract', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('VITE_RUNNER_URL', 'http://127.0.0.1:8420');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  const validHealth = () => ({
    mode: 'RESEARCH_RUNNER',
    live_disallowed: true,
    kinds: ['ashare_technical_search', 'ashare_forward_observation'],
    quarantined_kinds: { ashare_alpha: 'INVALIDATED_PERP_LEDGER_V1' },
    kind_policies: {
      ashare_technical_search: {
        purpose: 'DIAGNOSTIC_ONLY',
        evidence_authority: null,
      },
      ashare_forward_observation: {
        purpose: 'DIAGNOSTIC_ONLY',
        evidence_authority: null,
      },
      ashare_alpha: {
        purpose: 'DIAGNOSTIC_ONLY',
        evidence_authority: null,
      },
    },
  });

  it('accepts and returns a structurally valid fail-closed runner health body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => validHealth(),
    }));
    const { fetchRunnerHealth } = await import('./research-runner-api');

    await expect(fetchRunnerHealth()).resolves.toEqual(validHealth());
  });

  it.each([
    ['non-object body', null],
    ['wrong mode', { ...validHealth(), mode: 'READ_ONLY' }],
    ['live boundary disabled', { ...validHealth(), live_disallowed: false }],
    ['non-array kinds', { ...validHealth(), kinds: 'ashare_technical_search' }],
    ['duplicate kinds', {
      ...validHealth(), kinds: ['ashare_technical_search', 'ashare_technical_search'],
    }],
    ['missing policies', { ...validHealth(), kind_policies: undefined }],
    ['missing advertised policy', {
      ...validHealth(),
      kind_policies: { ashare_forward_observation: validHealth().kind_policies.ashare_forward_observation },
    }],
    ['invalid policy purpose', {
      ...validHealth(),
      kind_policies: {
        ...validHealth().kind_policies,
        ashare_technical_search: { purpose: 'LIVE', evidence_authority: null },
      },
    }],
    ['diagnostic authority conflict', {
      ...validHealth(),
      kind_policies: {
        ...validHealth().kind_policies,
        ashare_technical_search: {
          purpose: 'DIAGNOSTIC_ONLY', evidence_authority: 'SHOULD_NOT_EXIST',
        },
      },
    }],
    ['advertised/quarantined conflict', {
      ...validHealth(),
      quarantined_kinds: { ashare_technical_search: 'QUARANTINED' },
    }],
  ])('rejects %s instead of exposing trigger buttons', async (_label, body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => body,
    }));
    const { fetchRunnerHealth } = await import('./research-runner-api');

    await expect(fetchRunnerHealth()).rejects.toThrow('RUNNER_HEALTH_INVALID:');
  });
});
