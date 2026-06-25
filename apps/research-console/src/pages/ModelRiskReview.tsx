import React, { useMemo } from 'react';
import { fetchModelRiskReviewSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ModelRiskModel, ModelRiskReviewPayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, Chip, GateDot, ProgressBar, KeyValue, type Column,
} from '@/ui/primitives';
import { RadialGauge, DivergingBars, Histogram } from '@/ui/charts';
import { num, pct, shortHash, statusTone, type Tone } from '@/ui/format';

const fallbackReview: ModelRiskReviewPayload = {
  source: 'FRONTEND_MODEL_RISK_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  model_risk_mode: 'READ_ONLY_MODEL_RISK_REVIEW',
  review_standard: 'PRINCIPLES_BASED_MODEL_RISK_MANAGEMENT',
  summary: {
    models: 3,
    high_risk_models: 2,
    open_issues: 2,
    open_high_issues: 1,
    avg_residual_risk: 0.34,
    independent_validation_complete: false,
    live_use_authorized: false,
  },
  models: [
    {
      model_id: 'MRM-ALPHA-001',
      name: 'Alpha factor alpha-1',
      model_type: 'ALPHA_MODEL',
      linked_component: 'alpha-1',
      owner: 'Factor Hunter',
      risk_tier: 'HIGH',
      review_status: 'VALIDATION_REQUIRED',
      intended_use: 'Research ranking, validation, portfolio simulation, and risk review only',
      prohibited_use: 'Live trading authorization, broker routing, credential handling, or production order decisions',
      validation_scores: {conceptual_soundness: 0.76, implementation_verification: 0.82, outcomes_analysis: 0.68, ongoing_monitoring: 0.63},
      residual_risk: 0.28,
      evidence: {experiment_id: 'exp-alpha-1-v001', config_hash: 'fallback-config', data_snapshot_id: 'md-snapshot-fallback', stress_run_id: null, backtest_run_id: 'bt-alpha-1'},
      limitations: ['Offline deterministic replay, not broker-confirmed production behavior'],
      findings: ['Independent validation evidence incomplete'],
      compensating_controls: ['Read-only console', 'L2 dry-run execution boundary'],
    },
    {
      model_id: 'MRM-RISK-001',
      name: 'Research risk gate',
      model_type: 'RISK_MODEL',
      linked_component: 'risk-gate',
      owner: 'Risk',
      risk_tier: 'CRITICAL',
      review_status: 'INDEPENDENT_REVIEW_REQUIRED',
      intended_use: 'Research ranking, validation, portfolio simulation, and risk review only',
      prohibited_use: 'Live trading authorization, broker routing, credential handling, or production order decisions',
      validation_scores: {conceptual_soundness: 0.66, implementation_verification: 0.75, outcomes_analysis: 0.58, ongoing_monitoring: 0.55},
      residual_risk: 0.43,
      evidence: {experiment_id: null, config_hash: 'fallback-risk', data_snapshot_id: 'md-snapshot-fallback', stress_run_id: 'stress-alpha-1', backtest_run_id: null},
      limitations: ['Proxy costs and haircuts require independent calibration'],
      findings: ['Stress calibration requires independent challenge'],
      compensating_controls: ['Read-only console', 'Phase 3A NO_GO gate blocks broker test work'],
    },
    {
      model_id: 'MRM-DATA-001',
      name: 'Point-in-time feature generation',
      model_type: 'DATA_MODEL',
      linked_component: 'feature-store',
      owner: 'Research Platform',
      risk_tier: 'HIGH',
      review_status: 'VALIDATION_REQUIRED',
      intended_use: 'Research ranking, validation, portfolio simulation, and risk review only',
      prohibited_use: 'Live trading authorization, broker routing, credential handling, or production order decisions',
      validation_scores: {conceptual_soundness: 0.7, implementation_verification: 0.77, outcomes_analysis: 0.61, ongoing_monitoring: 0.56},
      residual_risk: 0.39,
      evidence: {experiment_id: null, config_hash: 'fallback-data', data_snapshot_id: 'md-snapshot-fallback', stress_run_id: null, backtest_run_id: null},
      limitations: ['Research feature catalog is snapshot-based and not a persisted governed store'],
      findings: ['Point-in-time and data lineage require deeper sample audit'],
      compensating_controls: ['Read-only console', 'Immutable reproducibility hashes'],
    },
  ],
  validation_dimensions: [
    {dimension: 'Conceptual soundness', dimension_zh: '概念合理性', score: 0.71, status: 'NEEDS_REVIEW', evidence: 'Alpha formulas, feature lineage, cost assumptions'},
    {dimension: 'Implementation verification', dimension_zh: '实现核验', score: 0.78, status: 'PARTIAL', evidence: 'API contract tests and dry-run gates'},
    {dimension: 'Outcomes analysis', dimension_zh: '结果分析', score: 0.62, status: 'NEEDS_REVIEW', evidence: 'Backtest explainability and stress library'},
    {dimension: 'Ongoing monitoring', dimension_zh: '持续监控', score: 0.56, status: 'MISSING_PERSISTENCE', evidence: 'Persistent drift monitoring is not implemented'},
  ],
  issue_register: [
    {issue_id: 'MRM-I01', severity: 'HIGH', status: 'OPEN', owner: 'Risk', description: 'Independent model validation workflow is not persisted', linked_models: ['MRM-RISK-001']},
    {issue_id: 'MRM-I02', severity: 'HIGH', status: 'OPEN', owner: 'Research Platform', description: 'Feature store is read-only snapshot', linked_models: ['MRM-DATA-001']},
  ],
  evidence_sources: [
    {source_id: 'EXP_REGISTRY', name: 'Experiment Registry', records: 3, read_only: true},
    {source_id: 'SCENARIO_STRESS', name: 'Scenario Stress Library', records: 3, read_only: true},
  ],
  governance_policy: {
    requires_independent_validation: true,
    requires_intended_use_statement: true,
    requires_limitations_disclosure: true,
    requires_issue_tracking: true,
    requires_ongoing_monitoring: true,
    mutable_reviews: false,
    authorizes_live_trading: false,
  },
};

