import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchDataCatalogSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { DataCatalogDataset, DataCatalogFeature, DataCatalogPayload } from '@/lib/types/research';

const fallbackCatalog: DataCatalogPayload = {
  source: 'FRONTEND_DATA_CATALOG_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  catalog_mode: 'READ_ONLY_DATA_CATALOG',
  feature_store_mode: 'OFFLINE_POINT_IN_TIME_RESEARCH',
  summary: {
    datasets: 2,
    features: 3,
    quality_checks: 6,
    quality_passed: 6,
    point_in_time_safe_features: 3,
    lineage_edges: 6,
    avg_dataset_quality: 0.96,
    avg_feature_quality: 0.95,
  },
  datasets: [
    {
      dataset_id: 'ds-market-daily-bars',
      name: 'CN A-share daily bars',
      domain: 'MARKET_DATA',
      version: 'fallback',
      snapshot_id: 'md-snapshot-fallback',
      row_count: 18_420_000,
      coverage: 0.99,
      freshness_hours: 3.2,
      quality_score: 0.98,
      point_in_time_safe: true,
      owner: 'Market Data',
      storage_uri: 'artifact://fallback/market/daily-bars',
      content_hash: 'fallback-market-hash',
      checks: [],
    },
    {
      dataset_id: 'ds-liquidity-profile',
      name: 'Liquidity and capacity profile',
      domain: 'LIQUIDITY_DATA',
      version: 'fallback',
      snapshot_id: 'liq-snapshot-fallback',
      row_count: 5_520_000,
      coverage: 0.97,
      freshness_hours: 4.1,
      quality_score: 0.94,
      point_in_time_safe: true,
      owner: 'Trading Research',
      storage_uri: 'artifact://fallback/liquidity/profile',
      content_hash: 'fallback-liq-hash',
      checks: [],
    },
  ],
  features: [
    {
      feature_id: 'feat-ts-mean-close-5',
      name: 'ts_mean_close_5',
      family: 'PRICE_MOMENTUM',
      expression: '(ts_mean :close 5)',
      source_dataset_id: 'ds-market-daily-bars',
      version: 'v1',
      owner: 'Alpha Research',
      lookback_days: 5,
      point_in_time_policy: {event_timestamp_column: 'event_ts', as_of_join: true, max_source_lag_days: 1, leakage_guard: 'STRICT_EVENT_TIME_UPPER_BOUND'},
      quality_score: 0.98,
      lineage_hash: 'fallback-feature-001',
      used_by_experiments: ['exp-alpha-1-v001'],
    },
    {
      feature_id: 'feat-turnover-std-60',
      name: 'ts_std_turnover_60',
      family: 'LIQUIDITY',
      expression: '(ts_std :turnover 60)',
      source_dataset_id: 'ds-liquidity-profile',
      version: 'v2',
      owner: 'Alpha Research',
      lookback_days: 60,
      point_in_time_policy: {event_timestamp_column: 'event_ts', as_of_join: true, max_source_lag_days: 2, leakage_guard: 'STRICT_EVENT_TIME_UPPER_BOUND'},
      quality_score: 0.94,
      lineage_hash: 'fallback-feature-002',
      used_by_experiments: ['exp-alpha-5-v002'],
    },
    {
      feature_id: 'feat-rank-decay-amount-90',
      name: 'rank_decay_amount_90',
      family: 'CAPACITY',
      expression: '(rank_decay :amount 90)',
      source_dataset_id: 'ds-liquidity-profile',
      version: 'v3',
      owner: 'Alpha Research',
      lookback_days: 90,
      point_in_time_policy: {event_timestamp_column: 'event_ts', as_of_join: true, max_source_lag_days: 2, leakage_guard: 'STRICT_EVENT_TIME_UPPER_BOUND'},
      quality_score: 0.93,
      lineage_hash: 'fallback-feature-003',
      used_by_experiments: ['exp-alpha-6-v003'],
    },
  ],
  lineage_edges: [
    {from: 'ds-market-daily-bars', to: 'feat-ts-mean-close-5', kind: 'DATASET_TO_FEATURE'},
    {from: 'feat-ts-mean-close-5', to: 'exp-alpha-1-v001', kind: 'FEATURE_TO_EXPERIMENT'},
    {from: 'ds-liquidity-profile', to: 'feat-turnover-std-60', kind: 'DATASET_TO_FEATURE'},
    {from: 'feat-turnover-std-60', to: 'exp-alpha-5-v002', kind: 'FEATURE_TO_EXPERIMENT'},
  ],
  quality_policy: {
    requires_dataset_hash: true,
    requires_event_timestamp: true,
    requires_asof_join: true,
    blocks_future_leakage: true,
    mutable_raw_inputs: false,
  },
};

function percent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function qualityClass(value: number) {
  if (value >= 0.97) {
    return 'text-emerald-300';
  }
  if (value >= 0.94) {
    return 'text-cyan-300';
  }
  return 'text-amber-300';
}

