import React, { useMemo } from 'react';
import { fetchDataCatalogSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type {
  DataCatalogDataset, DataCatalogFeature, DataCatalogPayload,
} from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, Chip, KeyValue, type Column,
} from '@/ui/primitives';
import { Donut, Histogram } from '@/ui/charts';
import { num, pct, fixed, compact, shortHash, type Tone } from '@/ui/format';

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

const familyTones: Tone[] = ['accent', 'info', 'warn', 'special', 'pos', 'muted', 'neg'];

function qualityTone(v: number): Tone {
  if (v >= 0.97) return 'pos';
  if (v >= 0.94) return 'accent';
  if (v >= 0.9) return 'warn';
  return 'neg';
}

function checkStats(dataset: DataCatalogDataset): { passed: number; total: number } {
  const total = dataset.checks.length;
  const passed = dataset.checks.filter(c => c.status === 'PASS').length;
  return { passed, total };
}

export default function DataCatalog() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<DataCatalogPayload>(
    s => fetchDataCatalogSnapshot(s));

  const catalog = usingBackend ? payload! : fallbackCatalog;

  const familySegments = useMemo(() => {
    const counts = catalog.features.reduce<Record<string, number>>((acc, f) => {
      acc[f.family] = (acc[f.family] ?? 0) + 1;
      return acc;
    }, {});
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([family, value], i) => ({ label: family, value, tone: familyTones[i % familyTones.length] }));
  }, [catalog.features]);

  const lineageBins = useMemo(() => {
    const counts = catalog.lineage_edges.reduce<Record<string, number>>((acc, e) => {
      acc[e.kind] = (acc[e.kind] ?? 0) + 1;
      return acc;
    }, {});
    return Object.entries(counts).map(([kind, count]) => ({
      label: kind === 'DATASET_TO_FEATURE' ? bi('集→特', 'DS→FEAT')
        : kind === 'FEATURE_TO_EXPERIMENT' ? bi('特→实验', 'FEAT→EXP')
        : kind,
      count,
    }));
  }, [catalog.lineage_edges]);

  const policy = catalog.quality_policy;
  const policyRows: Array<{ label: React.ReactNode; ok: boolean }> = [
    { label: bi('要求数据集哈希', 'Requires dataset hash'), ok: policy.requires_dataset_hash },
    { label: bi('要求事件时间戳', 'Requires event timestamp'), ok: policy.requires_event_timestamp },
    { label: bi('要求 as-of join', 'Requires as-of join'), ok: policy.requires_asof_join },
    { label: bi('阻断未来泄漏', 'Blocks future leakage'), ok: policy.blocks_future_leakage },
    { label: bi('原始输入不可变', 'Immutable raw inputs'), ok: !policy.mutable_raw_inputs },
  ];
  const policyOk = policyRows.every(p => p.ok);

  const datasetCols: Column<DataCatalogDataset>[] = [
    {
      key: 'name', header: bi('数据集', 'Dataset'),
      render: d => (
        <div className="min-w-0">
          <div className="truncate font-medium text-ink">{d.name}</div>
          <div className="mono truncate text-2xs text-faint">{d.dataset_id}</div>
        </div>
      ),
    },
    { key: 'domain', header: bi('域', 'Domain'), render: d => <Chip label={d.domain} tone="info" /> },
    {
      key: 'version', header: bi('版本/快照', 'Version'),
      render: d => (
        <div className="min-w-0">
          <div className="text-ink">{d.version}</div>
          <div className="mono truncate text-2xs text-faint">{d.snapshot_id}</div>
        </div>
      ),
    },
    { key: 'rows', header: bi('行数', 'Rows'), align: 'right', render: d => compact(d.row_count) },
    { key: 'coverage', header: bi('覆盖', 'Coverage'), align: 'right', render: d => pct(d.coverage) },
    { key: 'fresh', header: bi('新鲜度', 'Freshness'), align: 'right', render: d => `${fixed(d.freshness_hours, 1)}h` },
    {
      key: 'quality', header: bi('质量分', 'Quality'), width: '150px',
      render: d => (
        <div className="flex items-center gap-2">
          <ProgressBar value={d.quality_score} tone={qualityTone(d.quality_score)} />
          <span className="tnum w-10 text-right text-muted">{pct(d.quality_score, 0)}</span>
        </div>
      ),
    },
    {
      key: 'checks', header: bi('检查', 'Checks'), align: 'right',
      render: d => {
        const { passed, total } = checkStats(d);
        return total ? <span className="tnum">{passed}/{total}</span> : <span className="text-faint">—</span>;
      },
    },
    { key: 'pit', header: bi('PIT 安全', 'PIT Safe'), align: 'center', render: d => <GateDot ok={d.point_in_time_safe} /> },
    { key: 'hash', header: 'Hash', mono: true, render: d => <span className="text-faint">{shortHash(d.content_hash, 10)}</span> },
  ];

  const featureCols: Column<DataCatalogFeature>[] = [
    {
      key: 'name', header: bi('特征', 'Feature'),
      render: f => (
        <div className="min-w-0">
          <div className="truncate font-medium text-ink">{f.name}</div>
          <div className="mono truncate text-2xs text-faint">{f.feature_id}</div>
        </div>
      ),
    },
    { key: 'family', header: bi('族', 'Family'), render: f => <Chip label={f.family} tone="accent" /> },
    { key: 'expr', header: bi('表达式', 'Expression'), mono: true, render: f => <span className="text-info">{f.expression}</span> },
    { key: 'src', header: bi('源数据', 'Source'), mono: true, render: f => <span className="text-muted">{f.source_dataset_id}</span> },
    { key: 'lookback', header: bi('窗口', 'Lookback'), align: 'right', render: f => `${num(f.lookback_days)}d` },
    {
      key: 'pit', header: bi('PIT 策略', 'PIT Policy'),
      render: f => (
        <div className="flex items-center gap-2">
          <GateDot ok={f.point_in_time_policy.as_of_join} />
          <span className="truncate text-2xs text-muted">{f.point_in_time_policy.leakage_guard}</span>
        </div>
      ),
    },
    {
      key: 'quality', header: bi('质量分', 'Quality'), width: '140px',
      render: f => (
        <div className="flex items-center gap-2">
          <ProgressBar value={f.quality_score} tone={qualityTone(f.quality_score)} />
          <span className="tnum w-10 text-right text-muted">{pct(f.quality_score, 0)}</span>
        </div>
      ),
    },
    {
      key: 'exp', header: bi('实验', 'Experiments'), align: 'right',
      render: f => <span className="tnum">{num(f.used_by_experiments.length)}</span>,
    },
  ];

  const qPassRate = catalog.summary.quality_checks > 0
    ? catalog.summary.quality_passed / catalog.summary.quality_checks : 0;

  return (
    <PageShell>
      <PageHeader title="Data Catalog / Feature Store" titleZh="数据目录 / 特征库"
        subtitle={bi('只读数据集与特征快照 · point-in-time 安全 · 血缘可追溯',
          'Read-only datasets & features snapshot · point-in-time safe · traceable lineage')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Chip label={catalog.catalog_mode} tone="info" />
            <Chip label={catalog.feature_store_mode} tone="special" />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={catalog.source} error={error} />

      <KpiStrip cols={8}>
        <StatTile label={bi('数据集', 'Datasets')} value={num(catalog.summary.datasets)} accent />
        <StatTile label={bi('特征', 'Features')} value={num(catalog.summary.features)} />
        <StatTile label={bi('质量检查', 'Quality Checks')} value={`${num(catalog.summary.quality_passed)}/${num(catalog.summary.quality_checks)}`}
          tone={qPassRate >= 1 ? 'pos' : 'warn'} sub={pct(qPassRate, 0)} />
        <StatTile label={bi('PIT 安全特征', 'PIT-Safe Feat.')} value={num(catalog.summary.point_in_time_safe_features)} tone="pos" />
        <StatTile label={bi('数据集质量', 'Dataset Q')} value={pct(catalog.summary.avg_dataset_quality)} tone="info" />
        <StatTile label={bi('特征质量', 'Feature Q')} value={pct(catalog.summary.avg_feature_quality)} tone="info" />
        <StatTile label={bi('血缘边', 'Lineage Edges')} value={num(catalog.summary.lineage_edges)} />
        <StatTile label={bi('只读', 'Read-only')} value={catalog.read_only ? bi('是', 'YES') : bi('否', 'NO')} tone="pos" />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('构成', 'Composition')} title={bi('特征族分布', 'Feature Families')}
          right={<Chip label={bi(`${familySegments.length} 族`, `${familySegments.length} fam.`)} tone="muted" />}>
          <div className="flex items-center gap-4">
            <Donut size={140} segments={familySegments} />
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              {familySegments.map(s => (
                <div key={s.label} className="flex items-center justify-between gap-2 text-xs">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: `var(--${s.tone})` }} />
                    <span className="truncate text-muted">{s.label}</span>
                  </span>
                  <span className="tnum text-ink">{num(s.value)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel eyebrow={bi('血缘', 'Lineage')} title={bi('血缘边类型', 'Lineage Edge Kinds')}
          right={<Chip label={bi(`${catalog.lineage_edges.length} 边`, `${catalog.lineage_edges.length} edges`)} tone="info" />}>
          <Histogram bins={lineageBins} height={180} tone="special" />
        </Panel>

        <Panel eyebrow={bi('防泄漏门', 'Anti-leakage Gate')} title={bi('质量策略', 'Quality Policy')}
          right={<GateDot ok={policyOk} label={policyOk ? bi('全部强制', 'ENFORCED') : bi('有缺口', 'GAP')} />}
          tone={policyOk ? 'pos' : 'warn'}>
          <div className="flex flex-col gap-2.5">
            {policyRows.map(p => (
              <div key={String(p.label)} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-inset/40 px-3 py-2 text-xs">
                <span className="text-muted">{p.label}</span>
                <span className="flex items-center gap-2">
                  <span className={p.ok ? 'text-pos' : 'text-neg'}>{p.ok ? 'PASS' : 'FAIL'}</span>
                  <GateDot ok={p.ok} />
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('数据集', 'Datasets')} title={bi('数据集健康度', 'Dataset Health')}
        right={<Chip label={bi(`${catalog.datasets.length} 个`, `${catalog.datasets.length} sets`)} tone="info" />}>
        <DataTable columns={datasetCols} rows={catalog.datasets} getKey={d => d.dataset_id} dense />
      </Panel>

      <Panel eyebrow={bi('特征库', 'Feature Store')} title={bi('特征定义与 PIT 策略', 'Feature Definitions & PIT Policy')}
        right={<Chip label={bi(`${catalog.features.length} 个`, `${catalog.features.length} feat.`)} tone="accent" />}>
        <DataTable columns={featureCols} rows={catalog.features} getKey={f => f.feature_id} dense />
      </Panel>

      <Panel eyebrow={bi('血缘', 'Lineage')} title={bi('特征血缘链路', 'Feature Lineage Chain')}
        right={<Chip label={bi('数据集 → 特征 → 实验', 'dataset → feature → experiment')} tone="muted" />}>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {catalog.features.map(f => (
            <div key={f.feature_id} className="flex flex-col gap-2 rounded-lg border border-line bg-inset/40 p-3 text-xs">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="mono truncate text-info">{f.source_dataset_id}</span>
                <span className="text-faint">→</span>
                <span className="mono truncate text-accent">{f.name}</span>
                <span className="text-faint">→</span>
                <span className="mono truncate text-pos">{f.used_by_experiments.join(', ') || bi('无', 'none')}</span>
              </div>
              <KeyValue cols={2} items={[
                { k: bi('族', 'Family'), v: f.family },
                { k: bi('窗口', 'Lookback'), v: `${num(f.lookback_days)}d` },
                { k: bi('源滞后上限', 'Max lag'), v: `${num(f.point_in_time_policy.max_source_lag_days)}d` },
                { k: bi('质量', 'Quality'), v: pct(f.quality_score, 0) },
              ]} />
              <div className="flex items-center justify-between gap-2 border-t border-line pt-2">
                <span className="mono truncate text-2xs text-faint">{shortHash(f.lineage_hash, 12)}</span>
                <StatusChip value={f.point_in_time_policy.as_of_join ? 'PASS' : 'FAIL'} />
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </PageShell>
  );
}
