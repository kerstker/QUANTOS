import React from 'react';
import { Link } from 'react-router-dom';
import { bi } from '@/lib/i18n/bilingual';
import { PageShell, PageHeader, Panel, Chip } from '@/ui/primitives';
import { PAGES, GROUPS, type GroupId } from '@/app/routes';

/**
 * Getting Started — the actual landing page (`/research` redirects here, see routes.tsx
 * REDIRECTS). Pure static orientation: what QUANTOS is, the safety posture, and a
 * walk through the six research-lifecycle groups (data → alpha → validation →
 * portfolio → execution → system), linking to the REAL pages registered in
 * `PAGES` — nothing hardcoded/invented, so this page can never drift from the nav.
 *
 * Deliberately makes NO network requests and offers NO action buttons: it is the
 * first thing anyone opening the console sees, so it must render instantly and
 * correctly regardless of backend health. All operations happen on the pages this
 * page links to (Research Runs, Dry-Run Pipeline, ...), never here.
 */

interface Stage { group: GroupId; titleZh: string; titleEn: string; descZh: string; descEn: string }

const STAGES: readonly Stage[] = [
  {
    group: 'data',
    titleZh: '数据 — 研究的原料',
    titleEn: 'Data — the raw material',
    descZh: '下游每一步研究都从这里的真实历史数据出发:数据目录展示 point-in-time 数据集、版本、质量检查与无生存偏差的'
      + '宇宙定义。这一步不产出任何交易信号,只保证后面每一步用的数据干净、可复现、没有前视偏差。',
    descEn: 'Every downstream step starts from real historical data here: Data Catalog shows point-in-time datasets, '
      + 'versioning, quality checks, and survivorship-free universes. This step produces no trading signal — it just '
      + 'guarantees everything downstream is clean, reproducible, and lookahead-free.',
  },
  {
    group: 'alpha',
    titleZh: '因子研究 — 发现候选 alpha',
    titleEn: 'Alpha research — discover candidates',
    descZh: '研究运行是唯一的可变操作台,在这里触发真实的回测/搜索任务并写入运行注册表;IC 面板是横向初筛诊断(不是晋级门禁),'
      + '用截面 rank-IC / ICIR / t-stat 比较候选因子;DSL 检查器验证因子表达式的静态类型与 PIT 安全性;Alpha 资产库汇总每个'
      + '候选因子的多维评估(含生命周期状态分布);相关图谱检查因子之间是否冗余;研究结论沉淀容量、情景、宏观 regime 等'
      + '现实主义诊断成果,以及不定期收到的外部策略主张审计。',
    descEn: 'Research Runs is the only mutating surface — it triggers real backtest/search jobs and writes to the run '
      + 'registry; IC Panel is a cross-sectional screening diagnostic (not a promotion gate) comparing candidates by '
      + 'rank-IC / ICIR / t-stat; DSL Explorer verifies factor-expression static typing and PIT safety; Alpha Zoo '
      + 'aggregates multi-dimensional evaluation per candidate (incl. lifecycle-state composition); Alpha Graph checks '
      + 'for redundancy; Findings holds the realism/macro/diversification diagnostics plus any external strategy '
      + 'claims that have been independently audited.',
  },
  {
    group: 'validation',
    titleZh: '验证 — 不自欺关卡',
    titleEn: 'Validation — no-self-deception gates',
    descZh: '一个因子哪怕历史回测好看,也要先过 Deflated Sharpe Ratio、组合式清洗交叉验证(CPCV)、purged & embargoed '
      + 'walk-forward 这些验证门(含 What-if 成本/压力敏感性估算),才有资格往下走;实验登记保存不可篡改的实验产物和'
      + '试验计数,防止事后调参美化结果;回测战果看结果汇总;加密市场/外汇市场是这套关卡在具体资产类别上的完整案例——'
      + '前者是机器因子发现被无窥视 OOS 验证捕获选择性过拟合,后者是一个看似为正的全样本结果被可交易关卡证伪。',
    descEn: 'Even a good-looking backtest must clear Deflated Sharpe Ratio, combinatorial purged cross-validation '
      + '(CPCV), and purged & embargoed walk-forward gates (with a what-if cost/stress sensitivity estimate) before '
      + 'it can proceed; Experiments persists immutable artifacts and trial counts so results cannot be beautified '
      + 'after the fact; Backtest Results shows the result summary; Crypto Live / FX Research are full case studies '
      + 'of these gates on specific asset classes — the former catches selection overfitting via no-peek OOS, the '
      + 'latter shows a seemingly-positive full-sample result refuted by a tradable-universe gauntlet.',
  },
  {
    group: 'portfolio',
    titleZh: '组合与风险 — 合成一个组合',
    titleEn: 'Portfolio & risk — combine into one book',
    descZh: '把通过验证的因子合成一个组合,而不是孤立看单条因子。组合构建用带可行性检查的约束优化器和 PSD 校验的因子'
      + '风险模型;种群优化器在一堆相关候选里选出真正互补的去冗余子集;风险仪表盘看敞口与风险预算;衰退监控盯着'
      + '已上线因子有没有开始失效。',
    descEn: 'Validated factors get combined into one portfolio rather than judged in isolation. Portfolio uses a '
      + 'feasibility-checked constrained optimizer and a PSD-checked factor risk model; Optimizer selects a truly '
      + 'complementary, de-redundant subset out of correlated candidates; Risk Dashboard shows exposure and risk '
      + 'budget; Drift Monitor watches deployed factors for decay.',
  },
  {
    group: 'execution',
    titleZh: '执行与跟踪 — 当真钱一样跑一遍,但从不真的发单',
    titleEn: 'Execution — run it like real money, never actually send an order',
    descZh: '干跑流水线是运行时关卡:权重经过盘前风控 + kill-switch + 纸面成交 + 对账,全程哈希链留痕,纯进程内'
      + '模拟、不发出任何外部网络请求;Carry 就绪、GoNoGo 证据、实盘路线汇总走向实盘所需的证据以及 Phase 3A '
      + '人工批准阶段;交易台连接的是真实交易所测试网/demo REST 接口(不同于干跑流水线的纯进程内模拟),可通过'
      + '二次确认的 SUBMIT 按钮提交测试网订单——仅 testnet/demo、零真实资产,且全程经 mandate + 预交易门治理;'
      + '生命周期看一个策略在整条状态机上的位置,并可按行钻取每个已追踪因子的纸面前向回放。注意:交易台、实盘'
      + '路线、GoNoGo 证据这几个页面各自有自己独立的操作/确认流程,不在本页"不发起任何操作"的静态承诺范围内。',
    descEn: 'Dry-Run Pipeline is a runtime chokepoint: weights pass pre-trade risk + kill-switch, get paper-filled, '
      + 'and reconciled, all hash-chain logged — a pure in-process simulation that makes no external network '
      + 'requests; Carry Readiness, GoNoGo Evidence, and Live Trading aggregate the evidence and the Phase 3A '
      + 'human-approval stages needed to reach live; Live Desk connects to real exchange testnet/demo REST '
      + "endpoints (not the dry-run pipeline's in-process simulation) and can submit testnet orders via a "
      + 'double-confirmation SUBMIT button — testnet/demo only, zero real capital, governed end-to-end by mandate '
      + '+ pre-trade gate; Lifecycle shows where a strategy sits on the state machine, with a row-level drill-down '
      + "into each tracked alpha's forward paper-track replay. Note: Live Desk, Live Trading, and GoNoGo Evidence "
      + 'each have their own action/confirmation flow and are not covered by this page\'s blanket "triggers '
      + 'nothing" promise.',
  },
  {
    group: 'system',
    titleZh: '治理与系统 — 系统本身的治理层',
    titleEn: 'Governance & system — the governance layer over the system itself',
    descZh: '晋级审批是因子/策略状态推进时的多方人工把关记录;事件流是审计日志;系统健康看服务与安全原语的实时'
      + '状态;系统完整度以真实代码仓库内省为地基,诚实列出哪些子系统是真实构建、哪些仍是占位,是唯一的诚实盘点入口。',
    descEn: 'Promotion Queue records the multi-party human sign-off each time a factor/strategy state advances; '
      + 'Events is the audit log; System Health shows live service and safety-primitive status; System Completeness '
      + 'is grounded in real repo introspection and honestly lists which subsystems are really built versus still '
      + 'placeholders — the single source of truth for that.',
  },
] as const;

