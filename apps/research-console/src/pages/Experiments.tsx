import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchExperimentsSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ExperimentsPayload, ResearchExperiment } from '@/lib/types/research';

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

function formatTime(seconds: number) {
  return new Date(seconds * 1000).toISOString().slice(0, 16).replace('T', ' ');
}

function artifactKinds(experiments: ResearchExperiment[]) {
  return experiments.reduce<Record<string, number>>((acc, experiment) => {
    for (const artifact of experiment.artifacts) {
      acc[artifact.kind] = (acc[artifact.kind] ?? 0) + 1;
    }
    return acc;
  }, {});
}

function StageBoard({stageCounts}: {stageCounts: Record<string, number>}) {
  const max = Math.max(...Object.values(stageCounts), 1);
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('实验阶段', 'Experiment Stages')}</h2>
      <div className="space-y-3 text-xs">
        {Object.entries(stageCounts).map(([stage, count]) => (
          <div key={stage} className="grid grid-cols-[7rem_1fr_2rem] items-center gap-2">
            <span className="text-slate-400">{stage}</span>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-cyan-700" style={{width: `${Math.max(4, count / max * 100)}%`}} />
            </div>
            <span className="text-right font-bold text-slate-200">{count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ArtifactBoard({experiments}: {experiments: ResearchExperiment[]}) {
  const kinds = artifactKinds(experiments);
  const max = Math.max(...Object.values(kinds), 1);
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('产物覆盖', 'Artifact Coverage')}</h2>
      <div className="space-y-3 text-xs">
        {Object.entries(kinds).map(([kind, count]) => (
          <div key={kind} className="grid grid-cols-[6rem_1fr_2rem] items-center gap-2">
            <span className="text-slate-400">{kind}</span>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-emerald-700" style={{width: `${Math.max(4, count / max * 100)}%`}} />
            </div>
            <span className="text-right font-bold text-slate-200">{count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ReproducibilityBoard({payload}: {payload: ExperimentsPayload}) {
  const policies = [
    ['CONFIG_HASH', payload.lineage_policy.requires_config_hash],
    ['DATA_SNAPSHOT', payload.lineage_policy.requires_data_snapshot],
    ['METRIC_ARTIFACT', payload.lineage_policy.requires_metric_artifact],
    ['IMMUTABLE_RECORDS', !payload.lineage_policy.mutable_records],
  ];
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm uppercase text-slate-400">{bi('可复现策略', 'Reproducibility Policy')}</h2>
          <div className="mt-1 text-xs text-slate-500">{payload.registry_mode}</div>
        </div>
        <DecisionBadge decision="READ_ONLY" />
      </div>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        {policies.map(([label, passed]) => (
          <div key={String(label)} className="flex items-center justify-between gap-2 rounded bg-slate-800 px-3 py-2">
            <span className="text-slate-300">{label}</span>
            <span className={passed ? 'text-emerald-300' : 'text-red-300'}>{passed ? 'PASS' : 'FAIL'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Experiments() {
  const [payload, setPayload] = useState<ExperimentsPayload | null>(null);
  const [apiState, setApiState] = useState<ConsoleApiState>('disabled');
  const [apiError, setApiError] = useState<string | null>(null);
  const apiEnabled = isResearchConsoleApiEnabled();

  useEffect(() => {
    if (!apiEnabled) {
      setPayload(null);
      setApiState('disabled');
      setApiError(null);
      return;
    }

    const controller = new AbortController();
    setApiState('loading');
    setApiError(null);
    fetchExperimentsSnapshot(controller.signal)
      .then(nextPayload => {
        setPayload(nextPayload);
        setApiState('connected');
      })
      .catch(error => {
        if (controller.signal.aborted) {
          return;
        }
        setPayload(null);
        setApiState('error');
        setApiError(error instanceof Error ? error.message : String(error));
      });

    return () => controller.abort();
  }, [apiEnabled]);

  const usingBackend = apiState === 'connected' && payload !== null;
  const registry = usingBackend && payload ? payload : fallbackExperiments;
  const topExperiments = useMemo(
    () => [...registry.experiments].sort((left, right) => right.metrics.composite_score - left.metrics.composite_score).slice(0, 4),
    [registry.experiments],
  );

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('实验注册表', 'Experiment Registry')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision="L2_DRY_RUN_ONLY" />
              <DecisionBadge decision={registry.registry_mode} />
            </div>
          </div>
          <div className="text-xs text-slate-500 md:text-right">
            <div>{bi('总实验', 'Total')}: {registry.summary.total_experiments}</div>
            <div>{bi('可复现', 'Reproducible')}: {registry.summary.reproducible}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={registry.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <MetricCard label={bi('实验数', 'Experiments')} value={registry.summary.total_experiments} />
        <MetricCard label={bi('通过', 'Approved')} value={registry.summary.approved} />
        <MetricCard label={bi('拒绝', 'Rejected')} value={registry.summary.rejected} />
        <MetricCard label={bi('产物', 'Artifacts')} value={registry.summary.artifact_count} />
        <MetricCard label={bi('可复现率', 'Repro Rate')} value={(registry.summary.reproducible / Math.max(registry.summary.total_experiments, 1) * 100).toFixed(0)} unit="%" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <StageBoard stageCounts={registry.stage_counts} />
        <ArtifactBoard experiments={registry.experiments} />
        <ReproducibilityBoard payload={registry} />
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('Top 实验', 'Top Experiments')}</h2>
        <div className="grid gap-3 xl:grid-cols-4">
          {topExperiments.map(experiment => (
            <div key={experiment.experiment_id} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs">
              <div className="mb-2 flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-bold text-cyan-300">{experiment.experiment_id}</div>
                  <div className="mt-1 truncate text-slate-400">{experiment.owner_agent}</div>
                </div>
                <DecisionBadge decision={experiment.decision} />
              </div>
              <div className="truncate text-slate-200">{experiment.expression}</div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-slate-500">
                <div>IC <span className="text-slate-200">{experiment.metrics.ic.toFixed(4)}</span></div>
                <div>Sharpe <span className="text-slate-200">{experiment.metrics.sharpe.toFixed(2)}</span></div>
                <div>{bi('分数', 'Score')} <span className="text-slate-200">{experiment.metrics.composite_score.toFixed(2)}</span></div>
                <div>{bi('产物', 'Artifacts')} <span className="text-slate-200">{experiment.artifacts.length}</span></div>
              </div>
              <div className="mt-3 truncate text-[10px] text-slate-500">{experiment.reproducibility_hash}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[1120px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">{bi('实验', 'Experiment')}</th>
              <th className="p-2 text-left">Alpha</th>
              <th className="p-2 text-left">{bi('阶段', 'Stage')}</th>
              <th className="p-2 text-left">{bi('决策', 'Decision')}</th>
              <th className="p-2 text-right">IC</th>
              <th className="p-2 text-right">{bi('夏普', 'Sharpe')}</th>
              <th className="p-2 text-right">{bi('换手', 'Turnover')}</th>
              <th className="p-2 text-left">{bi('数据快照', 'Data Snapshot')}</th>
              <th className="p-2 text-left">{bi('配置哈希', 'Config Hash')}</th>
              <th className="p-2 text-left">{bi('创建时间', 'Created')}</th>
            </tr>
          </thead>
          <tbody>
            {registry.experiments.map(experiment => (
              <tr key={experiment.experiment_id} className="border-b border-slate-800/50 hover:bg-slate-800/50">
                <td className="p-2 font-bold text-cyan-300">{experiment.experiment_id}</td>
                <td className="p-2 text-slate-300">{experiment.alpha_id}</td>
                <td className="p-2 text-slate-400">{experiment.stage}</td>
                <td className="p-2"><DecisionBadge decision={experiment.decision} /></td>
                <td className="p-2 text-right text-emerald-300">{experiment.metrics.ic.toFixed(4)}</td>
                <td className="p-2 text-right text-slate-200">{experiment.metrics.sharpe.toFixed(2)}</td>
                <td className="p-2 text-right text-amber-300">{experiment.metrics.turnover.toFixed(2)}</td>
                <td className="p-2 text-slate-500">{experiment.config.data_snapshot_id}</td>
                <td className="p-2 text-slate-500">{experiment.config.config_hash}</td>
                <td className="p-2 text-slate-500">{formatTime(experiment.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
