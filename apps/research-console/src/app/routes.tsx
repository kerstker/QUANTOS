import type React from 'react';

/**
 * 单一页面注册表(single source of truth)——路由、左侧导航、冒烟测试全部由此派生。
 * 新增页面 = 在 PAGES 加一行:自动进导航组、自动获得路由、自动被全量冒烟测试覆盖(见 pages.smoke.test.tsx)。
 * 分组即研究生命周期(顶级量化系统的工作流):总览 → 数据 → 因子研究 → 验证 → 组合与风险 → 执行与跟踪 → 治理与系统。
 * 2026-07-03 信息架构重构:42 页 → 30 页(砍掉 12 个纯合成蓝图/重复/演示页,旧路由在 REDIRECTS 语义就近重定向;
 * 诚实姿态由 SystemCompleteness 的 REAL_REPO_INVENTORY 统一承担——删页不删诚实)。
 */

export type GroupId =
  | 'overview' | 'data' | 'alpha' | 'validation' | 'portfolio' | 'execution' | 'system';

export interface GroupDef { id: GroupId; zh: string; en: string }

export interface PageDef {
  path: string;
  zh: string;
  en: string;
  icon: string;
  /** null = 仅路由(详情/对比等子页),不进导航 */
  group: GroupId | null;
  load: () => Promise<{ default: React.ComponentType }>;
}

export interface RedirectDef { from: string; to: string }

export const GROUPS: readonly GroupDef[] = [
  { id: 'overview', zh: '总览', en: 'OVERVIEW' },
  { id: 'data', zh: '数据', en: 'DATA' },
  { id: 'alpha', zh: '因子研究', en: 'ALPHA RESEARCH' },
  { id: 'validation', zh: '验证', en: 'VALIDATION' },
  { id: 'portfolio', zh: '组合与风险', en: 'PORTFOLIO & RISK' },
  { id: 'execution', zh: '执行与跟踪', en: 'EXECUTION' },
  { id: 'system', zh: '治理与系统', en: 'GOVERNANCE & SYSTEM' },
] as const;