/** First 5-minute path: data → discovery → screening. Hardcoded to real, existing paths only. */
const QUICK_START_PATHS: readonly string[] = ['/research/data-catalog', '/research/runs', '/research/ic-panel'];

function PageLinkCard({ path }: { path: string }) {
  const page = PAGES.find(p => p.path === path);
  if (!page) return null; // defensive: never invent a link, never crash if a route is renamed
  return (
    <Link to={page.path}
      className="lift group flex min-w-0 items-center gap-3 rounded-tile border border-line bg-inset/40 px-3.5 py-3 hover:border-line-strong hover:bg-elevated/40">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-[10px] font-bold text-faint transition-colors group-hover:text-accent"
        style={{ background: 'var(--inset)', boxShadow: 'inset 0 0 0 1px var(--line)' }}>
        {page.icon}
      </span>
      <span className="min-w-0 leading-tight">
        <span className="block truncate text-[13px] font-medium text-ink">{page.zh}</span>
        <span className="block truncate text-2xs text-faint">{page.en}</span>
      </span>
      <span className="ml-auto shrink-0 text-faint transition-colors group-hover:text-accent">→</span>
    </Link>
  );
}

export default function GettingStarted() {
  const quickPages = QUICK_START_PATHS.map(p => PAGES.find(pg => pg.path === p)).filter(Boolean) as typeof PAGES[number][];

  return (
    <PageShell>
      <PageHeader title="Getting Started" titleZh="新手引导"
        subtitle={bi('QUANTOS 是什么、量化研究要走哪几步、从哪里开始 — 纯说明页面,不发起请求,不触发任何操作',
          'What QUANTOS is, the research lifecycle, and where to start — a static page that makes no requests and triggers nothing')} />

      {/* ---- 是什么 + 安全边界 ---- */}
      <Panel eyebrow={bi('QUANTOS 是什么', 'WHAT THIS IS')}
        title={bi('AI 辅助的量化研究与干跑模拟平台', 'AI-assisted quant research & dry-run simulation platform')}>
        <div className="flex flex-col gap-2.5">
          <p className="max-w-3xl text-[13px] leading-relaxed text-muted">
            {bi('QUANTOS 用于发现候选交易因子、做统计验证、构建组合、并跑端到端的干跑(纸面)执行——覆盖量化研究从原始数据到模拟成交的完整链路。',
              'QUANTOS discovers candidate trading factors, validates them statistically, constructs portfolios, and runs end-to-end dry-run (paper) execution — the full chain from raw data to simulated fills.')}
          </p>
          <p className="max-w-3xl text-[13px] leading-relaxed text-muted">
            {bi('当前就绪等级为 L2_DRY_RUN_ONLY:只做研究与模拟,不做实盘下单。真正走到实盘需要经过 Phase 3A 定义的多方人工批准流程——本页任何链接都不会触发那个流程,也不会提交任何订单。',
              'Current readiness level is L2_DRY_RUN_ONLY — research and simulation only, no live order submission. Reaching live trading requires the multi-party human-approval process defined in Phase 3A; nothing on this page triggers that process or submits any order.')}
          </p>
          <p className="max-w-3xl text-[13px] leading-relaxed text-muted">
            {bi('你会在各页面看到的每一条夏普 / IC / 回撤数字,都是历史模拟或诊断读数,不是对未来收益的承诺。',
              'Every Sharpe / IC / drawdown figure you will encounter is a historical simulation or diagnostic reading, not a promise of future returns.')}
          </p>
          {/* min-w-0 overrides the flex-item default min-width:auto, and [overflow-wrap:anywhere] gives these
              unbroken FLAG=value tokens an internal break point, so a pill that's wider than the viewport wraps
              inside itself instead of silently clipping at the AppShell's overflow-x-hidden edge (narrow phones). */}
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Chip label="L2_DRY_RUN_ONLY" tone="info" className="max-w-full min-w-0 break-words [overflow-wrap:anywhere]" />
            <Chip label="LIVE_TRADING_ENABLED=false" tone="neg" className="max-w-full min-w-0 break-words [overflow-wrap:anywhere]" />
            <Chip label="REAL_BROKER_ENABLED=false" tone="neg" className="max-w-full min-w-0 break-words [overflow-wrap:anywhere]" />
            <Chip label="ALLOW_ORDER_SUBMISSION=false" tone="neg" className="max-w-full min-w-0 break-words [overflow-wrap:anywhere]" />
            <Chip label="ALLOW_AI_DIRECT_TRADING=false" tone="neg" className="max-w-full min-w-0 break-words [overflow-wrap:anywhere]" />
            <Chip label="REQUIRE_HUMAN_APPROVAL=true" tone="pos" className="max-w-full min-w-0 break-words [overflow-wrap:anywhere]" />
          </div>
        </div>
      </Panel>

      {/* ---- 第一次来?最短路径 ---- */}
      <Panel eyebrow={bi('第一次来?', 'FIRST TIME HERE?')}
        title={bi('5 分钟最短路径:数据 → 发现 → 初筛', '5-minute shortest path: data → discovery → screening')}>
        <p className="mb-4 max-w-3xl text-[13px] leading-relaxed text-muted">
          {bi('先看数据目录搞清楚系统喂了什么数据,再去研究运行看已经跑过什么、需要时触发新的,最后到 IC 面板横向比较候选因子的初筛诊断——三步覆盖「数据 → 发现 → 初筛」。',
            'Start at Data Catalog to see what data feeds the system, then Research Runs to see what has already run (and trigger new ones if needed), then IC Panel to compare candidate factors side by side — three stops covering data → discovery → screening.')}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          {quickPages.map((page, i) => (
            <React.Fragment key={page.path}>
              {/* Only shown once the row is wide enough (2xl main canvas) for all three cards + both
                  arrows to fit on one line — below that, flex-wrap would otherwise wrap a card without
                  its preceding arrow, leaving a clipped/orphaned glyph at the line break. Card order
                  (and the "数据 → 发现 → 初筛" copy above) already conveys the sequence without it. */}
              {i > 0 && <span className="hidden text-lg text-faint 2xl:inline">→</span>}
              <div className="w-full max-w-xs sm:w-72">
                <PageLinkCard path={page.path} />
              </div>
            </React.Fragment>
          ))}
        </div>
      </Panel>

      {/* ---- 全流程分阶段讲解 ---- */}
      <Panel eyebrow={bi('研究全流程', 'THE RESEARCH LIFECYCLE')}
        title={bi('六个研究阶段,总览为入口,对应顶部导航的六个工作分组', 'Six research stages, with Overview as the entry point, mapped to six work groups in the top navigation')}>
        <ol className="flex flex-col gap-7">
          {STAGES.map((stage, i) => {
            const group = GROUPS.find(g => g.id === stage.group)!;
            const pages = PAGES.filter(p => p.group === stage.group);
            return (
              <li key={stage.group} className="relative pl-10">
                <span className="absolute left-0 top-0.5 grid h-7 w-7 place-items-center rounded-full text-[12px] font-bold text-ink"
                  style={{ background: 'var(--grad)', boxShadow: 'var(--glow-accent)' }}>
                  {i + 1}
                </span>
                {i < STAGES.length - 1 && (
                  <span className="absolute left-[13px] top-8 h-[calc(100%-0.25rem)] w-px bg-line" />
                )}
                <div className="mb-1 text-2xs font-semibold uppercase tracking-[0.14em] text-faint">
                  {group.zh} <span className="opacity-60">{group.en}</span>
                </div>
                <h3 className="text-[15px] font-semibold leading-snug text-ink">
                  {stage.titleZh} <span className="ml-1.5 text-[13px] font-medium text-faint">{stage.titleEn}</span>
                </h3>
                <p className="mt-1.5 max-w-3xl text-[13px] leading-relaxed text-muted">
                  {bi(stage.descZh, stage.descEn)}
                </p>
                <div className="mt-3.5 grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
                  {pages.map(p => <PageLinkCard key={p.path} path={p.path} />)}
                </div>
              </li>
            );
          })}
        </ol>
      </Panel>

      <div className="rounded-md border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('本页为纯静态说明,不发起任何网络请求、不触发任何研究运行或下单;上面每个链接都指向本系统中真实存在、可用的页面。',
          'This page is purely static — it makes no network requests and triggers no research run or order; every link above points to a real, working page in this system.')}
      </div>
    </PageShell>
  );
}
