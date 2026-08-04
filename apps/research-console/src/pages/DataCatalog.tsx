import React, { useEffect, useMemo, useState } from 'react';
import { fetchDataCatalogSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type {
  DataCatalogDataset, DataCatalogFeature, DataCatalogPayload, DataChannel,
} from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, Chip, KeyValue, LoadingVeil, type Column,
} from '@/ui/primitives';
import { Donut, Histogram } from '@/ui/charts';
import { num, pct, fixed, compact, shortHash, type Tone } from '@/ui/format';

const emptySystemicRateLimit = {
  planned_ip_weight: 0,
  endpoint_weight_per_symbol: 0,
  configured_max_workers: 0,
  min_request_interval_ms: 0,
  cooldown_active: false,
  last_status_code: null,
  retry_after_seconds: 0,
  max_used_weights: {},
};

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
  data_channels: { status: 'UNAVAILABLE', channels: [], summary: {} },
  binance_realtime: {
    verdict: 'UNAVAILABLE',
    checked_at_ms: 0,
    universe_size: 14,
    stream: {
      status_present: false, effective_streaming: false, reported_streaming: false,
      fresh: false, age_ms: null, lag_ms: null, coverage: {}, connections: {},
      reconnects: {}, event_counts: {}, rejected_frames: null, chain_valid: null,
      routes: {}, source_path: null,
    },
    rest_snapshot: {
      status_present: false, fresh: false, age_ms: null, verdict: null,
      coverage: {}, chain_valid: null, errors: 0, fields: [], source_path: null,
    },
    systemic_risk: {
      status_present: false, fresh: false, age_ms: null, verdict: null,
      coverage: {}, chain_valid: null, row_count: 0, row_counts: {},
      inserted: {}, latest: {}, errors: 0,
      request_rate_limit: emptySystemicRateLimit,
      fields: [], source_path: null,
    },
    rpi_depth: {
      status_present: false, fresh: false, age_ms: null, verdict: null,
      coverage: {}, chain_valid: null, row_count: 0, inserted: 0,
      latest: {}, errors: 0, request_policy: {}, fields: [], source_path: null,
    },
    feature_projection: {
      status_present: false, fresh: false, age_ms: null, status: null,
      feature_version: null, feature_count: 0, instrument_count: 0,
      retained_value_count: 0, new_value_count: 0,
      eligible_source_snapshot_count: 0, skipped_existing_snapshot_count: 0,
      materialization_elapsed_ms: null, bounded_drift_read: false,
      drift_loaded_observation_count: 0, drift_history_limit_per_series: 0,
      drift_status: null, drift_status_counts: {},
      drift_series_count: 0, batch_chain_valid: null, calibration: null,
      calibration_id: null, warn_ks: null, block_ks: null,
      calibration_window_count: 0,
      load_bearing: false, source_path: null,
    },
    drift_alerting: {
      status_present: false, fresh: false, age_ms: null, status: null,
      current_severity: null, reason: null, transition: null,
      deduplicated: false, local_delivery: null, external_delivery: null,
      independent_worker: false, event_count: 0, chain_valid: null,
      source_path: null,
    },
    api_coverage: {
      status: 'INVALID',
      catalog_endpoint_count: 0,
      accounted_endpoint_count: 0,
      accounted_ratio: 0,
      load_bearing_endpoint_count: 0,
      gap_count: 0,
      gaps: [],
    },
    stream_coverage: {
      status: 'INVALID',
      catalog_stream_count: 0,
      accounted_stream_count: 0,
      accounted_ratio: 0,
      load_bearing_stream_count: 0,
      gap_count: 0,
      gaps: [],
    },
    safety: {
      public_market_data_only: true, credentials: false, private_stream: false,
      broker_connectivity: false, order_submission: false, live_trading: false,
    },
  },
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

function channelTone(status: string): Tone {
  if (status === 'REAL_HISTORY') return 'pos';
  if (status === 'REAL_SNAPSHOT' || status === 'REAL_DIAGNOSTIC' || status === 'REAL_RECENT') return 'info';
  if (status === 'PUBLIC_STREAM_REQUIRED') return 'warn';
  return 'neg';
}

function checkStats(dataset: DataCatalogDataset): { passed: number; total: number } {
  const total = dataset.checks.length;
  const passed = dataset.checks.filter(c => c.status === 'PASS').length;
  return { passed, total };
}