function DatasetHealth({datasets}: {datasets: DataCatalogDataset[]}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('数据集健康度', 'Dataset Health')}</h2>
      <div className="space-y-3 text-xs">
        {datasets.map(dataset => (
          <div key={dataset.dataset_id} className="rounded border border-slate-800 bg-slate-950/60 p-3">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="break-words font-bold text-slate-200">{dataset.name}</div>
                <div className="mt-1 break-words text-slate-500">{dataset.snapshot_id}</div>
              </div>
              <span className={`font-bold ${qualityClass(dataset.quality_score)}`}>{percent(dataset.quality_score)}</span>
            </div>
            <div className="grid gap-2 md:grid-cols-3">
              <div className="rounded bg-slate-800/70 px-2 py-1">{bi('覆盖', 'Coverage')}: {percent(dataset.coverage)}</div>
              <div className="rounded bg-slate-800/70 px-2 py-1">{bi('新鲜度', 'Freshness')}: {dataset.freshness_hours}h</div>
              <div className="rounded bg-slate-800/70 px-2 py-1">{bi('检查', 'Checks')}: {dataset.checks.filter(check => check.status === 'PASS').length}/{dataset.checks.length}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function FeatureFamilies({features}: {features: DataCatalogFeature[]}) {
  const families = features.reduce<Record<string, number>>((acc, feature) => {
    acc[feature.family] = (acc[feature.family] ?? 0) + 1;
    return acc;
  }, {});
  const max = Math.max(...Object.values(families), 1);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('特征族覆盖', 'Feature Families')}</h2>
      <div className="space-y-3 text-xs">
        {Object.entries(families).map(([family, count]) => (
          <div key={family} className="grid grid-cols-[8rem_1fr_2rem] items-center gap-2">
            <span className="truncate text-slate-400">{family}</span>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-cyan-700" style={{width: `${Math.max(6, count / max * 100)}%`}} />
            </div>
            <span className="text-right font-bold text-slate-200">{count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PolicyBoard({catalog}: {catalog: DataCatalogPayload}) {
  const policies: Array<[string, boolean]> = [
    ['DATASET_HASH', catalog.quality_policy.requires_dataset_hash],
    ['EVENT_TIMESTAMP', catalog.quality_policy.requires_event_timestamp],
    ['ASOF_JOIN', catalog.quality_policy.requires_asof_join],
    ['FUTURE_LEAKAGE_BLOCK', catalog.quality_policy.blocks_future_leakage],
    ['IMMUTABLE_RAW_INPUTS', !catalog.quality_policy.mutable_raw_inputs],
  ];
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm uppercase text-slate-400">{bi('Point-in-time 策略', 'Point-in-time Policy')}</h2>
          <div className="mt-1 text-xs text-slate-500">{catalog.feature_store_mode}</div>
        </div>
        <DecisionBadge decision="READ_ONLY" />
      </div>
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

function LineagePreview({catalog}: {catalog: DataCatalogPayload}) {
  const sampleFeatures = catalog.features.slice(0, 5);
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('血缘链路', 'Lineage Preview')}</h2>
      <div className="space-y-2 text-xs">
        {sampleFeatures.map(feature => (
          <div key={feature.feature_id} className="rounded border border-slate-800 bg-slate-950/60 p-3">
            <div className="break-words text-slate-300">
              <span className="text-cyan-300">{feature.source_dataset_id}</span>
              <span className="mx-2 text-slate-600">-&gt;</span>
              <span className="text-emerald-300">{feature.feature_id}</span>
              <span className="mx-2 text-slate-600">-&gt;</span>
              <span className="text-amber-300">{feature.used_by_experiments.join(', ')}</span>
            </div>
            <div className="mt-2 break-words text-slate-500">{feature.lineage_hash}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function DataCatalog() {
  const [payload, setPayload] = useState<DataCatalogPayload | null>(null);
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
    fetchDataCatalogSnapshot(controller.signal)
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
  const catalog = usingBackend && payload ? payload : fallbackCatalog;
  const highestUseFeatures = useMemo(
    () => [...catalog.features].sort((left, right) => right.used_by_experiments.length - left.used_by_experiments.length).slice(0, 4),
    [catalog.features],
  );

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('数据目录 / 特征库', 'Data Catalog / Feature Store')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision="L2_DRY_RUN_ONLY" />
              <DecisionBadge decision={catalog.catalog_mode} />
            </div>
          </div>
          <div className="text-xs text-slate-500 md:text-right">
            <div>{bi('特征模式', 'Feature mode')}: {catalog.feature_store_mode}</div>
            <div>{bi('血缘边', 'Lineage edges')}: {catalog.summary.lineage_edges}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={catalog.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('数据集', 'Datasets')} value={catalog.summary.datasets} />
        <MetricCard label={bi('特征', 'Features')} value={catalog.summary.features} />
        <MetricCard label={bi('检查', 'Checks')} value={`${catalog.summary.quality_passed}/${catalog.summary.quality_checks}`} />
        <MetricCard label={bi('PIT 安全', 'PIT Safe')} value={catalog.summary.point_in_time_safe_features} />
        <MetricCard label={bi('数据质量', 'Dataset Q')} value={Math.round(catalog.summary.avg_dataset_quality * 100)} unit="%" />
        <MetricCard label={bi('特征质量', 'Feature Q')} value={Math.round(catalog.summary.avg_feature_quality * 100)} unit="%" />
        <MetricCard label={bi('血缘', 'Lineage')} value={catalog.summary.lineage_edges} />
        <MetricCard label={bi('只读', 'Read-only')} value={catalog.read_only ? 'YES' : 'NO'} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <DatasetHealth datasets={catalog.datasets} />
        <div className="space-y-4">
          <FeatureFamilies features={catalog.features} />
          <PolicyBoard catalog={catalog} />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <LineagePreview catalog={catalog} />
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('高复用特征', 'High-use Features')}</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {highestUseFeatures.map(feature => (
              <div key={feature.feature_id} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs">
                <div className="break-words font-bold text-slate-200">{feature.name}</div>
                <div className="mt-1 break-words text-cyan-300">{feature.expression}</div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-slate-400">
                  <div>{bi('族', 'Family')}: {feature.family}</div>
                  <div>{bi('窗口', 'Lookback')}: {feature.lookback_days}d</div>
                  <div>{bi('质量', 'Quality')}: {percent(feature.quality_score)}</div>
                  <div>{bi('实验', 'Experiments')}: {feature.used_by_experiments.length}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[980px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">{bi('数据集', 'Dataset')}</th>
              <th className="p-2 text-left">{bi('域', 'Domain')}</th>
              <th className="p-2 text-left">{bi('版本', 'Version')}</th>
              <th className="p-2 text-left">{bi('快照', 'Snapshot')}</th>
              <th className="p-2 text-right">{bi('行数', 'Rows')}</th>
              <th className="p-2 text-right">{bi('覆盖', 'Coverage')}</th>
              <th className="p-2 text-right">{bi('新鲜度', 'Freshness')}</th>
              <th className="p-2 text-left">Hash</th>
            </tr>
          </thead>
          <tbody>
            {catalog.datasets.map(dataset => (
              <tr key={dataset.dataset_id} className="border-b border-slate-800/50">
                <td className="p-2">
                  <div className="font-bold text-slate-200">{dataset.name}</div>
                  <div className="text-slate-500">{dataset.dataset_id}</div>
                </td>
                <td className="p-2 text-cyan-300">{dataset.domain}</td>
                <td className="p-2 text-slate-400">{dataset.version}</td>
                <td className="p-2 text-slate-400">{dataset.snapshot_id}</td>
                <td className="p-2 text-right text-slate-300">{dataset.row_count.toLocaleString()}</td>
                <td className="p-2 text-right text-slate-300">{percent(dataset.coverage)}</td>
                <td className="p-2 text-right text-slate-300">{dataset.freshness_hours}h</td>
                <td className="p-2 font-mono text-slate-500">{dataset.content_hash}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[1080px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">{bi('特征', 'Feature')}</th>
              <th className="p-2 text-left">{bi('族', 'Family')}</th>
              <th className="p-2 text-left">{bi('表达式', 'Expression')}</th>
              <th className="p-2 text-left">{bi('源数据', 'Source')}</th>
              <th className="p-2 text-right">{bi('窗口', 'Lookback')}</th>
              <th className="p-2 text-right">{bi('质量', 'Quality')}</th>
              <th className="p-2 text-left">{bi('PIT 策略', 'PIT Policy')}</th>
              <th className="p-2 text-left">{bi('实验', 'Experiments')}</th>
            </tr>
          </thead>
          <tbody>
            {catalog.features.map(feature => (
              <tr key={feature.feature_id} className="border-b border-slate-800/50">
                <td className="p-2">
                  <div className="font-bold text-slate-200">{feature.name}</div>
                  <div className="text-slate-500">{feature.feature_id}</div>
                </td>
                <td className="p-2 text-cyan-300">{feature.family}</td>
                <td className="p-2 font-mono text-slate-300">{feature.expression}</td>
                <td className="p-2 text-slate-400">{feature.source_dataset_id}</td>
                <td className="p-2 text-right text-slate-300">{feature.lookback_days}d</td>
                <td className={`p-2 text-right font-bold ${qualityClass(feature.quality_score)}`}>{percent(feature.quality_score)}</td>
                <td className="p-2 text-slate-400">{feature.point_in_time_policy.leakage_guard}</td>
                <td className="p-2 text-slate-500">{feature.used_by_experiments.join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
