import React, { useMemo } from 'react';
import { fetchExperimentsSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ExperimentsPayload, ResearchExperiment } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, Chip, type Column,
} from '@/ui/primitives';
import { Histogram, RadialGauge, Donut, BarSeries } from '@/ui/charts';
import { num, pct, fixed, money, compact, shortHash, stamp, statusTone, type Tone } from '@/ui/format';

const fallbackExperiments: ExperimentsPayload = {
  source: 'FRONTEND_EXPERIMENT_REGISTRY_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  registry_mode: 'READ_ONLY_RESEARCH_REGISTRY',
  summary: {
    total_experiments: 3,
    approved: 1,
    rejected: 1,
    needs_more_data: 1,
    artifact_count: 10,
    reproducible: 3,
  },
  experiments: [
    {
      experiment_id: 'exp-alpha-1-v001',
      alpha_id: 'alpha-1',
      expression: '(ts_mean :close 5)',
      owner_agent: 'Macro Analyst',
      stage: 'VALIDATION',
      decision: 'APPROVED',
      created_at: 1_781_222_400,
      execution_mode: 'L2_DRY_RUN_ONLY',
      config: {universe: 'CN_A_SHARE_RESEARCH', horizon_days: 5, cost_bps: 18, seed: 20260612, data_snapshot_id: 'md-snapshot-2026w20', config_hash: 'fallback-config-001'},
      metrics: {ic: 0.061, rank_ic: 0.058, sharpe: 1.18, turnover: 0.34, capacity_estimate: 84_000_000, composite_score: 0.71},
      reproducibility_hash: 'fallback-repro-001',
      artifacts: [
        {artifact_id: 'art-01-config', kind: 'CONFIG', status: 'AVAILABLE', uri: 'artifact://fallback/config.json', content_hash: 'hash-config'},
        {artifact_id: 'art-01-metrics', kind: 'METRICS', status: 'AVAILABLE', uri: 'artifact://fallback/metrics.json', content_hash: 'hash-metrics'},
        {artifact_id: 'art-01-lineage', kind: 'LINEAGE', status: 'AVAILABLE', uri: 'artifact://fallback/lineage.json', content_hash: 'hash-lineage'},
      ],
      lineage: {upstream: ['factor_engine'], downstream: ['paper_trading', 'risk_gate']},
    },
    {
      experiment_id: 'exp-alpha-2-v001',
      alpha_id: 'alpha-2',
      expression: '(ts_rank :volume 20)',
      owner_agent: 'Factor Hunter',
      stage: 'DISCOVERY',
      decision: 'NEEDS_MORE_DATA',
      created_at: 1_781_215_200,
      execution_mode: 'L2_DRY_RUN_ONLY',
      config: {universe: 'CN_A_SHARE_RESEARCH', horizon_days: 20, cost_bps: 21, seed: 20260629, data_snapshot_id: 'md-snapshot-2026w21', config_hash: 'fallback-config-002'},
      metrics: {ic: 0.043, rank_ic: 0.041, sharpe: 0.62, turnover: 0.58, capacity_estimate: 56_000_000, composite_score: 0.44},
      reproducibility_hash: 'fallback-repro-002',
      artifacts: [
        {artifact_id: 'art-02-config', kind: 'CONFIG', status: 'AVAILABLE', uri: 'artifact://fallback/config.json', content_hash: 'hash-config-2'},
        {artifact_id: 'art-02-metrics', kind: 'METRICS', status: 'AVAILABLE', uri: 'artifact://fallback/metrics.json', content_hash: 'hash-metrics-2'},
        {artifact_id: 'art-02-lineage', kind: 'LINEAGE', status: 'AVAILABLE', uri: 'artifact://fallback/lineage.json', content_hash: 'hash-lineage-2'},
      ],
      lineage: {upstream: ['factor_engine'], downstream: ['validation_review']},
    },
    {
      experiment_id: 'exp-alpha-3-v001',
      alpha_id: 'alpha-3',
      expression: '(zscore :returns 10)',
      owner_agent: 'Momentum Specialist',
      stage: 'ARCHIVED',
      decision: 'REJECTED_UNSTABLE',
      created_at: 1_781_208_000,
      execution_mode: 'L2_DRY_RUN_ONLY',
      config: {universe: 'CN_A_SHARE_RESEARCH', horizon_days: 10, cost_bps: 24, seed: 20260646, data_snapshot_id: 'md-snapshot-2026w22', config_hash: 'fallback-config-003'},
      metrics: {ic: 0.031, rank_ic: 0.033, sharpe: 0.41, turnover: 0.82, capacity_estimate: 42_000_000, composite_score: 0.28},
      reproducibility_hash: 'fallback-repro-003',
      artifacts: [
        {artifact_id: 'art-03-config', kind: 'CONFIG', status: 'AVAILABLE', uri: 'artifact://fallback/config.json', content_hash: 'hash-config-3'},
        {artifact_id: 'art-03-metrics', kind: 'METRICS', status: 'AVAILABLE', uri: 'artifact://fallback/metrics.json', content_hash: 'hash-metrics-3'},
        {artifact_id: 'art-03-review', kind: 'REVIEW_NOTE', status: 'AVAILABLE', uri: 'artifact://fallback/review.md', content_hash: 'hash-review-3'},
      ],
      lineage: {upstream: ['factor_engine'], downstream: ['validation_review']},
    },
  ],
  stage_counts: {DISCOVERY: 1, VALIDATION: 1, PAPER_REPLAY: 0, RISK_REVIEW: 0, ARCHIVED: 1},
  lineage_policy: {
    requires_config_hash: true,
    requires_data_snapshot: true,
    requires_metric_artifact: true,
    mutable_records: false,
  },
};

