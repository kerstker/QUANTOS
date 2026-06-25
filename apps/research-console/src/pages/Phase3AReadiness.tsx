import React from 'react';
import { fetchPhase3AReadinessSnapshot } from '@/lib/api/research-console-api';
import { mockPhase3AReadiness } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type {
  Phase3ABlocker, Phase3ACriterion, Phase3AReadinessPayload,
} from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, Chip, type Column,
} from '@/ui/primitives';
import { RadialGauge, Donut, Histogram } from '@/ui/charts';
import { num, pct, statusTone, type Tone } from '@/ui/format';

/* ---------- bilingual lookups (presentation only) ------------------------- */
const criterionZh: Record<string, string> = {
  E01: 'Phase 2H 治理批准', E02: '券商测试环境账户确认', E03: '法务与合规批准',
  E04: '测试凭证进入 Vault', E05: '代码中无凭证', E06: '网络访问已审查',
  E07: '券商准备清单', E08: '熔断开关已测试', E09: '回滚已测试',
  E10: '灾备手册已批准', E11: 'UAT 计划已批准', E12: '应急响应已批准',
  E13: '无生产凭证', E14: '无生产交易', E15: 'Phase 2G.1 测试通过',
};
const categoryZh: Record<string, string> = {
  Governance: '治理', Broker: '券商', Compliance: '合规', Credentials: '凭证',
  Security: '安全', Network: '网络', Operations: '运营', Risk: '风险',
  DR: '灾备', UAT: '用户验收', IR: '事件响应', Technical: '技术',
};
const ownerZh: Record<string, string> = {
  CTO: 'CTO', 'Broker Relationship Owner': '券商关系负责人', 'Compliance Officer': '合规官',
  'Security Officer': '安全负责人', 'Operations Lead': '运营负责人',
  'Trading System Owner': '交易系统负责人', 'Risk Officer': '风险负责人',
};
const evidenceZh: Record<string, string> = {
  'Governance signoff register': '治理签署登记', 'Broker response intake': '券商回复收集表',
  'Legal/compliance checklist': '法务与合规清单', 'Vault path and access policy': 'Vault 路径与访问策略',
  'Governance scanner report': '治理扫描报告', 'Network/IP whitelist review': '网络与 IP 白名单审查',
  'Broker readiness checklist': '券商准备清单', 'Kill switch drill report': '熔断演练报告',
  'Rollback drill report': '回滚演练报告', 'DR dry-run report': '灾备干跑报告',
  'UAT dry-run report': 'UAT 干跑报告', 'Incident response plan': '事件响应计划',
  'Dry-run adapter gate evidence': '干跑适配器门禁证据',
};
const blockerZh: Record<string, string> = {
  E01: '治理签名未完成', E02: '券商测试环境未确认', E03: '法务与合规批准未完成',
  E04: '测试凭证未在 Vault 中配置', E06: '网络访问未审查',
};
const nextActionZh: Record<string, string> = {
  'Collect required role signatures': '收集所需角色签名',
  'Send broker request package': '发送券商请求材料',
  'Review broker agreement and test scope': '审查券商协议与测试范围',
  'Prepare Vault-only intake workflow': '准备仅通过 Vault 接收凭证的流程',
  'Collect broker endpoint and whitelist details': '收集券商端点与白名单信息',
};
const dependencyZh: Record<string, string> = {
  None: '无', 'Broker response': '券商回复', E02: 'E02',
};

const cZh = (c: Phase3ACriterion) => bi(criterionZh[c.id] ?? c.criterion, c.criterion);
const safetyBoundary: ReadonlyArray<[string, string]> = [
  ['无真实 CTP/FIX 实现', 'No real CTP/FIX implementation'],
  ['无券商 SDK 导入', 'No broker SDK imports'],
  ['无 socket 连接', 'No socket connections'],
  ['无凭证文件', 'No credential files'],
  ['无实盘下单 API', 'No live order APIs'],
];

