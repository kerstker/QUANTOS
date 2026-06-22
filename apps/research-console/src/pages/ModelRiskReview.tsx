import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchModelRiskReviewSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ModelRiskModel, ModelRiskReviewPayload } from '@/lib/types/research';

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

function pct(value: number) {
  return `${Math.round(value * 100)}%`;
}

function tierClass(tier: string) {
  if (tier === 'CRITICAL') {
    return 'border-red-800 bg-red-950/30 text-red-300';
  }
  if (tier === 'HIGH') {
    return 'border-orange-800 bg-orange-950/30 text-orange-300';
  }
  return 'border-slate-700 bg-slate-950/60 text-slate-300';
}

function statusClass(status: string) {
  if (status === 'OPEN' || status.includes('REQUIRED') || status.includes('MISSING')) {
    return 'text-red-300';
  }
  if (status === 'PARTIAL' || status.includes('MITIGATED')) {
    return 'text-amber-300';
  }
  return 'text-emerald-300';
}

function ModelCard({model}: {model: ModelRiskModel}) {
  const avgValidation = (
    model.validation_scores.conceptual_soundness +
    model.validation_scores.implementation_verification +
    model.validation_scores.outcomes_analysis +
    model.validation_scores.ongoing_monitoring
  ) / 4;
  return (
    <div className={`rounded-lg border p-3 ${tierClass(model.risk_tier)}`}>
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[10px] font-bold uppercase tracking-wider text-cyan-300">{model.model_id}</div>
          <div className="mt-1 break-words text-sm font-bold text-slate-100">{model.name}</div>
        </div>
        <span className="rounded bg-slate-950/60 px-2 py-0.5 text-[10px]">{model.risk_tier}</span>
      </div>
      <div className="grid gap-2 text-xs">
        <div className="text-slate-400">{bi('类型', 'Type')}: {model.model_type}</div>
        <div className="text-slate-400">{bi('负责人', 'Owner')}: {model.owner}</div>
        <div className={statusClass(model.review_status)}>{bi('状态', 'Status')}: {model.review_status}</div>
        <div className="h-2 overflow-hidden rounded bg-slate-800">
          <div className="h-full bg-cyan-700" style={{width: pct(avgValidation)}} />
        </div>
        <div className="text-slate-500">{bi('残余风险', 'Residual risk')}: {pct(model.residual_risk)}</div>
        <div className="break-words text-slate-500">hash: {model.evidence.config_hash}</div>
      </div>
    </div>
  );
}

