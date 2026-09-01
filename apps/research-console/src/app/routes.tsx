import type React from 'react';

import {
  WORKBENCH_DATA_SOURCE_MANIFEST,
  type WorkbenchObjectId,
} from '@/workbench/data-source-manifest';

/**
 * Wave 3 route registry. The data-source manifest owns every user-facing object
 * workspace label, route, purpose and authority policy. This file adds only
 * route-level loaders and a bounded set of unambiguous legacy bookmarks.
 */
export type GroupId = 'workbench';

export interface GroupDef { id: GroupId; zh: string; en: string }

export interface PageDef {
  path: string;
  zh: string;
  en: string;
  icon: string;
  /** null marks a bounded detail route; it never becomes a top-level destination. */
  group: GroupId | null;
  load: () => Promise<{ default: React.ComponentType }>;
}

export interface RedirectDef { from: string; to: string }

export const GROUPS: readonly GroupDef[] = [
  { id: 'workbench', zh: '对象工作区', en: 'OBJECT WORKSPACES' },
] as const;

const LOADERS: Record<WorkbenchObjectId, PageDef['load']> = {
  'market-monitor': () => import('@/pages/workbench/MarketMonitor'),
  'alpha-control-plane': () => import('@/pages/workbench/AlphaControlPlane'),
  'validation-evidence': () => import('@/pages/workbench/ValidationEvidence'),
  'portfolio-risk': () => import('@/pages/workbench/PortfolioRisk'),
  'execution-orders': () => import('@/pages/workbench/ExecutionOrders'),
};

const WORKSPACE_PAGES: readonly PageDef[] = WORKBENCH_DATA_SOURCE_MANIFEST.map(item => ({
  path: item.route,
  zh: item.zh,
  en: item.en,
  icon: item.icon,
  group: 'workbench',
  load: LOADERS[item.id],
}));

const DETAIL_PAGES: readonly PageDef[] = [
  { path: '/research/modules', zh: '全部后端模块', en: 'Backend Modules', icon: 'BM', group: null,
    load: () => import('@/pages/ModuleExplorer') },
  { path: '/research/runs', zh: '研究运行', en: 'Research Runs', icon: 'RR', group: null,
    load: () => import('@/pages/ResearchRuns') },
  { path: '/research/data-catalog', zh: '数据目录详情', en: 'Data Catalog Detail', icon: 'DC', group: null,
    load: () => import('@/pages/DataCatalog') },
  { path: '/research/runs/:id', zh: '运行详情', en: 'Run Detail', icon: 'RD', group: null,
    load: () => import('@/pages/RunDetail') },
  { path: '/research/compare', zh: '运行对比', en: 'Compare Runs', icon: 'CR', group: null,
    load: () => import('@/pages/CompareRuns') },
  { path: '/research/experiments', zh: '实验登记详情', en: 'Experiment Detail', icon: 'EX', group: null,
    load: () => import('@/pages/Experiments') },
  { path: '/research/alpha-graph', zh: 'Alpha 血缘详情', en: 'Alpha Graph Detail', icon: 'AG', group: null,
    load: () => import('@/pages/AlphaGraph') },
  { path: '/research/alpha-lifecycle', zh: '生命周期详情', en: 'Lifecycle Detail', icon: 'LC', group: null,
    load: () => import('@/pages/AlphaLifecycle') },
  { path: '/research/dsl-explorer', zh: 'DSL 检查详情', en: 'DSL Detail', icon: 'DSL', group: null,
    load: () => import('@/pages/DslExplorer') },
  { path: '/research/backtest-results', zh: '回测证据详情', en: 'Backtest Detail', icon: 'BT', group: null,
    load: () => import('@/pages/BacktestResults') },
  { path: '/research/population-optimizer', zh: '种群优化详情', en: 'Population Detail', icon: 'PO', group: null,
    load: () => import('@/pages/PopulationOptimizer') },
  { path: '/research/monitoring-drift', zh: '漂移详情', en: 'Drift Detail', icon: 'MD', group: null,
    load: () => import('@/pages/MonitoringDrift') },
  { path: '/research/dry-run', zh: '干跑证据详情', en: 'Dry-Run Detail', icon: 'DR', group: null,
    load: () => import('@/pages/DryRunPipeline') },
  { path: '/research/system-completeness', zh: '系统完整度详情', en: 'System Completeness', icon: 'SC', group: null,
    load: () => import('@/pages/SystemCompleteness') },
] as const;

export const PAGES: readonly PageDef[] = [...WORKSPACE_PAGES, ...DETAIL_PAGES];

/**
 * Only bookmarks with a single safe interpretation survive consolidation.
 * Command-like legacy destinations intentionally have no redirect: silently
 * mapping an old approval/live route to a read view would create safety
 * ambiguity. Drawer redirects are URL-addressable and open the named summary.
 */
export const REDIRECTS: readonly RedirectDef[] = [
  { from: '/research', to: '/workbench/market' },
  { from: '/research/getting-started', to: '/workbench/market' },
  { from: '/research/mexc-market', to: '/workbench/market' },

  { from: '/research/ic-panel', to: '/workbench/alpha' },
  { from: '/research/alpha-zoo', to: '/workbench/alpha' },
  { from: '/research/findings', to: '/workbench/alpha' },
  { from: '/research/ashare-alpha', to: '/workbench/alpha' },
  { from: '/research/agents', to: '/workbench/alpha' },

  { from: '/research/validation', to: '/workbench/validation' },
  { from: '/research/lifecycle-monitor', to: '/workbench/validation' },
  { from: '/research/crypto-live', to: '/workbench/validation' },
  { from: '/research/fx-research', to: '/workbench/validation' },

  { from: '/research/portfolio', to: '/workbench/portfolio-risk' },
  { from: '/research/risk', to: '/workbench/portfolio-risk' },

  { from: '/research/carry-readiness', to: '/workbench/execution' },
  { from: '/research/mexc-strategy', to: '/workbench/execution' },

  { from: '/research/command', to: '/workbench/market?drawer=tasks' },
  { from: '/research/events', to: '/workbench/market?drawer=audit' },
  { from: '/research/system-health', to: '/workbench/market?drawer=health' },
] as const;