export const PAGES: readonly PageDef[] = [
  // ---- 总览:新手引导(真正的首页 — 说明全流程、导向各分组)+ 唯一落地仪表盘 ----
  { path: '/research/getting-started', zh: '新手引导', en: 'Getting Started', icon: 'GS', group: 'overview',
    load: () => import('@/pages/GettingStarted') },
  { path: '/research/command', zh: '指挥中心', en: 'Command Center', icon: 'CC', group: 'overview',
    load: () => import('@/pages/CommandCenter') },

  // ---- 数据:研究的原料(全真实快照) ----
  { path: '/research/data-catalog', zh: '数据目录', en: 'Data Catalog', icon: 'DC', group: 'data',
    load: () => import('@/pages/DataCatalog') },
  { path: '/research/crypto-live', zh: '加密市场', en: 'Crypto Live', icon: '₿', group: 'data',
    load: () => import('@/pages/CryptoLive') },
  { path: '/research/fx-research', zh: '外汇市场', en: 'FX Research', icon: 'FX', group: 'data',
    load: () => import('@/pages/FxResearch') },

  // ---- 因子研究:发现与操作(ResearchRuns 是唯一可变操作台) ----
  { path: '/research/runs', zh: '研究运行', en: 'Research Runs', icon: 'RR', group: 'alpha',
    load: () => import('@/pages/ResearchRuns') },
  { path: '/research/ic-panel', zh: 'IC 面板', en: 'IC Panel', icon: 'IC', group: 'alpha',
    load: () => import('@/pages/ICPanel') },
  { path: '/research/alpha-factory', zh: '因子工厂', en: 'Alpha Factory', icon: 'AF', group: 'alpha',
    load: () => import('@/pages/AlphaFactory') },
  { path: '/research/alpha-zoo', zh: 'Alpha 资产库', en: 'Alpha Zoo', icon: 'AZ', group: 'alpha',
    load: () => import('@/pages/AlphaZoo') },
  { path: '/research/alpha-graph', zh: '相关图谱', en: 'Alpha Graph', icon: 'GR', group: 'alpha',
    load: () => import('@/pages/AlphaGraph') },
  { path: '/research/dsl-explorer', zh: 'DSL 检查器', en: 'DSL Explorer', icon: 'DSL', group: 'alpha',
    load: () => import('@/pages/DslExplorer') },
  { path: '/research/findings', zh: '研究结论', en: 'Findings', icon: 'RF', group: 'alpha',
    load: () => import('@/pages/ResearchFindings') },

  // ---- 验证:不自欺关卡与证据 ----
  { path: '/research/validation', zh: '验证门', en: 'Validation Gate', icon: 'VA', group: 'validation',
    load: () => import('@/pages/Validation') },
  { path: '/research/experiments', zh: '实验登记', en: 'Experiments', icon: 'EX', group: 'validation',
    load: () => import('@/pages/Experiments') },
  { path: '/research/backtest-results', zh: '回测战果', en: 'Backtest Results', icon: 'BR', group: 'validation',
    load: () => import('@/pages/BacktestResults') },
  { path: '/research/computation-lab', zh: '计算实验室', en: 'Computation Lab', icon: 'CL', group: 'validation',
    load: () => import('@/pages/ComputationLab') },

  // ---- 组合与风险 ----
  { path: '/research/portfolio', zh: '组合构建', en: 'Portfolio', icon: 'PF', group: 'portfolio',
    load: () => import('@/pages/Portfolio') },
  { path: '/research/population-optimizer', zh: '种群优化器', en: 'Optimizer', icon: 'PO', group: 'portfolio',
    load: () => import('@/pages/PopulationOptimizer') },
  { path: '/research/risk', zh: '风险仪表盘', en: 'Risk Dashboard', icon: 'RK', group: 'portfolio',
    load: () => import('@/pages/Risk') },
  { path: '/research/monitoring-drift', zh: '衰退监控', en: 'Drift Monitor', icon: 'MD', group: 'portfolio',
    load: () => import('@/pages/MonitoringDrift') },

  // ---- 执行与跟踪:干跑是运行时 chokepoint,纸面/生命周期是前向证据 ----
  { path: '/research/dry-run', zh: '干跑流水线', en: 'Dry-Run Pipeline', icon: 'DR', group: 'execution',
    load: () => import('@/pages/DryRunPipeline') },
  { path: '/research/carry-readiness', zh: 'Carry 就绪', en: 'Carry Readiness', icon: 'CY', group: 'execution',
    load: () => import('@/pages/CarryReadiness') },
  { path: '/research/paper-trading', zh: '纸面跟踪', en: 'Paper Track', icon: 'PT', group: 'execution',
    load: () => import('@/pages/PaperTrading') },
  { path: '/research/live-trading', zh: '实盘路线', en: 'Live Trading', icon: 'LT', group: 'execution',
    load: () => import('@/pages/LiveTrading') },
  { path: '/research/live-desk', zh: '交易台', en: 'Live Desk', icon: 'LD', group: 'execution',
    load: () => import('@/pages/LiveDesk') },
  { path: '/research/gonogo', zh: 'GoNoGo 证据', en: 'GoNoGo Evidence', icon: 'GG', group: 'execution',
    load: () => import('@/pages/Gonogo') },
  { path: '/research/lifecycle-monitor', zh: '生命周期', en: 'Lifecycle', icon: 'LC', group: 'execution',
    load: () => import('@/pages/LifecycleMonitor') },

  // ---- 治理与系统:审计、健康、诚实完整度 ----
  { path: '/research/promotion-queue', zh: '晋级审批', en: 'Promotion Queue', icon: 'PQ', group: 'system',
    load: () => import('@/pages/PromotionQueue') },
  { path: '/research/evidence-ledger', zh: '证据链', en: 'Evidence Ledger', icon: 'EL', group: 'system',
    load: () => import('@/pages/EvidenceLedger') },
  { path: '/research/events', zh: '事件流', en: 'Events', icon: 'EV', group: 'system',
    load: () => import('@/pages/Events') },
  { path: '/research/system-health', zh: '系统健康', en: 'Health', icon: 'SY', group: 'system',
    load: () => import('@/pages/SystemHealth') },
  { path: '/research/system-completeness', zh: '系统完整度', en: 'Completeness', icon: 'CP', group: 'system',
    load: () => import('@/pages/SystemCompleteness') },
  { path: '/research/architecture', zh: '架构', en: 'Architecture', icon: 'AR', group: 'system',
    load: () => import('@/pages/Architecture') },

  // ---- 仅路由(不进导航) ----
  { path: '/research/runs/:id', zh: '运行详情', en: 'Run Detail', icon: 'RD', group: null,
    load: () => import('@/pages/RunDetail') },
  { path: '/research/compare', zh: '运行对比', en: 'Compare Runs', icon: 'CMP', group: null,
    load: () => import('@/pages/CompareRuns') },
] as const;

/** 旧路由 → 语义就近的新目标(书签不断链;蓝图页统一去「系统完整度」——那里诚实说明哪些子系统尚未建成)。 */
export const REDIRECTS: readonly RedirectDef[] = [
  { from: '/research', to: '/research/getting-started' },
  { from: '/research/model-risk-review', to: '/research/system-completeness' },
  { from: '/research/reviewer-workflow', to: '/research/system-completeness' },
  { from: '/research/access-control', to: '/research/system-completeness' },
  { from: '/research/incident-response', to: '/research/system-completeness' },
  { from: '/research/promotion-control', to: '/research/system-completeness' },
  { from: '/research/release-committee', to: '/research/system-completeness' },
  { from: '/research/agents', to: '/research/system-completeness' },
  { from: '/research/phase-3a', to: '/research/system-completeness' },
  { from: '/research/scenario-stress', to: '/research/risk' },
  { from: '/research/backtest-explainability', to: '/research/backtest-results' },
  { from: '/research/signal-to-portfolio', to: '/research/portfolio' },
] as const;