function ValidationBoard({payload}: {payload: ModelRiskReviewPayload}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('验证维度', 'Validation Dimensions')}</h2>
      <div className="space-y-3 text-xs">
        {payload.validation_dimensions.map(item => (
          <div key={item.dimension} className="rounded border border-slate-800 bg-slate-950/60 p-3">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div className="font-bold text-slate-200">{item.dimension_zh} / {item.dimension}</div>
              <span className={statusClass(item.status)}>{item.status}</span>
            </div>
            <div className="mb-2 h-2 overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-cyan-700" style={{width: pct(item.score)}} />
            </div>
            <div className="text-slate-500">{item.evidence}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function GovernancePolicy({payload}: {payload: ModelRiskReviewPayload}) {
  const policies: Array<[string, boolean]> = [
    ['INDEPENDENT_VALIDATION', payload.governance_policy.requires_independent_validation],
    ['INTENDED_USE_STATEMENT', payload.governance_policy.requires_intended_use_statement],
    ['LIMITATIONS_DISCLOSURE', payload.governance_policy.requires_limitations_disclosure],
    ['ISSUE_TRACKING', payload.governance_policy.requires_issue_tracking],
    ['ONGOING_MONITORING', payload.governance_policy.requires_ongoing_monitoring],
    ['IMMUTABLE_REVIEWS', !payload.governance_policy.mutable_reviews],
    ['NO_LIVE_AUTHORIZATION', !payload.governance_policy.authorizes_live_trading],
  ];
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('治理策略', 'Governance Policy')}</h2>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        {policies.map(([label, passed]) => (
          <div key={label} className="flex items-center justify-between gap-2 rounded bg-slate-800 px-3 py-2">
            <span className="text-slate-300">{label}</span>
            <span className={passed ? 'text-emerald-300' : 'text-red-300'}>{passed ? 'PASS' : 'FAIL'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ModelRiskReview() {
  const [payload, setPayload] = useState<ModelRiskReviewPayload | null>(null);
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
    fetchModelRiskReviewSnapshot(controller.signal)
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
  const review = usingBackend && payload ? payload : fallbackReview;
  const worstModels = useMemo(
    () => [...review.models].sort((left, right) => right.residual_risk - left.residual_risk).slice(0, 4),
    [review.models],
  );

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('模型风险评审', 'Model Risk Review')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision="L2_DRY_RUN_ONLY" />
              <DecisionBadge decision={review.model_risk_mode} />
              <DecisionBadge decision={review.summary.live_use_authorized ? 'APPROVED' : 'NO_GO'} />
            </div>
          </div>
          <div className="text-xs text-slate-500 lg:text-right">
            <div>{bi('标准', 'Standard')}: {review.review_standard}</div>
            <div>{bi('独立验证完成', 'Independent validation complete')}: {String(review.summary.independent_validation_complete)}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={review.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('模型', 'Models')} value={review.summary.models} />
        <MetricCard label={bi('高风险', 'High Risk')} value={review.summary.high_risk_models} />
        <MetricCard label={bi('开放问题', 'Open Issues')} value={review.summary.open_issues} />
        <MetricCard label={bi('高危问题', 'High Issues')} value={review.summary.open_high_issues} />
        <MetricCard label={bi('残余风险', 'Residual')} value={pct(review.summary.avg_residual_risk)} />
        <MetricCard label={bi('证据源', 'Evidence')} value={review.evidence_sources.length} />
        <MetricCard label={bi('独立验证', 'Validation')} value={review.summary.independent_validation_complete ? 'DONE' : 'OPEN'} />
        <MetricCard label={bi('实盘授权', 'Live Auth')} value={review.summary.live_use_authorized ? 'YES' : 'NO'} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_0.9fr]">
        <ValidationBoard payload={review} />
        <GovernancePolicy payload={review} />
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('残余风险最高模型', 'Highest Residual Risk Models')}</h2>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {worstModels.map(model => <ModelCard key={model.model_id} model={model} />)}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
          <table className="w-full min-w-[1080px] text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase">
                <th className="p-2 text-left">{bi('模型', 'Model')}</th>
                <th className="p-2 text-left">{bi('类型', 'Type')}</th>
                <th className="p-2 text-left">{bi('层级', 'Tier')}</th>
                <th className="p-2 text-left">{bi('状态', 'Status')}</th>
                <th className="p-2 text-right">{bi('概念', 'Concept')}</th>
                <th className="p-2 text-right">{bi('实现', 'Impl')}</th>
                <th className="p-2 text-right">{bi('结果', 'Outcome')}</th>
                <th className="p-2 text-right">{bi('监控', 'Monitor')}</th>
                <th className="p-2 text-right">{bi('残余风险', 'Residual')}</th>
              </tr>
            </thead>
            <tbody>
              {review.models.map(model => (
                <tr key={model.model_id} className="border-b border-slate-800/50">
                  <td className="p-2">
                    <div className="font-bold text-slate-200">{model.name}</div>
                    <div className="text-slate-500">{model.model_id}</div>
                  </td>
                  <td className="p-2 text-cyan-300">{model.model_type}</td>
                  <td className="p-2 text-orange-300">{model.risk_tier}</td>
                  <td className={`p-2 ${statusClass(model.review_status)}`}>{model.review_status}</td>
                  <td className="p-2 text-right text-slate-300">{pct(model.validation_scores.conceptual_soundness)}</td>
                  <td className="p-2 text-right text-slate-300">{pct(model.validation_scores.implementation_verification)}</td>
                  <td className="p-2 text-right text-slate-300">{pct(model.validation_scores.outcomes_analysis)}</td>
                  <td className="p-2 text-right text-slate-300">{pct(model.validation_scores.ongoing_monitoring)}</td>
                  <td className="p-2 text-right text-red-300">{pct(model.residual_risk)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('问题台账', 'Issue Register')}</h2>
            <div className="space-y-3 text-xs">
              {review.issue_register.map(issue => (
                <div key={issue.issue_id} className="rounded border border-slate-800 bg-slate-950/60 p-3">
                  <div className="mb-1 flex justify-between gap-2">
                    <span className="font-bold text-slate-200">{issue.issue_id}</span>
                    <span className={statusClass(issue.status)}>{issue.severity} / {issue.status}</span>
                  </div>
                  <div className="text-slate-300">{issue.description}</div>
                  <div className="mt-2 text-slate-500">{bi('负责人', 'Owner')}: {issue.owner}</div>
                  <div className="mt-1 break-words text-slate-500">{issue.linked_models.join(', ')}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('证据来源', 'Evidence Sources')}</h2>
            <div className="space-y-2 text-xs">
              {review.evidence_sources.map(source => (
                <div key={source.source_id} className="flex items-center justify-between gap-2 rounded bg-slate-800 px-3 py-2">
                  <span className="text-slate-300">{source.name}</span>
                  <span className="text-cyan-300">{source.records}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