export default function DataCatalog() {
  const [refreshTick, setRefreshTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setRefreshTick(value => value + 1), 5_000);
    return () => window.clearInterval(timer);
  }, []);

  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<DataCatalogPayload>(
    s => fetchDataCatalogSnapshot(s),
    [refreshTick],
  );

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

  const channelCols: Column<DataChannel>[] = [
    {
      key: 'channel', header: bi('渠道', 'Channel'),
      render: c => (
        <div className="min-w-0">
          <div className="truncate font-medium text-ink">{c.channel_id}</div>
          <div className="truncate text-2xs text-faint">{c.provider} / {c.venue}</div>
        </div>
      ),
    },
    { key: 'dataset', header: bi('数据集', 'Dataset'), mono: true, render: c => c.dataset_id },
    { key: 'cadence', header: bi('频率', 'Cadence'), render: c => c.cadence },
    {
      key: 'status', header: bi('证据状态', 'Evidence'),
      render: c => <StatusChip value={c.artifact.status} />,
    },
    {
      key: 'availability', header: bi('可用性', 'Availability'),
      render: c => <Chip label={c.availability} tone={channelTone(c.availability)} />,
    },
    {
      key: 'artifact', header: bi('资产', 'Artifact'),
      render: c => <span className="mono truncate text-2xs text-muted">{c.artifact_path}</span>,
    },
    {
      key: 'fields', header: bi('字段', 'Fields'),
      render: c => <span className="truncate text-2xs text-muted">{c.fields.join(', ')}</span>,
    },
  ];

  const qPassRate = catalog.summary.quality_checks > 0
    ? catalog.summary.quality_passed / catalog.summary.quality_checks : 0;
  const realtime = catalog.binance_realtime ?? fallbackCatalog.binance_realtime;
  const streamCoverage = realtime.stream.coverage;
  const restCoverage = realtime.rest_snapshot.coverage;
  const systemicCoverage = realtime.systemic_risk.coverage;
  const systemicRateLimit = realtime.systemic_risk.request_rate_limit
    ?? emptySystemicRateLimit;
  const systemicUsedWeight = Math.max(
    0,
    ...Object.values(systemicRateLimit.max_used_weights),
  );
  const rpiCoverage = realtime.rpi_depth.coverage;
  const streamEvents = Object.values(realtime.stream.event_counts)
    .reduce((total, value) => total + value, 0);

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
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && catalog.data_provenance === 'REAL_PIT_DATASETS'}
        source={catalog.source} error={error} />

      <LoadingVeil active={apiState === 'loading'}>
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

      <Panel eyebrow="BINANCE USD-M REALTIME" title={bi('实时公共行情流', 'Real-time Public Market Stream')}
        right={<StatusChip value={realtime.verdict} />}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <StatTile label={bi('流状态', 'Stream')} value={realtime.stream.effective_streaming ? 'LIVE' : 'NOT LIVE'}
            tone={realtime.stream.effective_streaming ? 'pos' : 'warn'} />
          <StatTile label={bi('核心覆盖', 'Core Coverage')}
            value={`${num(streamCoverage.complete_core ?? 0)}/${num(streamCoverage.requested ?? realtime.universe_size)}`}
            tone={(streamCoverage.complete_core ?? 0) === realtime.universe_size ? 'pos' : 'warn'} />
          <StatTile label={bi('接收延迟', 'Receive Lag')}
            value={realtime.stream.lag_ms == null ? '—' : `${num(realtime.stream.lag_ms)}ms`}
            tone={realtime.stream.fresh ? 'pos' : 'warn'} />
          <StatTile label={bi('累计事件', 'Events')} value={compact(streamEvents)} />
          <StatTile label={bi('拒绝帧', 'Rejected')} value={num(realtime.stream.rejected_frames ?? 0)}
            tone={(realtime.stream.rejected_frames ?? 0) === 0 ? 'pos' : 'warn'} />
          <StatTile label={bi('流哈希链', 'Stream Chain')} value={realtime.stream.chain_valid ? 'VALID' : 'UNKNOWN'}
            tone={realtime.stream.chain_valid ? 'pos' : 'warn'} />
          <StatTile label={bi('REST 覆盖', 'REST Coverage')}
            value={`${num(restCoverage.complete ?? 0)}/${num(restCoverage.requested ?? realtime.universe_size)}`}
            tone={realtime.rest_snapshot.fresh ? 'pos' : 'warn'} />
          <StatTile label={bi('REST 哈希链', 'REST Chain')} value={realtime.rest_snapshot.chain_valid ? 'VALID' : 'UNKNOWN'}
            tone={realtime.rest_snapshot.chain_valid ? 'pos' : 'warn'} />
        </div>
        <details className="mt-4 rounded-lg border border-line bg-inset/30">
          <summary className="cursor-pointer select-none px-4 py-3 text-xs font-semibold text-muted">
            Market-data diagnostics and full evidence
          </summary>
          <div className="border-t border-line px-4 pb-4">
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatTile label={bi('官方目录', 'Official Catalog')}
            value={`${num(realtime.api_coverage.accounted_endpoint_count)}/${num(realtime.api_coverage.catalog_endpoint_count)}`}
            tone={realtime.api_coverage.status === 'VALIDATED_OFFICIAL_CATALOG' ? 'info' : 'warn'} />
          <StatTile label={bi('承载接口', 'Load-bearing APIs')}
            value={num(realtime.api_coverage.load_bearing_endpoint_count)} tone="pos" />
          <StatTile label={bi('明确缺口', 'Explicit Gaps')}
            value={num(realtime.api_coverage.gap_count)}
            tone={realtime.api_coverage.gap_count === 0 ? 'pos' : 'warn'} />
          <StatTile label={bi('覆盖契约', 'Coverage Contract')}
            value={realtime.api_coverage.status === 'VALIDATED_OFFICIAL_CATALOG' ? 'VALID' : 'INVALID'}
            tone={realtime.api_coverage.status === 'VALIDATED_OFFICIAL_CATALOG' ? 'pos' : 'warn'} />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatTile label={bi('官方流目录', 'Official WS Catalog')}
            value={`${num(realtime.stream_coverage.accounted_stream_count)}/${num(realtime.stream_coverage.catalog_stream_count)}`}
            tone={realtime.stream_coverage.status === 'VALIDATED_OFFICIAL_CATALOG' ? 'info' : 'warn'} />
          <StatTile label={bi('承载实时流', 'Load-bearing Streams')}
            value={num(realtime.stream_coverage.load_bearing_stream_count)} tone="pos" />
          <StatTile label={bi('实时流缺口', 'WS Explicit Gaps')}
            value={num(realtime.stream_coverage.gap_count)}
            tone={realtime.stream_coverage.gap_count === 0 ? 'pos' : 'warn'} />
          <StatTile label={bi('实时流契约', 'WS Coverage Contract')}
            value={realtime.stream_coverage.status === 'VALIDATED_OFFICIAL_CATALOG' ? 'VALID' : 'INVALID'}
            tone={realtime.stream_coverage.status === 'VALIDATED_OFFICIAL_CATALOG' ? 'pos' : 'warn'} />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <StatTile label={bi('系统风险通道', 'Systemic Risk')}
            value={realtime.systemic_risk.verdict ?? 'UNAVAILABLE'}
            tone={realtime.systemic_risk.fresh ? 'pos' : 'warn'} />
          <StatTile label={bi('系统风险覆盖', 'Systemic Coverage')}
            value={`${num(systemicCoverage.complete ?? 0)}/${num(systemicCoverage.requested ?? realtime.universe_size)}`}
            tone={realtime.systemic_risk.fresh ? 'pos' : 'warn'} />
          <StatTile label={bi('系统风险记录', 'Systemic Rows')}
            value={compact(realtime.systemic_risk.row_count)} />
          <StatTile label={bi('系统风险链', 'Systemic Chain')}
            value={realtime.systemic_risk.chain_valid ? 'VALID' : 'UNKNOWN'}
            tone={realtime.systemic_risk.chain_valid ? 'pos' : 'warn'} />
          <StatTile label={bi('计划 IP 权重', 'Planned IP Weight')}
            value={num(systemicRateLimit.planned_ip_weight)}
            sub={`${num(systemicRateLimit.endpoint_weight_per_symbol)}/symbol`} />
          <StatTile label={bi('观测 IP 权重', 'Observed IP Weight')}
            value={num(systemicUsedWeight)}
            sub={Object.keys(systemicRateLimit.max_used_weights)[0] ?? 'header unavailable'} />
          <StatTile label={bi('请求节奏', 'Request Pacing')}
            value={`${num(systemicRateLimit.min_request_interval_ms)}ms`}
            sub={`${num(systemicRateLimit.configured_max_workers)} worker`} />
          <StatTile label={bi('限流冷却', 'Rate-limit Cooldown')}
            value={systemicRateLimit.cooldown_active ? 'ACTIVE' : 'CLEAR'}
            tone={systemicRateLimit.cooldown_active ? 'neg' : 'pos'}
            sub={systemicRateLimit.cooldown_active
              ? `${num(systemicRateLimit.retry_after_seconds)}s / HTTP ${systemicRateLimit.last_status_code ?? '—'}`
              : '429/418 guard ready'} />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatTile label={bi('RPI 深度', 'RPI Depth')}
            value={realtime.rpi_depth.verdict ?? 'UNAVAILABLE'}
            tone={realtime.rpi_depth.fresh ? 'pos' : 'warn'} />
          <StatTile label={bi('RPI 覆盖', 'RPI Coverage')}
            value={`${num(rpiCoverage.complete ?? 0)}/${num(rpiCoverage.requested ?? realtime.universe_size)}`}
            tone={realtime.rpi_depth.fresh ? 'pos' : 'warn'} />
          <StatTile label={bi('RPI 快照', 'RPI Snapshots')}
            value={compact(realtime.rpi_depth.row_count)} />
          <StatTile label={bi('RPI 哈希链', 'RPI Chain')}
            value={realtime.rpi_depth.chain_valid ? 'VALID' : 'UNKNOWN'}
            tone={realtime.rpi_depth.chain_valid ? 'pos' : 'warn'} />
        </div>
        {realtime.api_coverage.gaps.length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {realtime.api_coverage.gaps.map(gap => (
              <Chip key={gap.id} label={`${gap.title} · ${gap.path}`} tone="warn" />
            ))}
          </div>
        ) : null}
        {realtime.stream_coverage.gaps.length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {realtime.stream_coverage.gaps.map(gap => (
              <Chip key={gap.id} label={`${gap.title} · ${gap.route} · ${gap.stream}`} tone="warn" />
            ))}
          </div>
        ) : null}
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <StatTile label={bi('实时特征', 'Realtime Features')}
            value={num(realtime.feature_projection.feature_count)}
            tone={realtime.feature_projection.fresh ? 'pos' : 'warn'} />
          <StatTile label={bi('特征标的', 'Feature Instruments')}
            value={num(realtime.feature_projection.instrument_count)} />
          <StatTile label={bi('保留值', 'Retained Values')}
            value={compact(realtime.feature_projection.retained_value_count)} />
          <StatTile label={bi('漂移序列', 'Drift Series')}
            value={num(realtime.feature_projection.drift_series_count)} />
          <StatTile label={bi('漂移状态', 'Drift Status')}
            value={realtime.feature_projection.drift_status ?? '—'}
            tone={realtime.feature_projection.drift_status === 'BLOCK' ? 'warn' : 'info'} />
          <StatTile label={bi('稳定', 'Stable')}
            value={num(realtime.feature_projection.drift_status_counts.PASS ?? 0)} tone="pos" />
          <StatTile label={bi('警告/阻断', 'Warn / Block')}
            value={`${num(realtime.feature_projection.drift_status_counts.WARN ?? 0)} / ${num(realtime.feature_projection.drift_status_counts.BLOCK ?? 0)}`}
            tone="warn" />
          <StatTile label={bi('特征批次链', 'Feature Batch Chain')}
            value={realtime.feature_projection.batch_chain_valid ? 'VALID' : 'UNKNOWN'}
            tone={realtime.feature_projection.batch_chain_valid ? 'pos' : 'warn'} />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <StatTile label={bi('漂移校准', 'Drift Calibration')}
            value={realtime.feature_projection.calibration === 'EMPIRICALLY_CALIBRATED_PIT_V1' ? 'CALIBRATED' : 'UNAVAILABLE'}
            tone={realtime.feature_projection.calibration_id ? 'pos' : 'warn'} />
          <StatTile label={bi('校准窗口', 'Calibration Windows')}
            value={num(realtime.feature_projection.calibration_window_count)} />
          <StatTile label={bi('警告阈值', 'Warn KS')}
            value={realtime.feature_projection.warn_ks == null ? '—' : realtime.feature_projection.warn_ks.toFixed(3)} />
          <StatTile label={bi('阻断阈值', 'Block KS')}
            value={realtime.feature_projection.block_ks == null ? '—' : realtime.feature_projection.block_ks.toFixed(3)} />
          <StatTile label={bi('本轮新增', 'New Values')}
            value={compact(realtime.feature_projection.new_value_count)}
            tone={realtime.feature_projection.fresh ? 'pos' : 'warn'} />
          <StatTile label={bi('增量快照', 'Incremental Snapshots')}
            value={num(realtime.feature_projection.eligible_source_snapshot_count)} />
          <StatTile label={bi('物化耗时', 'Materialization')}
            value={realtime.feature_projection.materialization_elapsed_ms == null
              ? '—'
              : `${num(realtime.feature_projection.materialization_elapsed_ms)} ms`}
            tone={realtime.feature_projection.bounded_drift_read ? 'pos' : 'warn'} />
          <StatTile label={bi('漂移输入', 'Drift Input')}
            value={compact(realtime.feature_projection.drift_loaded_observation_count)} />
          <StatTile label={bi('独立告警', 'Alert Worker')}
            value={realtime.drift_alerting.independent_worker ? 'RUNNING' : 'UNAVAILABLE'}
            tone={realtime.drift_alerting.fresh ? 'pos' : 'warn'} />
          <StatTile label={bi('本地投递', 'Local Delivery')}
            value={realtime.drift_alerting.local_delivery ?? '—'}
            tone={realtime.drift_alerting.local_delivery === 'DELIVERED' ? 'pos' : 'info'} />
          <StatTile label={bi('外部投递', 'External Delivery')}
            value={realtime.drift_alerting.external_delivery ?? '—'} />
          <StatTile label={bi('告警链', 'Alert Chain')}
            value={realtime.drift_alerting.chain_valid ? 'VALID' : 'UNKNOWN'}
            tone={realtime.drift_alerting.chain_valid ? 'pos' : 'warn'} />
        </div>
        <div className="mt-2 text-2xs text-muted">
          {bi(
            '实时漂移阈值来自当前窗口之外的 PIT 经验校准；独立告警只通知运营，不驱动晋级、仓位或订单。外部通道未配置时明确显示 UNCONFIGURED。',
            'Realtime thresholds use pre-current PIT empirical calibration. Independent alerts notify operators only and never drive promotion, positions, or orders; an absent external channel is explicit as UNCONFIGURED.',
          )}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {realtime.rest_snapshot.fields.map(field => (
            <Chip key={field} label={field} tone="muted" />
          ))}
          {realtime.systemic_risk.fields.map(field => (
            <Chip key={`systemic-${field}`} label={field} tone="info" />
          ))}
          {realtime.rpi_depth.fields.map(field => (
            <Chip key={`rpi-${field}`} label={field} tone="special" />
          ))}
        </div>
        <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
          <KeyValue cols={2} items={[
            { k: 'Public route', v: realtime.stream.routes.public ?? '—' },
            { k: 'Market route', v: realtime.stream.routes.market ?? '—' },
            { k: bi('公共连接', 'Public link'), v: realtime.stream.connections.public ? 'CONNECTED' : 'DOWN' },
            { k: bi('市场连接', 'Market link'), v: realtime.stream.connections.market ? 'CONNECTED' : 'DOWN' },
          ]} />
          <KeyValue cols={2} items={Object.entries(realtime.stream.event_counts).map(([key, value]) => ({
            k: key, v: compact(value),
          }))} />
          <div className="flex flex-wrap content-start gap-2 rounded-lg border border-line bg-inset/40 p-3">
            <Chip label="PUBLIC DATA ONLY" tone="pos" />
            <Chip label="NO CREDENTIALS" tone="pos" />
            <Chip label="NO PRIVATE STREAM" tone="pos" />
            <Chip label="NO BROKER" tone="pos" />
            <Chip label="NO ORDER SUBMISSION" tone="pos" />
            <Chip label="L2 DRY-RUN ONLY" tone="info" />
          </div>
        </div>
          </div>
        </details>
      </Panel>

      <details className="rounded-xl border border-line bg-surface/40">
        <summary className="cursor-pointer select-none px-4 py-3 text-xs font-semibold uppercase tracking-[0.12em] text-muted">
          Datasets, features, and lineage evidence
        </summary>
        <div className="space-y-4 border-t border-line p-4">
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

      <Panel eyebrow={bi('渠道', 'Channels')} title={bi('真实数据获取渠道', 'Real Data Acquisition Channels')}
        right={<Chip label={bi(`${catalog.data_channels.channels.length} 个`, `${catalog.data_channels.channels.length} channels`)}
          tone={catalog.data_channels.status === 'REAL_COVERAGE_INVENTORY' ? 'info' : 'warn'} />}>
        <DataTable columns={channelCols} rows={catalog.data_channels.channels} getKey={c => c.channel_id} dense />
        {catalog.data_channels.honesty?.length ? (
          <div className="mt-3 text-2xs text-faint">{catalog.data_channels.honesty.join(' · ')}</div>
        ) : null}
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
        </div>
      </details>
      </LoadingVeil>
    </PageShell>
  );
}
