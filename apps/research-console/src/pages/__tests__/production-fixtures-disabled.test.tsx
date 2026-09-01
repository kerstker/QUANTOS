import { render, screen } from '@testing-library/react';
import type { ComponentType } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { MemoryRouter } from '@/lib/router';
import { ToastProvider } from '@/ui/toast';
import AlphaGraph from '../AlphaGraph';
import AlphaLifecycle from '../AlphaLifecycle';
import DataCatalog from '../DataCatalog';
import DryRunPipeline from '../DryRunPipeline';
import DslExplorer from '../DslExplorer';
import Experiments from '../Experiments';
import MonitoringDrift from '../MonitoringDrift';
import PopulationOptimizer from '../PopulationOptimizer';

vi.mock('@/ui/useConsoleSnapshot', async importOriginal => {
  const actual = await importOriginal<typeof import('@/ui/useConsoleSnapshot')>();
  return {
    ...actual,
    useConsoleSnapshot: () => ({
      payload: null,
      apiState: 'error' as const,
      error: 'TEST_TRANSPORT_FAILURE',
      usingBackend: false,
      mockFixturesAllowed: false,
      snapshotState: 'UNAVAILABLE' as const,
      actionsAllowed: false,
    }),
  };
});

const PAGES: ReadonlyArray<[string, ComponentType]> = [
  ['alpha graph', AlphaGraph],
  ['alpha lifecycle', AlphaLifecycle],
  ['data catalog', DataCatalog],
  ['dry run', DryRunPipeline],
  ['DSL explorer', DslExplorer],
  ['experiments', Experiments],
  ['monitoring', MonitoringDrift],
  ['population optimizer', PopulationOptimizer],
];

describe('production fixture boundary', () => {
  it.each(PAGES)('%s renders only the transport-unavailable state', (_name, Page) => {
    render(
      <MemoryRouter>
        <ToastProvider><Page /></ToastProvider>
      </MemoryRouter>,
    );

    expect(screen.getByTestId('console-snapshot-unavailable')).toBeInTheDocument();
    expect(screen.getByText('BACKEND_SNAPSHOT_UNAVAILABLE')).toBeInTheDocument();
    expect(screen.getByText('PRODUCTION_FIXTURES_DISABLED')).toBeInTheDocument();
    expect(screen.getByText('TEST_TRANSPORT_FAILURE')).toBeInTheDocument();
  });
});
