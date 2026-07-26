import type { GroupId } from '@/app/routes';

/* ============================================================================
   Automation map (2026-07-24) — the honest division of labour between the
   SYSTEM (fully automated, fail-closed machinery) and the HUMAN (gates that
   structurally require one or more real people). Grounded in the verified
   system reality (AGENTS.md / 2026-06-29 audit): every human entry below is a
   REAL chokepoint — promotion requires three distinct real identities,
   kill-switch RESET requires a multi-party grant, live submission is
   structurally blocked in L2. This map is descriptive (it routes operators),
   not a permission system.
   ========================================================================== */

export interface FlowStep {
  zh: string;
  en: string;
  /** Console route where the step is observed / performed. */
  to?: string;
}

export interface StageAutomation {
  auto: FlowStep[];
  human: FlowStep[];
}

export const STAGE_AUTOMATION: Record<GroupId, StageAutomation> = {
  overview: {
    auto: [
      { zh: '实时快照聚合与管道仪表', en: 'live snapshot aggregation', to: '/research/command' },
    ],
    human: [
      { zh: '人工操作台决策(本页队列)', en: 'human ops decisions', to: '/research/ops-console' },
    ],
  },
  data: {
    auto: [
      { zh: '行情刷新流水线(幂等多源)', en: 'market-data refresh (idempotent)', to: '/research/data-catalog' },
      { zh: '质量检查与 PIT 存储', en: 'quality checks + PIT store', to: '/research/data-catalog' },
    ],
    human: [
      { zh: '新数据源接入与凭证持有(凭证永不落盘)', en: 'new source onboarding (credentials never on disk)', to: '/research/data-catalog' },
    ],
  },
  alpha: {
    auto: [
      { zh: '搜索循环 / GP 演化(guardrailed)', en: 'search loops / GP evolution', to: '/research/alpha-zoo' },
      { zh: 'IC 面板初筛诊断', en: 'IC panel screening', to: '/research/ic-panel' },
      { zh: '衰退 / 退役扫描(maintenance)', en: 'degradation & retirement sweep', to: '/research/monitoring-drift' },
    ],
    human: [
      { zh: '研究运行启动(guarded mutating runner)', en: 'launch research runs', to: '/research/runs' },
    ],
  },
  validation: {
    auto: [
      { zh: '验证门(DSR / 净化 CV / 无前视)', en: 'validation gate (DSR / purged CV)', to: '/research/validation' },
      { zh: '回测 + PnL 对账恒等式', en: 'backtest + PnL reconciliation', to: '/research/backtest-results' },
      { zh: '实验登记(不可变工件)', en: 'experiment registry', to: '/research/experiments' },
      { zh: '前向 paper-track 持久层', en: 'forward paper track', to: '/research/lifecycle-monitor' },
    ],
    human: [],
  },
  portfolio: {
    auto: [
      { zh: '组合优化(可行性检查)', en: 'constrained optimizer', to: '/research/portfolio' },
      { zh: '因子风险模型(PSD 检查)', en: 'factor risk model', to: '/research/risk' },
      { zh: '去冗余选集', en: 'de-redundant set', to: '/research/population-optimizer' },
    ],
    human: [],
  },
  execution: {
    auto: [
      { zh: '干跑流水线(门→纸面成交→对账→落库)', en: 'dry-run pipeline', to: '/research/dry-run' },
      { zh: '资金费实时交叉校验', en: 'funding cross-check', to: '/research/dry-run' },
    ],
    human: [
      { zh: '人工 mandate 签署(AUTO 恒拒)', en: 'sign mandate (AUTO always rejected)', to: '/research/live-trading' },
      { zh: 'GoNoGo 证据复核', en: 'GoNoGo evidence review', to: '/research/gonogo' },
      { zh: '上线向导(毕业路径)', en: 'go-live wizard', to: '/research/go-live' },
    ],
  },
  system: {
    auto: [
      { zh: '审计哈希链与事件流投影', en: 'audit chain + event projection', to: '/research/events' },
      { zh: '部署评估计算(L2 恒不应用)', en: 'deployment evaluation (never applied in L2)', to: '/research/system-completeness' },
      { zh: '健康与告警阈值监控', en: 'health + alert thresholds', to: '/research/system-health' },
    ],
    human: [
      { zh: '晋级审批(三个不同真人)', en: 'promotion approval (3 distinct humans)', to: '/research/promotion-queue' },
      { zh: 'Kill-Switch RESET(需多方 grant)', en: 'kill-switch RESET (multi-party grant)', to: '/research/risk' },
      { zh: 'LIVE 解锁(L2 结构性封锁,post-L2)', en: 'LIVE unlock (structurally blocked)', to: '/research/system-completeness' },
    ],
  },
};

/** Totals for badges: how many automated vs human-gated steps per stage. */
export function stageAutomationCounts(id: GroupId): { auto: number; human: number } {
  const s = STAGE_AUTOMATION[id];
  return { auto: s.auto.length, human: s.human.length };
}