const stageZh: Record<string, string> = {
  DISCOVERY: bi('发现', 'Discovery'), VALIDATION: bi('验证', 'Validation'),
  PAPER_REPLAY: bi('纸面回放', 'Paper'), RISK_REVIEW: bi('风险评审', 'Risk'),
  ARCHIVED: bi('归档', 'Archived'),
};

const stageTone: Record<string, Tone> = {
  DISCOVERY: 'info', VALIDATION: 'accent', PAPER_REPLAY: 'warn',
  RISK_REVIEW: 'special', ARCHIVED: 'muted',
};

function artifactKinds(experiments: ResearchExperiment[]): Record<string, number> {
  return experiments.reduce<Record<string, number>>((acc, experiment) => {
    for (const artifact of experiment.artifacts) {
      acc[artifact.kind] = (acc[artifact.kind] ?? 0) + 1;
    }
    return acc;
  }, {});
}

export default function Experiments() {
  const { payload, apiState, error, usingBackend } =
    useConsoleSnapshot<ExperimentsPayload>(s => fetchExperimentsSnapshot(s));

  const registry = usingBackend ? payload! : fallbackExperiments;
  const experiments = registry.experiments;
  const { summary, lineage_policy } = registry;

  const reproRate = summary.reproducible / Math.max(summary.total_experiments, 1);

  const stageBins = useMemo(
    () => Object.entries(registry.stage_counts).map(([stage, count]) => ({
      label: stageZh[stage] ?? stage, count,
    })),
    [registry.stage_counts],
  );

  const artifactBins = useMemo(() => {
    const kinds = artifactKinds(experiments);
    return Object.entries(kinds).map(([kind, count]) => ({ label: kind, count }));
  }, [experiments]);

  const topExperiments = useMemo(
    () => [...experiments]
      .sort((a, b) => b.metrics.composite_score - a.metrics.composite_score)
      .slice(0, 4),
    [experiments],
  );

  const decisionSegments = useMemo(() => [
    { label: bi('通过', 'Approved'), value: summary.approved, tone: 'pos' as Tone },
    { label: bi('待补数据', 'Needs Data'), value: summary.needs_more_data, tone: 'warn' as Tone },
    { label: bi('拒绝', 'Rejected'), value: summary.rejected, tone: 'neg' as Tone },
  ].filter(s => s.value > 0), [summary.approved, summary.needs_more_data, summary.rejected]);

  const lineagePolicyRows: Array<{ label: React.ReactNode; ok: boolean }> = [
    { label: bi('要求配置哈希', 'Requires config hash'), ok: lineage_policy.requires_config_hash },
    { label: bi('要求数据快照', 'Requires data snapshot'), ok: lineage_policy.requires_data_snapshot },
    { label: bi('要求指标产物', 'Requires metric artifact'), ok: lineage_policy.requires_metric_artifact },
    { label: bi('记录不可变', 'Immutable records'), ok: !lineage_policy.mutable_records },
  ];

  const expCols: Column<ResearchExperiment>[] = [
    {
      key: 'exp', header: bi('实验 / 因子', 'Experiment / Alpha'), render: e => (
        <div className="min-w-0">
          <div className="mono truncate font-medium text-ink">{e.experiment_id}</div>
          <div className="truncate text-2xs text-faint">{e.alpha_id} · {e.expression}</div>
        </div>
      ),
    },
    { key: 'owner', header: bi('负责人', 'Owner'), render: e => <span className="text-muted">{e.owner_agent}</span> },
    {
      key: 'stage', header: bi('阶段', 'Stage'),
      render: e => <Chip label={stageZh[e.stage] ?? e.stage} tone={stageTone[e.stage] ?? 'muted'} />,
    },
    { key: 'decision', header: bi('决策', 'Decision'), render: e => <StatusChip value={String(e.decision)} /> },
    { key: 'ic', header: 'IC', align: 'right', mono: true, render: e => fixed(e.metrics.ic, 4) },
    { key: 'ricic', header: bi('秩 IC', 'Rank IC'), align: 'right', mono: true, render: e => fixed(e.metrics.rank_ic, 4) },
    {
      key: 'sharpe', header: bi('夏普', 'Sharpe'), align: 'right', mono: true,
      render: e => <span className={e.metrics.sharpe >= 1 ? 'text-pos' : 'text-ink'}>{fixed(e.metrics.sharpe, 2)}</span>,
    },
    {
      key: 'turnover', header: bi('换手', 'Turnover'), align: 'right', mono: true,
      render: e => <span className={e.metrics.turnover > 0.6 ? 'text-warn' : 'text-ink'}>{pct(e.metrics.turnover, 0)}</span>,
    },
    { key: 'capacity', header: bi('容量', 'Capacity'), align: 'right', mono: true, render: e => money(e.metrics.capacity_estimate) },
    {
      key: 'score', header: bi('综合分', 'Score'), width: '140px', render: e => (
        <div className="flex items-center gap-2">
          <ProgressBar value={e.metrics.composite_score} tone={statusTone(String(e.decision))} />
          <span className="tnum w-10 text-right text-muted">{fixed(e.metrics.composite_score, 2)}</span>
        </div>
      ),
    },
    { key: 'artifacts', header: bi('产物', 'Artifacts'), align: 'right', mono: true, render: e => num(e.artifacts.length) },
    { key: 'snapshot', header: bi('数据快照', 'Snapshot'), mono: true, render: e => <span className="text-faint">{e.config.data_snapshot_id}</span> },
    { key: 'repro', header: bi('复现哈希', 'Repro Hash'), mono: true, render: e => <span className="text-info">{shortHash(e.reproducibility_hash, 12)}</span> },
    { key: 'created', header: bi('创建时间', 'Created'), mono: true, render: e => <span className="text-faint">{stamp(e.created_at)}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Experiment Registry" titleZh="实验注册表"
        subtitle={bi('不可变、可复现的研究试验账本 · 阶段、决策、产物谱系与多重检验治理',
          'Immutable, reproducible research-trial ledger — stages, decisions, artifact lineage & multiple-testing governance')}
        actions={
          <div className="flex items-center gap-2">
            <Chip label="L2_DRY_RUN_ONLY" tone="info" />
            <Chip label={registry.registry_mode} tone="accent" />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={error} />

      <KpiStrip cols={6}>
        <StatTile label={bi('实验总数', 'Experiments')} value={num(summary.total_experiments)} accent />
        <StatTile label={bi('通过', 'Approved')} value={num(summary.approved)} tone="pos" />
        <StatTile label={bi('待补数据', 'Needs Data')} value={num(summary.needs_more_data)} tone="warn" />
        <StatTile label={bi('拒绝', 'Rejected')} value={num(summary.rejected)} tone="neg" />
        <StatTile label={bi('产物总数', 'Artifacts')} value={num(summary.artifact_count)} tone="info" />
        <StatTile label={bi('可复现率', 'Repro Rate')} value={pct(reproRate, 0)}
          sub={bi(`${summary.reproducible} / ${summary.total_experiments} 可复现`, `${summary.reproducible} / ${summary.total_experiments} reproducible`)}
          tone={reproRate >= 1 ? 'pos' : 'warn'} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('生命周期', 'Lifecycle')}
          title={bi('实验阶段分布', 'Experiment Stage Distribution')}>
          <Histogram bins={stageBins} height={210} tone="accent" />
        </Panel>

        <Panel eyebrow={bi('决策构成', 'Decision Mix')} title={bi('决策结果分布', 'Decision Outcomes')}>
          <div className="flex items-center gap-4">
            <Donut segments={decisionSegments} size={150} />
            <div className="flex min-w-0 flex-1 flex-col gap-2.5">
              {decisionSegments.map(s => (
                <div key={String(s.label)} className="flex items-center justify-between gap-2">
                  <Chip label={s.label} tone={s.tone} />
                  <span className="tnum text-sm font-semibold text-ink">{num(s.value)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('产物覆盖', 'Artifact Coverage')} title={bi('产物类型分布', 'Artifact Kinds')}>
          <Histogram bins={artifactBins} height={190} tone="info" />
        </Panel>

        <Panel eyebrow={bi('可复现治理', 'Reproducibility Governance')}
          title={bi('谱系策略', 'Lineage Policy')}
          right={<Chip label={bi('只读', 'READ_ONLY')} tone="info" />}>
          <div className="flex flex-col gap-3">
            {lineagePolicyRows.map((r, i) => (
              <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-inset/40 px-3 py-2">
                <span className="text-xs text-muted">{r.label}</span>
                <GateDot ok={r.ok} label={r.ok ? bi('强制', 'ENFORCED') : bi('未启用', 'OFF')} />
              </div>
            ))}
          </div>
        </Panel>

        <Panel eyebrow={bi('谱系', 'Lineage')} title={bi('可复现完整性', 'Reproducibility Integrity')}>
          <div className="flex flex-col items-center gap-2">
            <RadialGauge value={reproRate} max={1} size={148}
              label={bi('可复现', 'reproducible')}
              tone={reproRate >= 1 ? 'pos' : 'warn'} />
            <div className="text-center text-2xs text-faint">
              {bi(`${summary.reproducible} 个实验携带完整配置哈希 / 数据快照 / 指标产物`,
                `${summary.reproducible} experiments carry full config-hash / data-snapshot / metric artifacts`)}
            </div>
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('最佳候选', 'Top Candidates')} title={bi('综合分前列实验', 'Top Experiments by Composite Score')}
        right={<Chip label={bi(`${experiments.length} 个实验`, `${experiments.length} experiments`)} tone="info" />}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {topExperiments.map(e => (
            <div key={e.experiment_id} className="flex flex-col gap-2.5 rounded-xl border border-line bg-gradient-to-b from-elevated/40 to-panel p-3.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="mono truncate text-xs font-semibold text-accent">{e.experiment_id}</div>
                  <div className="truncate text-2xs text-faint">{e.owner_agent}</div>
                </div>
                <StatusChip value={String(e.decision)} />
              </div>
              <div className="mono truncate text-2xs text-muted">{e.expression}</div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-2xs">
                <KvLine k="IC" v={fixed(e.metrics.ic, 4)} />
                <KvLine k={bi('夏普', 'Sharpe')} v={fixed(e.metrics.sharpe, 2)} />
                <KvLine k={bi('换手', 'Turnover')} v={pct(e.metrics.turnover, 0)} />
                <KvLine k={bi('容量', 'Capacity')} v={compact(e.metrics.capacity_estimate)} />
              </div>
              <div className="flex items-center gap-2">
                <ProgressBar value={e.metrics.composite_score} tone={statusTone(String(e.decision))} />
                <span className="tnum w-9 text-right text-2xs text-muted">{fixed(e.metrics.composite_score, 2)}</span>
              </div>
              <div className="mono truncate text-2xs text-info">{shortHash(e.reproducibility_hash, 16)}</div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel eyebrow={bi('全部试验', 'All Trials')} title={bi('实验注册表', 'Experiment Registry')}>
        <DataTable columns={expCols} rows={experiments} dense getKey={e => e.experiment_id} />
      </Panel>

      <Panel eyebrow={bi('阶段计数', 'Stage Counts')} title={bi('阶段流转条形图', 'Stage Throughput')}>
        <BarSeries
          data={stageBins.map(b => b.count)}
          labels={stageBins.map(b => b.label)}
          height={150}
          posTone="accent"
          valueFormat={v => num(v)}
        />
      </Panel>
    </PageShell>
  );
}

function KvLine({ k, v }: { k: React.ReactNode; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-1">
      <span className="text-faint">{k}</span>
      <span className="tnum font-medium text-ink">{v}</span>
    </div>
  );
}
