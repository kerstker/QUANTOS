import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, LoadingVeil, KeyValue, StatusChip,
} from '@/ui/primitives';
import { CandleChart, type Candle } from '@/ui/charts';
import { AsOfStamp, EndpointFootnote, StatusBanner, StateView } from '@/ui/workbench';
import { num, pctPoints } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchMexcMarketSnapshot } from '@/lib/api/research-console-api';
import type { MexcMarketPayload } from '@/lib/types/research';

/**
 * MEXC 市场 — zero-fee BTC spot market snapshot, surfaced read-only from
 * /api/v1/console/mexc-market (offline klines artifact, NOT a live feed).
 * Honest boundary: the endpoint ships aggregate indicators only; the candle
 * chart renders real per-bar OHLC only if the snapshot ever carries `bars`
 * (mock mode shows clearly-labelled deterministic fixtures). No order path.
 */

const mockPayload: MexcMarketPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_SNAPSHOT',
  venue: 'MEXC', symbol: 'BTCUSDT',
  latest_price: null, change_24h_pct: null, volatility_30d_ann: null,
  coverage: { date_start: null, date_end: null, n_rows: 0 },
  freshness: { fetched_at: null, age_hours: null, fresh_within_hours: 72, is_fresh: false },
  db: { present: false, n_rows: 0, date_start: null, date_end: null },
  honesty_note: 'Mock fixture — no offline MEXC klines artifact. Run the fetch driver off-gate to produce one.',
};

// Deterministic mock candles (mock mode only, labelled synthetic by the ribbon).
const mockBars: Candle[] = Array.from({ length: 90 }, (_, i) => {
  const base = 60_000 * (1 + 0.08 * Math.sin(i * 0.11) + 0.03 * Math.sin(i * 0.031));
  const open = base * (1 + 0.004 * Math.sin(i * 0.7));
  const close = base * (1 + 0.004 * Math.cos(i * 0.6));
  return {
    t: 1_700_000_000 + i * 86_400,
    open,
    high: Math.max(open, close) * 1.006,
    low: Math.min(open, close) * 0.994,
    close,
    volume: 800 + 300 * Math.abs(Math.sin(i * 0.23)),
  };
});

export default function MexcMarket() {
  const { payload, apiState, error, usingBackend, snapshotState } = useConsoleSnapshot<MexcMarketPayload>(
    s => fetchMexcMarketSnapshot(s));
  const p: MexcMarketPayload = usingBackend ? payload! : mockPayload;
  const has = p.snapshot_present;
  // Real per-bar OHLC only when the snapshot carries it; mock mode falls back to fixtures.
  const bars: Candle[] = p.bars && p.bars.length >= 2 ? p.bars : usingBackend ? [] : mockBars;
  const change = p.change_24h_pct;

  return (
    <PageShell>
      <PageHeader title="MEXC Market" titleZh="MEXC 市场"
        subtitle={bi('零手续费 BTC 现货只读快照(离线抓取,非实时推送)· 绝无下单/凭证路径',
          'Zero-fee BTC spot read-only snapshot (offline fetch, not a live feed) · no order/credential path')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_MEXC_MARKET_SNAPSHOT'}
        source={usingBackend ? p.data_provenance : 'FRONTEND_MEXC_MARKET_MOCK'}
        error={error}
        noDataYet={usingBackend && !p.snapshot_present}
        dataStatus={snapshotState} />
      <StatusBanner />

      <LoadingVeil active={apiState === 'loading'}>
        <div className="flex flex-wrap items-center gap-3">
          <StatusChip value={p.verdict} />
          <span className="text-2xs text-muted">{p.venue} · {p.symbol}</span>
          <AsOfStamp ts={p.freshness.fetched_at} ageHours={p.freshness.age_hours}
            freshWithinHours={p.freshness.fresh_within_hours} />
        </div>

        <KpiStrip cols={5}>
          <StatTile label={bi('最新价', 'Latest')} unit="USDT"
            value={p.latest_price == null ? '—' : num(p.latest_price, 0)} tone="accent" accent />
          <StatTile label={bi('24h 涨跌', '24h change')}
            value={change == null ? '—' : pctPoints(change, 2)}
            tone={change == null ? 'muted' : change >= 0 ? 'pos' : 'neg'}
            sub={bi('最近两根日 K 收盘差', 'last two daily closes')} />
          <StatTile label={bi('30 日年化波动', '30d ann. vol')}
            value={p.volatility_30d_ann == null ? '—' : pctPoints(p.volatility_30d_ann * 100, 1)}
            tone="info" sub={bi('对数收益年化', 'log-return annualized')} />
          <StatTile label={bi('覆盖行数', 'Coverage rows')}
            value={has ? String(p.coverage.n_rows) : '—'} tone="muted"
            sub={has ? `${p.coverage.date_start ?? '?'} → ${p.coverage.date_end ?? '?'}` : bi('无快照', 'no snapshot')} />
          <StatTile label={bi('数据年龄', 'Data age')}
            value={p.freshness.age_hours == null ? '—' : `${p.freshness.age_hours.toFixed(1)}h`}
            tone={p.freshness.is_fresh ? 'pos' : 'warn'}
            sub={bi(`新鲜度上限 ${p.freshness.fresh_within_hours}h`, `freshness limit ${p.freshness.fresh_within_hours}h`)} />
        </KpiStrip>

        <Panel eyebrow="BTCUSDT · DAILY" title={bi('日 K 线(离线快照)', 'Daily candles (offline snapshot)')}
          className="terminal-panel"
          right={<span className="text-2xs text-faint">{bi('MA 7 / 30', 'MA 7 / 30')}</span>}>
          <StateView
            state={bars.length >= 2 ? 'ready' : 'empty'}
            empty={usingBackend
              ? bi('端点未下发逐根 K 线(仅聚合指标)——不伪造蜡烛;覆盖统计见下',
                  'the endpoint ships aggregate indicators only, no per-bar OHLC — candles are never fabricated; see coverage below')
              : bi('无 K 线数据', 'no candle data')}>
            <CandleChart bars={bars} height={300} maPeriods={[7, 30]}
              yFormat={v => num(v, 0)} />
          </StateView>
        </Panel>

        <Panel eyebrow={bi('覆盖与新鲜度', 'COVERAGE & FRESHNESS')} title={bi('数据源明细', 'Source detail')}>
          <KeyValue cols={3} items={[
            { k: bi('JSON 覆盖', 'JSON coverage'), v: has ? `${p.coverage.date_start ?? '?'} → ${p.coverage.date_end ?? '?'} (${p.coverage.n_rows})` : '—' },
            { k: bi('PIT 库', 'PIT db'), v: p.db.present ? `${p.db.date_start ?? '?'} → ${p.db.date_end ?? '?'} (${p.db.n_rows})` : bi('缺失', 'absent') },
            { k: bi('新鲜度', 'Freshness'), v: p.freshness.age_hours == null ? '—' : `${p.freshness.age_hours.toFixed(1)}h / ${p.freshness.fresh_within_hours}h` },
          ]} />
          <div className="mt-3 text-2xs text-muted">{p.honesty_note}</div>
        </Panel>

        <EndpointFootnote path="/api/v1/console/mexc-market"
          note={bi('只读 · 研究/展示用途,不构成任何下单能力', 'read-only · research/display only, no order capability')} />
      </LoadingVeil>
    </PageShell>
  );
}
