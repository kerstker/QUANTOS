import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MemoryRouter } from '@/lib/router';
import {
  GLOBAL_DRAWER_DEFINITIONS,
  GlobalDrawerHost,
} from '@/workbench/GlobalDrawerHost';
import { makeMftSnapshot } from '@/workbench/__tests__/mft-fixtures';

const state = vi.hoisted(() => ({
  console: {} as Record<string, unknown>,
  mft: {} as Record<string, unknown>,
}));

vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: () => state.console,
}));

vi.mock('@/workbench/useMftReadModel', () => ({
  useMftReadModel: () => state.mft,
}));

function renderHost(path = '/workbench/market') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <GlobalDrawerHost />
    </MemoryRouter>,
  );
}

describe('global read-only workflow drawers', () => {
  beforeEach(() => {
    state.console = {
      payload: null, apiState: 'error', error: 'API unavailable', usingBackend: false,
    };
    state.mft = {
      phase: 'UNAVAILABLE', snapshot: null, reason: 'API unavailable', pages: 0,
      reloaded: false, transport: 'NONE', events: [], eventHistory: 'SESSION_DELTA',
    };
  });

  it('declares four unique purposes and authoritative read endpoints', () => {
    expect(GLOBAL_DRAWER_DEFINITIONS).toHaveLength(4);
    expect(new Set(GLOBAL_DRAWER_DEFINITIONS.map(item => item.purpose)).size).toBe(4);
    expect(new Set(GLOBAL_DRAWER_DEFINITIONS.map(item => item.authoritativeEndpoint)).size).toBe(4);
    expect(GLOBAL_DRAWER_DEFINITIONS.every(item => item.authoritativeEndpoint.startsWith('/api/v1/'))).toBe(true);
  });

  it('opens an audit bookmark directly and never fabricates an event stream', () => {
    renderHost('/workbench/market?drawer=audit');
    const drawer = screen.getByRole('dialog', { name: '审计抽屉' });
    expect(drawer).toHaveTextContent('AUDIT_UNAVAILABLE');
    expect(drawer).toHaveTextContent('API unavailable');
    expect(drawer).not.toHaveTextContent('evt-');
  });

  it('shows verified governance safety without exposing a command', () => {
    state.console = {
      apiState: 'connected', error: null, usingBackend: true,
      payload: {
        source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
        contract_version: 'console-runtime.v1', service: 'research-console', instance_id: 'instance-1',
        process_id: 42, build_sha: 'a'.repeat(40), build_sha_known: true,
        event_schema_catalog: { sha256: 'b'.repeat(64), event_type_count: 7, latest_versions: {} },
        safety_boundary: {
          read_only_console: true, authorized_level: 'L2_DRY_RUN_ONLY',
          live_trading_authorized: false, order_submission_allowed: false,
          broker_connectivity: false,
        },
        generated_at_ms: 1_700_000_000_000,
      },
    };
    renderHost();
    fireEvent.click(screen.getByRole('button', { name: '治理' }));
    const drawer = screen.getByRole('dialog', { name: '治理抽屉' });
    expect(drawer).toHaveTextContent('L2_DRY_RUN_ONLY');
    expect(drawer).toHaveTextContent('ORDER SUBMISSION BLOCKED');
    expect(withinDangerous(drawer)).toBeNull();
  });

  it('renders only backend audit events and verified chain state', () => {
    state.console = {
      apiState: 'connected', error: null, usingBackend: true,
      payload: {
        source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
        events: [{
          event_id: 'evt-authority-1', event_type: 'workflow.completed',
          timestamp: 1_700_000_000, source: 'orchestration', correlation_id: 'corr-1', payload: {},
        }],
        event_bus_health: {
          verdict: 'PASS', pending_events: 0, dead_letter_count: 0,
          integrity: {
            event_chain_valid: true, consumer_chain_valid: true, coordination_chain_valid: true,
          },
        },
      },
    };
    renderHost();
    fireEvent.click(screen.getByRole('button', { name: '审计' }));
    const drawer = screen.getByRole('dialog', { name: '审计抽屉' });
    expect(drawer).toHaveTextContent('workflow.completed');
    expect(drawer).toHaveTextContent('evt-authority-1');
    expect(drawer).toHaveTextContent('VERIFIED');
  });

  it('keeps unmeasured service health explicit instead of promoting it to healthy', () => {
    state.console = {
      apiState: 'connected', error: null, usingBackend: true,
      payload: {
        source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
        services: [
          { service: 'console-api', status: 'healthy', latency_ms: 4, error_rate: 0 },
          { service: 'paper-worker', status: 'unknown', latency_ms: null, error_rate: null },
        ],
        gates: [], safety_checks: ['No broker connectivity'],
      },
    };
    renderHost();
    fireEvent.click(screen.getByRole('button', { name: '健康' }));
    const drawer = screen.getByRole('dialog', { name: '健康抽屉' });
    expect(drawer).toHaveTextContent('paper-worker');
    expect(drawer).toHaveTextContent('UNKNOWN');
    expect(drawer).toHaveTextContent('No broker connectivity');
  });

  it('shows only current MFT scheduler state in the task drawer', () => {
    state.mft = {
      phase: 'LIVE', reason: null, pages: 1, reloaded: false, transport: 'SSE',
      events: [], eventHistory: 'SESSION_DELTA',
      snapshot: makeMftSnapshot({
        scheduler: {
          status: 'LEADER', task_name: 'mft-rebalance', last_success_ms: 1_700_000_000_000,
          next_due_ms: 1_700_000_060_000, recovery_status: 'NONE',
        },
      }),
    };
    renderHost();
    fireEvent.click(screen.getByRole('button', { name: '任务' }));
    const drawer = screen.getByRole('dialog', { name: '任务抽屉' });
    expect(drawer).toHaveTextContent('mft-rebalance');
    expect(drawer).toHaveTextContent('LEADER');
    expect(withinDangerous(drawer)).toBeNull();
  });
});

function withinDangerous(container: HTMLElement) {
  return container.querySelector('button:is([aria-label*="submit" i], [aria-label*="approve" i], [aria-label*="reset" i], [aria-label*="cancel" i])');
}