const decisionTone: Record<string, Tone> = {
  NO_GO: 'neg', GO_WITH_CONDITIONS: 'warn', GO_FOR_PHASE_3A: 'pos',
};
const statusOrder = ['APPROVED', 'PENDING', 'REJECTED'] as const;
const statusZh: Record<string, string> = { APPROVED: '已通过', PENDING: '待处理', REJECTED: '已拒绝' };

export default function Phase3AReadiness() {
  const { payload, apiState, error, usingBackend } =
    useConsoleSnapshot<Phase3AReadinessPayload>(s => fetchPhase3AReadinessSnapshot(s));

  const readiness = usingBackend ? payload!.readiness : mockPhase3AReadiness;
  const { criteria, blockers, approved_count, total_count } = readiness;
  const pending = total_count - approved_count;
  const approvedFrac = total_count > 0 ? approved_count / total_count : 0;

  /* status distribution for the gauge legend + histogram */
  const statusCounts = statusOrder.map(s => ({
    status: s,
    count: criteria.filter(c => c.status === s).length,
  }));

  /* criteria grouped by category -> Donut composition */
  const categoryCounts = new Map<string, number>();
  criteria.forEach(c => categoryCounts.set(c.category, (categoryCounts.get(c.category) ?? 0) + 1));
  const donutTones: Tone[] = ['accent', 'info', 'warn', 'special', 'pos', 'neg', 'muted'];
  const donutSegments = Array.from(categoryCounts.entries()).map(([cat, n], i) => ({
    label: bi(categoryZh[cat] ?? cat, cat),
    value: n,
    tone: donutTones[i % donutTones.length],
  }));

  /* per-category approval progress */
  const categoryProgress = Array.from(categoryCounts.keys()).map(cat => {
    const inCat = criteria.filter(c => c.category === cat);
    const ok = inCat.filter(c => c.status === 'APPROVED').length;
    return { cat, ok, total: inCat.length };
  }).sort((a, b) => b.total - a.total);

  /* histogram of remaining (non-approved) criteria by category */
  const remainingBins = Array.from(categoryCounts.keys())
    .map(cat => ({
      label: bi(categoryZh[cat] ?? cat, cat),
      count: criteria.filter(c => c.category === cat && c.status !== 'APPROVED').length,
    }))
    .filter(b => b.count > 0);

  const criteriaCols: Column<Phase3ACriterion>[] = [
    { key: 'id', header: 'ID', mono: true, width: '64px', render: c => <span className="font-semibold text-accent">{c.id}</span> },
    { key: 'criterion', header: bi('准入标准', 'Criterion'), render: c => <span className="font-medium text-ink">{cZh(c)}</span> },
    { key: 'category', header: bi('类别', 'Category'), render: c => <span className="text-muted">{bi(categoryZh[c.category] ?? c.category, c.category)}</span> },
    { key: 'owner', header: bi('负责人', 'Owner'), render: c => <span className="text-muted">{bi(ownerZh[c.owner] ?? c.owner, c.owner)}</span> },
    { key: 'status', header: bi('状态', 'Status'), render: c => <StatusChip value={c.status} /> },
    { key: 'evidence', header: bi('证据', 'Evidence'), render: c => <span className="text-faint">{bi(evidenceZh[c.evidence] ?? c.evidence, c.evidence)}</span> },
  ];

  const blockerCols: Column<Phase3ABlocker>[] = [
    { key: 'id', header: 'ID', mono: true, width: '64px', render: b => <span className="font-semibold text-warn">{b.id}</span> },
    { key: 'blocker', header: bi('阻塞项', 'Blocker'), render: b => <span className="text-ink">{bi(blockerZh[b.id] ?? b.blocker, b.blocker)}</span> },
    { key: 'owner', header: bi('负责人', 'Owner'), render: b => <span className="text-muted">{bi(ownerZh[b.owner] ?? b.owner, b.owner)}</span> },
    { key: 'dependency', header: bi('依赖', 'Depends On'), render: b => (
      b.dependency === 'None'
        ? <Chip label={bi('可独立推进', 'Independent')} tone="info" />
        : <Chip label={bi(dependencyZh[b.dependency] ?? b.dependency, b.dependency)} tone="warn" />
    ) },
    { key: 'next', header: bi('下一步行动', 'Next Action'), render: b => <span className="text-muted">{bi(nextActionZh[b.next_action] ?? b.next_action, b.next_action)}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Phase 3A Readiness" titleZh="Phase 3A 准入状态"
        subtitle={bi('迈向 L3 券商测试环境的准入门禁 — 15 项标准必须全部 APPROVED 方可放行(只读快照)',
          'Entry gate toward L3 broker test-env — all 15 criteria must be APPROVED to advance (read-only snapshot)')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Chip label={bi('当前', 'Now')} tone="info" />
            <StatusChip value={readiness.current_level} />
            <span className="text-faint">→</span>
            <Chip label={bi('目标', 'Target')} tone="muted" />
            <StatusChip value={readiness.target_level} />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={error} />

      {/* NO_GO decision banner — most emphatic element */}
      <Panel tone={decisionTone[readiness.decision] ?? 'neg'} bodyClass="!p-0">
        <div className="flex flex-col gap-3 p-4 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap items-center gap-3">
            <span className="rounded-lg px-3 py-1.5 text-lg font-extrabold tracking-tight"
              style={{ color: 'var(--neg)', background: 'color-mix(in srgb, var(--neg) 14%, transparent)',
                border: '1px solid color-mix(in srgb, var(--neg) 40%, transparent)' }}>
              {readiness.decision}
            </span>
            <div className="min-w-0">
              <div className="text-sm font-semibold text-ink">
                {bi('Phase 3A 实施未授权', 'Phase 3A implementation NOT authorized')}
              </div>
              <div className="text-2xs text-muted">
                {bi('阶段状态 BLOCKED · 实施授权 false · 须全部 15 项标准通过',
                  'Phase BLOCKED · authorization false · all 15 criteria required')}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip value={readiness.phase_status} />
            <GateDot ok={readiness.implementation_authorized} label={bi('实施授权', 'Impl. Authorized')} />
          </div>
        </div>
      </Panel>

      <KpiStrip cols={5}>
        <StatTile label={bi('已通过标准', 'Approved')} value={num(approved_count)} sub={`/ ${num(total_count)}`} tone="pos" />
        <StatTile label={bi('待处理', 'Pending')} value={num(pending)} tone="warn" />
        <StatTile label={bi('就绪度', 'Readiness')} value={pct(approvedFrac, 0)} accent />
        <StatTile label={bi('阻塞项', 'Blockers')} value={num(blockers.length)} tone={blockers.length ? 'neg' : 'pos'} />
        <StatTile label={bi('类别覆盖', 'Categories')} value={num(categoryCounts.size)} tone="info" />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* readiness gauge */}
        <Panel eyebrow={bi('门禁评分', 'Gate Score')} title={bi('准入就绪度', 'Entry Readiness')}>
          <div className="flex flex-col items-center gap-3">
            <RadialGauge value={approvedFrac} max={1} size={168} label={bi('已通过', 'approved')} tone={statusTone(readiness.decision)} />
            <div className="grid w-full grid-cols-3 gap-2">
              {statusCounts.map(s => (
                <div key={s.status} className="flex flex-col items-center gap-1 rounded-lg border border-line bg-inset/40 py-2">
                  <StatusChip value={s.status} />
                  <span className="tnum text-lg font-bold text-ink">{num(s.count)}</span>
                  <span className="text-2xs text-faint">{bi(statusZh[s.status] ?? s.status, s.status)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        {/* per-category approval progress */}
        <Panel className="xl:col-span-2" eyebrow={bi('按类别', 'By Category')}
          title={bi('各类别标准通过进度', 'Approval Progress per Category')}
          right={<Chip label={`${approved_count}/${total_count}`} tone="accent" />}>
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-2">
            {categoryProgress.map(({ cat, ok, total }) => (
              <div key={cat} className="flex flex-col gap-1">
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="truncate text-muted">{bi(categoryZh[cat] ?? cat, cat)}</span>
                  <span className="tnum shrink-0 text-faint">{ok}/{total}</span>
                </div>
                <ProgressBar value={ok} max={total} tone={ok === total ? 'pos' : ok > 0 ? 'warn' : 'neg'} />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {/* full criteria register */}
      <Panel eyebrow={bi('15 项准入标准', '15 Entry Criteria')} title={bi('准入标准登记表', 'Entry Criteria Register')}
        right={<GateDot ok={approved_count === total_count} label={bi('全部通过', 'All approved')} />}>
        <DataTable columns={criteriaCols} rows={criteria} getKey={c => c.id} dense
          empty={bi('无标准记录', 'No criteria')} />
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* category composition */}
        <Panel eyebrow={bi('构成', 'Composition')} title={bi('标准按类别分布', 'Criteria by Category')}>
          <div className="flex flex-col items-center gap-4 md:flex-row md:items-center md:justify-around">
            <Donut segments={donutSegments} size={148} />
            <div className="grid w-full grid-cols-1 gap-1.5 sm:grid-cols-2 md:w-auto md:grid-cols-1">
              {donutSegments.map(s => (
                <div key={String(s.label)} className="flex items-center justify-between gap-3 text-xs">
                  <span className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-sm" style={{ background: `var(--${s.tone})` }} />
                    <span className="text-muted">{s.label}</span>
                  </span>
                  <span className="tnum text-faint">{num(s.value)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        {/* remaining work by category */}
        <Panel className="xl:col-span-2" eyebrow={bi('剩余工作', 'Remaining')}
          title={bi('未通过标准按类别', 'Outstanding Criteria by Category')}>
          {remainingBins.length
            ? <Histogram bins={remainingBins} height={200} tone="warn" />
            : <div className="flex h-[200px] items-center justify-center text-xs text-pos">
                {bi('所有标准均已通过', 'All criteria approved')}
              </div>}
        </Panel>
      </div>

      {/* remaining blockers */}
      <Panel eyebrow={bi('关键路径', 'Critical Path')} title={bi('剩余阻塞项', 'Remaining Blockers')}
        tone={blockers.length ? 'warn' : undefined}
        right={
          <Chip
            label={bi(`${blockers.length} 项待清除`, `${blockers.length} open`)}
            tone={blockers.length ? 'warn' : 'pos'} />
        }>
        <div className="mb-3 text-2xs text-faint">
          {bi('E02(券商测试环境)是外部关键路径;其未关闭前,E03 / E04 / E06 保持阻塞。E01 可独立推进。',
            'E02 (broker test-env) is the external critical path; E03 / E04 / E06 stay blocked until it closes. E01 can proceed independently.')}
        </div>
        <DataTable columns={blockerCols} rows={blockers} getKey={b => b.id}
          empty={bi('无阻塞项', 'No blockers')} />
      </Panel>

      {/* safety boundary — non-negotiable */}
      <Panel tone="neg" eyebrow={bi('不可逾越', 'Non-negotiable')} title={bi('安全边界', 'Safety Boundary')}>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {safetyBoundary.map(([zh, en]) => (
            <div key={en} className="flex items-center gap-2 rounded-lg border border-neg/30 bg-neg/5 px-3 py-2">
              <GateDot ok={false} />
              <span className="text-xs text-ink">{bi(zh, en)}</span>
            </div>
          ))}
        </div>
      </Panel>
    </PageShell>
  );
}
