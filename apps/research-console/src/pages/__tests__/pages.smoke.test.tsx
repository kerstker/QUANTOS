import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AlphaFactory from '@/pages/AlphaFactory';
import Validation from '@/pages/Validation';
import CryptoLive from '@/pages/CryptoLive';
import LifecycleMonitor from '@/pages/LifecycleMonitor';
import AlphaZoo from '@/pages/AlphaZoo';
import PopulationOptimizer from '@/pages/PopulationOptimizer';
import SignalToPortfolio from '@/pages/SignalToPortfolio';
import DslExplorer from '@/pages/DslExplorer';
import AlphaGraph from '@/pages/AlphaGraph';
import BacktestResults from '@/pages/BacktestResults';
import ResearchRuns from '@/pages/ResearchRuns';
import RunDetail from '@/pages/RunDetail';

// First real component/render tests (the audit's "UI tests effectively zero" gap). In the test env the
// console API is disabled (VITE_MOCK_MODE), so every page renders its mock fixtures without any network.
// A smoke render proves the page mounts, produces DOM, and does not throw.
describe('console pages render in mock mode', () => {
  for (const [name, Page] of [
    ['AlphaFactory', AlphaFactory],
    ['Validation', Validation],
    ['CryptoLive', CryptoLive],
    ['LifecycleMonitor', LifecycleMonitor],
    ['AlphaZoo', AlphaZoo],
    ['PopulationOptimizer', PopulationOptimizer],
    ['SignalToPortfolio', SignalToPortfolio],
    ['DslExplorer', DslExplorer],
    ['AlphaGraph', AlphaGraph],
    ['BacktestResults', BacktestResults],
    ['ResearchRuns', ResearchRuns],
    ['RunDetail', RunDetail],
  ] as const) {
    it(`${name} mounts and produces non-empty DOM`, () => {
      const { container } = render(<MemoryRouter><Page /></MemoryRouter>);
      expect(container.firstChild).not.toBeNull();
      expect((container.textContent ?? '').trim().length).toBeGreaterThan(0);
    });
  }
});