const tierTone: Record<string, Tone> = {
  CRITICAL: 'neg', HIGH: 'warn', MEDIUM: 'info', LOW: 'muted',
};

function avgValidation(m: ModelRiskModel): number {
  const v = m.validation_scores;
  return (v.conceptual_soundness + v.implementation_verification + v.outcomes_analysis + v.ongoing_monitoring) / 4;
}

const dimZh: Record<keyof ModelRiskModel['validation_scores'], string> = {
  conceptual_soundness: bi('概念', 'Concept'),
  implementation_verification: bi('实现', 'Impl'),
  outcomes_analysis: bi('结果', 'Outcome'),
  ongoing_monitoring: bi('监控', 'Monitor'),
};

function scoreTone(s: number): Tone {
  return s >= 0.75 ? 'pos' : s >= 0.6 ? 'accent' : s >= 0.45 ? 'warn' : 'neg';
}

export default function ModelRiskReview() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<ModelRiskReviewPayload>(
    s => fetchModelRiskReviewSnapshot(s));

  const review = usingBackend ? payload! : fallbackReview;
  const { summary, governance_policy: gov } = review;

  const worstModels = useMemo(
    () => [...review.models].sort((a, b) => b.residual_risk - a.residual_risk).slice(0, 3),
    [review.models],
  );

  const issueBins = useMemo(() => {
    const bySeverity = new Map<string, number>();
    for (const i of review.issue_register) bySeverity.set(i.severity, (bySeverity.get(i.severity) ?? 0) + 1);
    const order = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
    return [...bySeverity.entries()]
      .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
      .map(([label, count]) => ({ label, count }));
  }, [review.issue_register]);

  const dimItems = review.validation_dimensions.map(d => ({ label: d.dimension, value: d.score }));

  const governancePolicies: Array<{ label: React.ReactNode; ok: boolean }> = [
    { label: bi('独立验证要求', 'Independent validation'), ok: gov.requires_independent_validation },
    { label: bi('用途声明要求', 'Intended-use statement'), ok: gov.requires_intended_use_statement },
    { label: bi('局限披露要求', 'Limitations disclosure'), ok: gov.requires_limitations_disclosure },
    { label: bi('问题追踪要求', 'Issue tracking'), ok: gov.requires_issue_tracking },
    { label: bi('持续监控要求', 'Ongoing monitoring'), ok: gov.requires_ongoing_monitoring },
    { label: bi('评审不可篡改', 'Immutable reviews'), ok: !gov.mutable_reviews },
    { label: bi('不授权实盘', 'No live authorization'), ok: !gov.authorizes_live_trading },
  ];

  const modelCols: Column<ModelRiskModel>[] = [
    {
      key: 'model', header: bi('模型', 'Model'), render: m => (
        <div className="min-w-0">
          <div className="truncate font-medium text-ink">{m.name}</div>
          <div className="mono text-2xs text-faint">{m.model_id} · {m.linked_component}</div>
        </div>
      ),
    },
    { key: 'type', header: bi('类型', 'Type'), render: m => <Chip label={m.model_type} tone="info" /> },
    { key: 'owner', header: bi('负责人', 'Owner'), render: m => <span className="text-muted">{m.owner}</span> },
    { key: 'tier', header: bi('层级', 'Tier'), render: m => <Chip label={m.risk_tier} tone={tierTone[m.risk_tier] ?? 'muted'} /> },
    { key: 'status', header: bi('评审状态', 'Review'), render: m => <StatusChip value={m.review_status} /> },
    {
      key: 'concept', header: dimZh.conceptual_soundness, align: 'right', mono: true,
      render: m => <span style={{ color: `var(--${scoreTone(m.validation_scores.conceptual_soundness)})` }}>{pct(m.validation_scores.conceptual_soundness, 0)}</span>,
    },
    {
      key: 'impl', header: dimZh.implementation_verification, align: 'right', mono: true,
      render: m => <span style={{ color: `var(--${scoreTone(m.validation_scores.implementation_verification)})` }}>{pct(m.validation_scores.implementation_verification, 0)}</span>,
    },
    {
      key: 'outcome', header: dimZh.outcomes_analysis, align: 'right', mono: true,
      render: m => <span style={{ color: `var(--${scoreTone(m.validation_scores.outcomes_analysis)})` }}>{pct(m.validation_scores.outcomes_analysis, 0)}</span>,
    },
    {
      key: 'monitor', header: dimZh.ongoing_monitoring, align: 'right', mono: true,
      render: m => <span style={{ color: `var(--${scoreTone(m.validation_scores.ongoing_monitoring)})` }}>{pct(m.validation_scores.ongoing_monitoring, 0)}</span>,
    },
    {
      key: 'residual', header: bi('残余风险', 'Residual'), width: '150px', render: m => (
        <div className="flex items-center gap-2">
          <ProgressBar value={m.residual_risk} tone={m.residual_risk >= 0.4 ? 'neg' : m.residual_risk >= 0.3 ? 'warn' : 'pos'} />
          <span className="tnum w-10 text-right text-muted">{pct(m.residual_risk, 0)}</span>
        </div>
      ),
    },
    {
      key: 'evidence', header: bi('证据哈希', 'Evidence'), mono: true,
      render: m => <span className="text-faint">{shortHash(m.evidence.config_hash)}</span>,
    },
  ];

  const issueCols: Column<ModelRiskReviewPayload['issue_register'][number]>[] = [
    { key: 'id', header: bi('编号', 'ID'), mono: true, render: i => <span className="text-ink">{i.issue_id}</span> },
    { key: 'sev', header: bi('严重度', 'Severity'), render: i => <Chip label={i.severity} tone={statusTone(i.severity)} /> },
    { key: 'status', header: bi('状态', 'Status'), render: i => <StatusChip value={i.status} /> },
    { key: 'owner', header: bi('负责人', 'Owner'), render: i => <span className="text-muted">{i.owner}</span> },
    { key: 'desc', header: bi('描述', 'Description'), render: i => <span className="text-ink">{i.description}</span> },
    { key: 'linked', header: bi('关联模型', 'Linked'), mono: true, render: i => <span className="text-faint">{i.linked_models.join(', ')}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Model Risk Review" titleZh="模型风险评审"
        subtitle={bi('原则导向的模型风险管理只读快照:验证四维、残余风险、问题台账与治理门控',
          'Read-only principles-based model-risk snapshot: four validation dimensions, residual risk, issue register, and governance gates')}
        actions={
          <div className="flex items-center gap-2">
            <Chip label={review.model_risk_mode} tone="info" />
            <StatusChip value={summary.live_use_authorized ? 'APPROVED' : 'NO_GO'} />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={error} />

      <KpiStrip cols={7}>
        <StatTile label={bi('模型', 'Models')} value={num(summary.models)} />
        <StatTile label={bi('高风险模型', 'High Risk')} value={num(summary.high_risk_models)} tone="warn" />
        <StatTile label={bi('开放问题', 'Open Issues')} value={num(summary.open_issues)} tone="warn" />
        <StatTile label={bi('高危开放', 'High Open')} value={num(summary.open_high_issues)} tone="neg" />
        <StatTile label={bi('平均残余风险', 'Avg Residual')} value={pct(summary.avg_residual_risk, 0)}
          tone={summary.avg_residual_risk >= 0.4 ? 'neg' : 'warn'} />
        <StatTile label={bi('独立验证', 'Indep. Valid.')} value={summary.independent_validation_complete ? bi('完成', 'DONE') : bi('待办', 'OPEN')}
          tone={summary.independent_validation_complete ? 'pos' : 'warn'} />
        <StatTile label={bi('实盘授权', 'Live Auth')} value={summary.live_use_authorized ? bi('是', 'YES') : bi('否', 'NO')}
          tone={summary.live_use_authorized ? 'neg' : 'pos'}
          sub={<span className="mono">{review.review_standard}</span>} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('验证四维', 'Validation')} title={bi('验证维度评分', 'Validation Dimension Scores')}>
          <DivergingBars items={dimItems} height={150} valueFormat={v => pct(v, 0)} />
          <div className="mt-3 flex flex-col gap-2">
            {review.validation_dimensions.map(d => (
              <div key={d.dimension} className="flex items-center justify-between gap-3 rounded-lg border border-line/60 bg-inset/30 px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium text-ink">{d.dimension_zh} <span className="text-faint">/ {d.dimension}</span></div>
                  <div className="truncate text-2xs text-faint">{d.evidence}</div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="tnum text-xs text-muted">{pct(d.score, 0)}</span>
                  <StatusChip value={d.status} />
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel eyebrow={bi('治理门控', 'Governance')} title={bi('治理策略门', 'Governance Policy Gates')}
          right={<RadialGauge value={summary.avg_residual_risk} max={1} size={56}
            tone={summary.avg_residual_risk >= 0.4 ? 'neg' : 'warn'} label={bi('残余', 'RES')} />}>
          <div className="flex flex-col gap-2">
            {governancePolicies.map((p, i) => (
              <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-line/60 bg-inset/30 px-3 py-2">
                <span className="text-xs text-ink">{p.label}</span>
                <div className="flex items-center gap-2">
                  <span className="text-2xs text-faint">{p.ok ? bi('满足', 'PASS') : bi('未满足', 'FAIL')}</span>
                  <GateDot ok={p.ok} />
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {worstModels.map(m => (
          <Panel key={m.model_id} tone={tierTone[m.risk_tier]} eyebrow={<span className="mono">{m.model_id}</span>}
            title={m.name}
            right={<Chip label={m.risk_tier} tone={tierTone[m.risk_tier] ?? 'muted'} />}>
            <div className="flex items-center gap-4">
              <RadialGauge value={avgValidation(m)} max={1} size={104} tone={scoreTone(avgValidation(m))} label={bi('验证', 'VALID')} />
              <div className="flex min-w-0 flex-1 flex-col gap-2">
                {(Object.keys(m.validation_scores) as Array<keyof ModelRiskModel['validation_scores']>).map(k => (
                  <div key={k} className="flex flex-col gap-1">
                    <div className="flex items-center justify-between text-2xs">
                      <span className="text-faint">{dimZh[k]}</span>
                      <span className="tnum text-muted">{pct(m.validation_scores[k], 0)}</span>
                    </div>
                    <ProgressBar value={m.validation_scores[k]} tone={scoreTone(m.validation_scores[k])} height={4} />
                  </div>
                ))}
              </div>
            </div>
            <div className="mt-3 flex items-center justify-between gap-2 border-t border-line/60 pt-2">
              <StatusChip value={m.review_status} />
              <div className="flex items-center gap-2">
                <span className="text-2xs text-faint">{bi('残余', 'Residual')}</span>
                <span className="tnum text-xs" style={{ color: `var(--${m.residual_risk >= 0.4 ? 'neg' : 'warn'})` }}>{pct(m.residual_risk, 0)}</span>
              </div>
            </div>
            {m.findings.length > 0 && (
              <div className="mt-2 text-2xs text-muted">⚠ {m.findings[0]}</div>
            )}
          </Panel>
        ))}
      </div>

      <Panel eyebrow={bi('模型清册', 'Inventory')} title={bi('模型风险登记表', 'Model Risk Inventory')}
        right={<Chip label={bi(`${review.models.length} 个模型`, `${review.models.length} models`)} tone="info" />}>
        <DataTable columns={modelCols} rows={review.models} getKey={m => m.model_id} dense />
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('风险问题', 'Issues')} title={bi('问题台账', 'Issue Register')}
          right={<Chip label={bi(`${summary.open_high_issues} 高危`, `${summary.open_high_issues} high`)} tone="neg" />}>
          <DataTable columns={issueCols} rows={review.issue_register} getKey={i => i.issue_id}
            empty={bi('无开放问题', 'No open issues')} />
          {issueBins.length > 0 && (
            <div className="mt-3 border-t border-line/60 pt-3">
              <Histogram bins={issueBins} height={120} tone="warn" />
            </div>
          )}
        </Panel>

        <Panel eyebrow={bi('证据来源', 'Evidence')} title={bi('只读证据源', 'Read-Only Evidence Sources')}>
          <div className="flex flex-col gap-2">
            {review.evidence_sources.map(s => (
              <div key={s.source_id} className="flex items-center justify-between gap-2 rounded-lg border border-line/60 bg-inset/30 px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium text-ink">{s.name}</div>
                  <div className="mono text-2xs text-faint">{s.source_id}</div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="tnum text-xs text-muted">{num(s.records)}</span>
                  <GateDot ok={s.read_only} label={bi('只读', 'RO')} />
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 border-t border-line/60 pt-3">
            <KeyValue cols={1} items={[
              { k: bi('评审标准', 'Review standard'), v: review.review_standard },
              { k: bi('评审模式', 'Mode'), v: review.model_risk_mode },
              { k: bi('独立验证完成', 'Indep. validation'), v: summary.independent_validation_complete ? bi('是', 'true') : bi('否', 'false') },
            ]} />
          </div>
        </Panel>
      </div>
    </PageShell>
  );
}
