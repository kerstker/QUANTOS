import { Fragment, useCallback, useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, LoadingVeil,
  DataTable, Chip, GateDot, KeyValue, StatusChip, type Column,
} from '@/ui/primitives';
import { AsOfStamp, ConfirmActionButton, EndpointFootnote, MandateGateChip, StateView, StatusBanner } from '@/ui/workbench';
import { fixed, num, stamp, type Tone } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchGoLiveReadiness, fetchMexcReadinessSnapshot } from '@/lib/api/research-console-api';
import {
  LiveOpsHttpError, postLiveOpsPreview, useOpsToken,
  type LiveOpsPreviewResponse, type LiveOpsWouldSubmitOrder,
} from '@/lib/api/live-ops-api';
import type {
  GoLiveGuidance, GoLiveReadinessPayload, GoLiveStrategy, GoLiveStrategyLeg, GoLiveVenue,
  MexcReadinessCheck, MexcReadinessPayload,
} from '@/lib/types/research';

/**
 * Go-Live 上线向导 — read-only readiness wizard over GET /api/v1/console/go-live-readiness
 * (offline artifact evidence/go_live_readiness.json, driver tools/go_live_readiness.py).
 * Honest boundary: all-green only means "materials for human review exist" — it never
 * grants live capability. The mandate is human-signed in key/ (the frontend never fills
 * it in). The final-step panel offers ONE action (2026-07-24): an armed-preview drill
 * via POST {ops}/api/v1/liveops/preview — server-fixed argv that never carries
 * `--submit`; PreviewVenue structurally cannot place orders (real mainnet account
 * reads, zero order placement). The final --submit keystroke stays deliberately
 * human-CLI-only: shown as copyable text with no trigger anywhere.
 * Absent snapshot (driver never ran) renders an honest empty StateView, never faked rows.
 * 2026-07-25: the retired MexcReadiness page merged in here — the mexc venue card carries an
 * expandable「MEXC 专项检查」section with the five checks unique to
 * /api/v1/console/mexc-readiness (adapter / governance test / data freshness / dry-run
 * evidence / live-flag) plus the four-stage graduation path (mandate / attestation /
 * kill-switch were already covered by the venue card). The mexc-readiness BACKEND endpoint
 * and its client fetcher stay live; only the page retired.
 */

const CROWD_LEGS: GoLiveStrategyLeg[] = [
  { instrument: 'AAVE', weight: 0.07142857142857142, price: 95.7 },
  { instrument: 'ADA', weight: -0.07142857142857142, price: 0.1737 },
  { instrument: 'APT', weight: 0.07142857142857142, price: 0.61 },
  { instrument: 'BCH', weight: 0.07142857142857142, price: 223.9 },
  { instrument: 'BNB', weight: -0.07142857142857142, price: 573.95 },
  { instrument: 'BTC', weight: 0.07142857142857142, price: 117000 },
  { instrument: 'DOGE', weight: -0.07142857142857142, price: 0.19 },
  { instrument: 'DOT', weight: 0.07142857142857142, price: 3.9 },
  { instrument: 'ETC', weight: -0.07142857142857142, price: 18.5 },
  { instrument: 'FIL', weight: 0.07142857142857142, price: 2.4 },
  { instrument: 'LTC', weight: -0.07142857142857142, price: 108 },
  { instrument: 'OP', weight: 0.07142857142857142, price: 0.55 },
  { instrument: 'SOL', weight: -0.07142857142857142, price: 160 },
  { instrument: 'XRP', weight: 0.07142857142857142, price: 3.1 },
];

const mockVenue: GoLiveVenue = {
  venue: 'binance_mainnet', mainnet: true, suffix: '_binance_mainnet',
  key_permissions: { status: 'SAFE', checked_at_ms: 1784875153972 },
  smoke: { verdict: 'PASS', age_ms: 3422730 },
  mandate: {
    path: 'key\\live_mandate_binance_mainnet.json', present: true, loads: true,
    expired: false, expires_at: 1792816121000,
    venue_domain_ok: true, exchange_domain_ok: true,
    expected_venue: 'mainnet', expected_exchange: 'binance',
    allowed_instruments: ['BTC', 'ETH'],
    max_gross_notional: 100, max_single_order_notional: 100, max_net_notional: 100,
  },
  attestations: { crowd_contra: { present: true, kind: 'human_override' } },
  kill_switch_tripped: false,
  last_run: {
    status_path: 'data\\cache\\live_track_status_binance_mainnet.json',
    present: false, at_ms: null, summary: {},
  },
};

const mockMexcVenue: GoLiveVenue = {
  venue: 'mexc', mainnet: true, suffix: '_mexc',
  key_permissions: { status: 'UNVERIFIED', checked_at_ms: null },
  smoke: { verdict: 'ABSENT', age_ms: null },
  mandate: {
    path: 'key\\live_mandate_mexc.json', present: false, loads: false,
    expired: false, expires_at: null,
    venue_domain_ok: false, exchange_domain_ok: false,
    expected_venue: 'mainnet', expected_exchange: 'mexc',
    allowed_instruments: [],
    max_gross_notional: null, max_single_order_notional: null, max_net_notional: null,
  },
  attestations: {},
  kill_switch_tripped: false,
  last_run: {
    status_path: 'data\\cache\\live_track_status_mexc.json',
    present: false, at_ms: null, summary: {},
  },
};

const mockGuidance: GoLiveGuidance = {
  venue: 'binance_mainnet', strategy: 'crowd_contra',
  required_instruments: CROWD_LEGS.map(l => l.instrument),
  missing_from_mandate: ['AAVE', 'ADA', 'APT', 'BCH', 'BNB', 'DOGE', 'DOT', 'ETC', 'FIL', 'LTC', 'OP', 'SOL', 'XRP'],
  stale_in_mandate: ['ETH'],
  min_deploy_for_all_legs: 700, min_deploy_binding_leg: 'BTC', deploy_target_usdt: 1000,
  suggested_caps: { max_gross_notional: 1100, max_single_order_notional: 150, max_net_notional: 100, per_instrument: 150 },
  warnings: ['Mock fixture — the real warnings are computed by tools/go_live_readiness.py'],
};

const mockPayload: GoLiveReadinessPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REAL_GO_LIVE_READINESS', snapshot_status: 'REAL', snapshot_present: true,
  artifact: 'GO_LIVE_READINESS', generated_at_ms: 1784875191166,
  readiness: 'L2_DRY_RUN_ONLY',
  venues: [mockVenue, mockMexcVenue],
  strategies: {
    crowd_contra: { decided_ts: 1784592000000, n_legs: 14, gross: 1.0, legs: CROWD_LEGS },
    btc_spot_trend: { decided_ts: 0, n_legs: 0, gross: 0, legs: [] },
  },
  guidance: mockGuidance,
  honesty_note: 'Mock fixture — real payload is evidence/go_live_readiness.json served verbatim. All-green never grants live capability.',
};

/* ---------- small local helpers ------------------------------------------- */

function ageLabel(ms: number | null): string {
  if (ms == null) return '—';
  const h = ms / 3.6e6;
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`;
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

function keyPermTone(status: string): Tone {
  return status === 'SAFE' ? 'pos' : status === 'NOT_SAFE' ? 'neg' : 'warn';
}

function smokeTone(verdict: string): Tone {
  return verdict === 'PASS' ? 'pos' : verdict === 'FAIL' ? 'neg' : 'warn';
}

/** Read-only copyable block (command / JSON snippet). Copy is best-effort; in
    environments without a clipboard API the text stays manually selectable. */
function CopyBlock({ code, label }: { code: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    const clip = typeof navigator !== 'undefined' ? navigator.clipboard : undefined;
    if (!clip) return;
    clip.writeText(code).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }).catch(() => { /* clipboard denied — text remains manually selectable */ });
  };
  return (
    <div className="relative rounded-lg border border-line bg-inset/60">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
        <span className="text-3xs text-faint">{label ?? bi('只读 · 可复制', 'read-only · copyable')}</span>
        <button type="button" onClick={copy}
          className="rounded border border-line px-2 py-0.5 text-3xs text-muted hover:text-ink">
          {copied ? bi('已复制 ✓', 'copied ✓') : bi('复制', 'copy')}
        </button>
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all px-3 py-2 text-2xs mono text-ink">{code}</pre>
    </div>
  );
}

function mandateStateOf(m: GoLiveVenue['mandate']): 'SIGNED' | 'UNSIGNED' | 'EXPIRED' | 'UNKNOWN' {
  if (!m.present || !m.loads) return 'UNSIGNED';
  if (m.expired) return 'EXPIRED';
  return 'SIGNED';
}

/* ---------- MEXC 专项检查(2026-07-25 自退役的 MexcReadiness 页并入) ----------
   The mexc venue card's expandable section: the five checks UNIQUE to
   /api/v1/console/mexc-readiness plus the four-stage graduation path. mandate /
   attestation / kill-switch are already shown by the venue card itself, so they
   are not duplicated here. */

const MEXC_UNIQUE_CHECK_IDS: readonly string[] = [
  'adapter_exists', 'adapter_governance_test', 'market_data_fresh', 'dry_run_evidence', 'live_flag_disarmed',
];

/** MEXC 无现货测试网 → 毕业路径四阶段(文档 docs/MEXC_INTEGRATION.md §8)。 */
const MEXC_GRADUATION_STAGES = [
  { id: 'dry_run', zh: '干跑执行证据', en: 'dry-run evidence' },
  { id: 'paper', zh: '前向 paper-track', en: 'forward paper-track' },
  { id: 'tiny_live', zh: '小额真实 + 人工 mandate', en: 'tiny real + human mandate' },
  { id: 'scale', zh: '规模化(新多方批准)', en: 'scale (new multi-party approval)' },
] as const;

const mockMexcReadiness: MexcReadinessPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REAL_MEXC_READINESS_CHECK', snapshot_present: true, verdict: 'BLOCKED',
  venue: 'MEXC', readiness: 'L2_DRY_RUN_ONLY',
  n_ready: 3, n_blocked: 2,
  checks: [
    { id: 'adapter_exists', label: 'MEXC 现货适配器 live/exchange/mexc_spot.py', status: 'READY', reason: 'mock' },
    { id: 'adapter_governance_test', label: '适配器治理边界测试', status: 'READY', reason: 'mock' },
    { id: 'market_data_fresh', label: 'MEXC 行情数据新鲜度', status: 'BLOCKED', reason: 'mock' },
    { id: 'dry_run_evidence', label: 'btc_spot_trend 干跑执行证据', status: 'BLOCKED', reason: 'mock' },
    { id: 'live_flag_disarmed', label: 'live_trading_armed 默认关闭', status: 'READY', reason: 'mock' },
  ],
  kill_switch: { tripped: false, reason: null },
  honesty_note: 'Mock fixture — the real checklist is computed live by the backend.',
};

function MexcChecksSection() {
  const { payload, apiState, usingBackend } = useConsoleSnapshot<MexcReadinessPayload>(
    s => fetchMexcReadinessSnapshot(s));
  const p: MexcReadinessPayload = usingBackend ? payload! : mockMexcReadiness;
  const checks: MexcReadinessCheck[] = p.checks.filter(c => MEXC_UNIQUE_CHECK_IDS.includes(c.id));
  // Graduation path: stage 1 needs dry-run evidence; stage 2+ honestly not yet
  // tracked by the checklist (no forward-track check exists yet) → always pending.
  const dryRunReady = p.checks.find(c => c.id === 'dry_run_evidence')?.status === 'READY';
  const stageState: Record<string, 'done' | 'current' | 'pending'> = {
    dry_run: dryRunReady ? 'done' : 'current',
    paper: dryRunReady ? 'current' : 'pending',
    tiny_live: 'pending',
    scale: 'pending',
  };
  return (
    <details className="mt-2 rounded-md border border-line bg-inset/40 px-2.5 py-1.5">
      <summary className="cursor-pointer text-2xs font-semibold text-muted">
        {bi('MEXC 专项检查(适配器 / 数据 / 干跑 / 毕业路径)', 'MEXC-specific checks (adapter / data / dry-run / graduation path)')}
        {apiState === 'loading' && <span className="ml-1.5 text-3xs text-faint">…</span>}
        {!usingBackend && apiState !== 'loading' && (
          <span className="ml-1.5 text-3xs text-faint">{bi('· 演示数据', '· mock')}</span>
        )}
      </summary>
      <div className="mt-2 flex flex-col gap-1">
        {checks.map(c => (
          <div key={c.id} className="flex flex-wrap items-center gap-1.5 text-2xs">
            <GateDot ok={c.status === 'READY'} />
            <span className="text-ink">{c.label}</span>
            <StatusChip value={c.status} />
            <span className="text-3xs text-faint">{c.reason}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-stretch gap-1.5">
        {MEXC_GRADUATION_STAGES.map((s, i) => {
          const st = stageState[s.id];
          const tone = st === 'done' ? 'var(--pos)' : st === 'current' ? 'var(--warn)' : 'var(--faint)';
          return (
            <Fragment key={s.id}>
              <div className="min-w-[120px] flex-1 rounded-md border px-2 py-1.5"
                style={{
                  borderColor: `color-mix(in srgb, ${tone} 35%, transparent)`,
                  background: `color-mix(in srgb, ${tone} ${st === 'done' ? 8 : 4}%, transparent)`,
                }}>
                <div className="text-3xs" style={{ color: tone }}>
                  {i + 1}. {st === 'done' ? '✓' : st === 'current' ? '◐' : '○'} {bi(s.zh, s.en)}
                </div>
              </div>
              {i < MEXC_GRADUATION_STAGES.length - 1 && <div className="self-center text-muted">→</div>}
            </Fragment>
          );
        })}
      </div>
      <div className="mt-1.5 text-3xs text-faint">
        {bi('阶段 2(前向 paper-track)尚未被清单机器跟踪 —— 诚实标为当前/待解锁,不假装完成。任何阶段跨越都是人工动作;AI 绝不触碰凭证、绝不下真实订单。',
          'Stage 2 (forward paper-track) is not yet machine-tracked — honestly shown as current/pending, never faked. Every stage crossing is a human action; AI never touches credentials, never places real orders.')}
      </div>
    </details>
  );
}

/* ---------- armed-preview drill result (POST {ops}/api/v1/liveops/preview) -- */

const wouldSubmitCols: Column<LiveOpsWouldSubmitOrder>[] = [
  { key: 'inst', header: bi('标的', 'Instrument'), render: o => <span className="mono text-ink">{o.instrument_id}</span> },
  { key: 'side', header: bi('方向', 'Side'), render: o => <Chip label={o.side} tone={o.side === 'BUY' ? 'pos' : 'neg'} /> },
  { key: 'qty', header: bi('数量', 'Qty'), align: 'right', mono: true, render: o => fixed(o.quantity, 6) },
  { key: 'type', header: bi('类型', 'Type'), render: o => <span className="text-muted">{o.order_type}</span> },
  { key: 'limit', header: bi('限价', 'Limit'), align: 'right', mono: true,
    render: o => (o.limit_price != null ? num(o.limit_price, 4) : '—') },
];

function StdoutTail({ tail }: { tail: string }) {
  const t = tail.trim();
  if (!t) return null;
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-3xs text-faint">stdout_tail</summary>
      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded border border-line bg-inset/60 px-2 py-1.5 text-3xs mono text-muted">{t}</pre>
    </details>
  );
}

/** Honest render of one preview drill: ok:false → rc + tail; status null →
    missing/stale snapshot said so plainly; success → summary chips + the exact
    "would have submitted" order list + frozen/funding info. */
function PreviewResultBlock({ result }: { result: LiveOpsPreviewResponse }) {
  const st = result.status;
  if (!result.ok) {
    return (
      <div className="mt-2 rounded-lg border border-neg/40 bg-neg/5 px-3 py-2 text-2xs">
        <div className="font-semibold text-neg">
          {bi('演练失败(驱动器非零退出)', 'drill failed (driver non-zero exit)')} · rc={result.returncode ?? '—'}
        </div>
        <StdoutTail tail={result.stdout_tail} />
      </div>
    );
  }
  if (!st) {
    return (
      <div className="mt-2 rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-2xs text-warn">
        {bi('驱动器跑完,但本次 status 快照缺失/陈旧 —— 诚实不把旧结果当本次演练成果,无 would_submit 可展示。',
          'driver finished, but this run’s status snapshot is missing/stale — old results are honestly not shown as this drill’s outcome; no would_submit to render.')}
        <StdoutTail tail={result.stdout_tail} />
      </div>
    );
  }
  const ws = st.would_submit ?? [];
  const fundingStatus = st.funding_check?.status ?? null;
  const fundingFrozen = st.funding_check?.frozen ?? [];
  return (
    <div className="mt-2 rounded-lg border border-pos/40 bg-pos/5 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip label={`MODE ${st.mode}`} tone="info" />
        <Chip label={st.armed ? 'ARMED_PREVIEW' : 'UNARMED_PREVIEW'} tone={st.armed ? 'warn' : 'muted'} />
        <Chip label={`TIER ${st.tier}`} tone="muted" />
        {st.batch_skipped
          ? <Chip label={`SKIPPED · ${st.batch_skipped}`} tone="warn" />
          : <Chip label={bi(`本会提交 ${ws.length} 单`, `would submit ${ws.length}`)} tone={ws.length > 0 ? 'accent' : 'muted'} />}
        {st.n_rejected_by_gate > 0 && <Chip label={bi(`门拒 ${st.n_rejected_by_gate}`, `gate-rejected ${st.n_rejected_by_gate}`)} tone="warn" />}
        {st.n_frozen > 0 && <Chip label={bi(`冻结 ${st.n_frozen}`, `frozen ${st.n_frozen}`)} tone="neg" />}
        {fundingStatus && (
          <Chip label={`FUNDING ${fundingStatus}${st.funding_check?.n_frozen ? ` · -${st.funding_check.n_frozen}` : ''}`}
            tone={fundingStatus === 'ran' ? (st.funding_check?.n_frozen ? 'warn' : 'pos') : 'muted'} />
        )}
        <span className="ml-auto"><AsOfStamp ts={st.ran_at} /></span>
      </div>
      {st.batch_skipped
        ? <div className="mt-2 text-2xs text-warn">{bi('本轮被跳过(无订单意图):', 'batch skipped (no order intents):')} {st.batch_skipped}</div>
        : ws.length > 0
          ? <div className="mt-2">
              <div className="mb-1 text-3xs text-faint">{bi('本来会提交的订单(PreviewVenue 记录,零真实提交)', 'orders the pipeline WOULD have placed (PreviewVenue record, zero real submission)')}</div>
              <DataTable rows={ws} columns={wouldSubmitCols} getKey={(o, i) => `${o.instrument_id}-${i}`} density="compact" />
            </div>
          : <div className="mt-2 text-2xs text-muted">{bi('本轮零订单意图(目标=现状,无需调仓)。', 'zero order intents this cycle (target = current, nothing to rebalance).')}</div>}
      {fundingFrozen.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-2xs">
          <span className="text-neg">{bi('资金费核对冻结(仓位不动):', 'funding-check frozen (positions untouched):')}</span>
          {fundingFrozen.map(f => <Chip key={f} label={f} tone="neg" />)}
        </div>
      )}
      {(st.alerts ?? []).length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-3xs text-faint">{bi(`告警 (${(st.alerts ?? []).length})`, `alerts (${(st.alerts ?? []).length})`)}</summary>
          <ul className="mt-1 list-disc pl-4 text-2xs text-muted">
            {(st.alerts ?? []).map((a, i) => <li key={i}>{a}</li>)}
          </ul>
        </details>
      )}
      <StdoutTail tail={result.stdout_tail} />
    </div>
  );
}

/* ---------- page ----------------------------------------------------------- */

export default function GoLive() {
  const { payload, apiState, error, usingBackend, snapshotState } = useConsoleSnapshot<GoLiveReadinessPayload>(
    s => fetchGoLiveReadiness(s));
  const p: GoLiveReadinessPayload = usingBackend ? payload! : mockPayload;
  const absent = p.snapshot_present === false || p.snapshot_status === 'NO_SNAPSHOT';
  const pageState = apiState === 'loading' ? 'loading' : absent ? 'empty' : 'ready';

  // binance_mainnet is the wizard's focus venue; other venues stay secondary.
  const venues = [...p.venues].sort((a, b) => Number(b.mainnet) - Number(a.mainnet));
  const focus = venues.find(v => v.venue === 'binance_mainnet') ?? venues[0] ?? null;
  const guidance = p.guidance;
  const crowd: GoLiveStrategy | null = p.strategies['crowd_contra'] ?? null;
  const covered = guidance
    ? guidance.required_instruments.filter(i => !guidance.missing_from_mandate.includes(i))
    : [];
  const mandateJson = guidance
    ? JSON.stringify({
        venue: focus?.mandate.expected_venue ?? 'mainnet',
        exchange: focus?.mandate.expected_exchange ?? 'binance',
        strategy: guidance.strategy,
        allowed_instruments: guidance.required_instruments,
        max_gross_notional: guidance.suggested_caps.max_gross_notional,
        max_single_order_notional: guidance.suggested_caps.max_single_order_notional,
        max_net_notional: guidance.suggested_caps.max_net_notional,
        per_instrument_notional: guidance.suggested_caps.per_instrument,
      }, null, 2)
    : '';

  const previewCmd = `python -m tools.live_track_advance --venue ${guidance?.venue ?? 'binance_mainnet'} --strategy ${guidance?.strategy ?? 'crowd_contra'} --arm-live`;
  const submitCmd = `${previewCmd} --submit`;

  /* Armed-preview drill (2026-07-24): the page's ONE action. Server-fixed argv
     never carries --submit; PreviewVenue structurally cannot place orders. */
  const [opsToken, setOpsToken] = useOpsToken();
  const [preview, setPreview] = useState<LiveOpsPreviewResponse | null>(null);
  const [previewErr, setPreviewErr] = useState<{ status: number | null; message: string } | null>(null);
  const drillVenue = guidance?.venue ?? 'binance_mainnet';
  const drillStrategy = guidance?.strategy ?? 'crowd_contra';
  const runDrill = useCallback(async () => {
    setPreview(null);
    setPreviewErr(null);
    try {
      setPreview(await postLiveOpsPreview(drillVenue, drillStrategy, opsToken));
    } catch (e) {
      setPreviewErr(e instanceof LiveOpsHttpError
        ? { status: e.status, message: e.body }
        : { status: null, message: String(e) });
    }
  }, [drillVenue, drillStrategy, opsToken]);

  const legCols: Column<GoLiveStrategyLeg>[] = [
    { key: 'instrument', header: bi('标的', 'Instrument'), render: l => <span className="text-ink">{l.instrument}</span> },
    { key: 'side', header: bi('方向', 'Side'), render: l => (
      <Chip label={l.weight >= 0 ? bi('多 LONG', 'LONG') : bi('空 SHORT', 'SHORT')} tone={l.weight >= 0 ? 'pos' : 'neg'} />) },
    { key: 'weight', header: bi('权重', 'Weight'), render: l => <span className="ticker-num text-muted">{(Math.abs(l.weight) * 100).toFixed(2)}%</span> },
    { key: 'price', header: bi('价格', 'Price'), render: l => <span className="ticker-num text-muted">{l.price != null ? num(l.price, 4) : '—'}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Go-Live Wizard" titleZh="上线向导"
        subtitle={bi('上线就绪清单 + mandate 指引 · 全部项绿也只是"具备提交人工评审的材料",绝不授予实盘能力;最后一步按键刻意仅人类可执行',
          'Go-live readiness checklist + mandate guidance · all-green only means "materials for human review exist" — it never grants live capability; the final keystroke is deliberately human-only')}
        actions={<AsOfStamp ts={p.generated_at_ms} />} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_GO_LIVE_READINESS'}
        source={usingBackend ? p.data_provenance ?? 'GO_LIVE_READINESS' : 'FRONTEND_GO_LIVE_MOCK'}
        error={error}
        dataStatus={snapshotState} />
      <StatusBanner />

      <LoadingVeil active={apiState === 'loading'}>
        <StateView state={pageState}
          empty={bi('就绪证据尚未产出 —— 先人工运行 python -m tools.go_live_readiness(全本地输入,无网络)。本页绝不伪造就绪行。',
            'Readiness evidence not produced yet — run python -m tools.go_live_readiness by hand (all-local inputs, no network). This page never fakes readiness rows.')}>
          {/* 1. 场地就绪卡片 */}
          <Panel eyebrow={bi('场地就绪', 'VENUE READINESS')}
            title={bi('逐场地检查(key 权限 / 只读 smoke / mandate / attestation / kill-switch / 最近运行)',
              'Per-venue checks (key permissions / read-only smoke / mandate / attestation / kill-switch / last run)')}>
            <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
              {venues.map(v => (
                <div key={v.venue}
                  className={`rounded-lg border px-3 py-2.5 ${v.venue === focus?.venue ? 'border-accent/50 bg-accent/5' : 'border-line bg-inset/30'}`}>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-ink">{v.venue}</span>
                    {v.mainnet
                      ? <Chip label="MAINNET" tone="warn" />
                      : <Chip label="TESTNET" tone="info" />}
                    {v.venue === focus?.venue && <Chip label={bi('焦点', 'FOCUS')} tone="accent" />}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <Chip label={`KEY ${v.key_permissions.status}`} tone={keyPermTone(v.key_permissions.status)} />
                    <Chip label={`SMOKE ${v.smoke.verdict}${v.smoke.age_ms != null ? ` · ${ageLabel(v.smoke.age_ms)}` : ''}`}
                      tone={smokeTone(v.smoke.verdict)} />
                    <MandateGateChip state={mandateStateOf(v.mandate)} expiresAt={v.mandate.expires_at} />
                    <Chip
                      label={v.attestations['crowd_contra']?.present
                        ? `ATTEST ✓ ${v.attestations['crowd_contra'].kind ?? ''}`.trim()
                        : 'ATTEST ✗ crowd_contra'}
                      tone={v.attestations['crowd_contra']?.present ? 'pos' : 'neg'} />
                    <Chip label={v.kill_switch_tripped ? 'KILL TRIPPED' : 'KILL CLEAR'}
                      tone={v.kill_switch_tripped ? 'neg' : 'pos'} />
                    <Chip label={v.last_run.present
                      ? `${bi('最近运行', 'last run')} ${ageLabel(v.last_run.at_ms != null ? Date.now() - v.last_run.at_ms : null)}`
                      : bi('无运行记录', 'no run yet')}
                      tone={v.last_run.present ? 'info' : 'muted'} />
                  </div>
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 text-3xs text-faint">
                    <span>{v.mandate.venue_domain_ok ? '✓' : '✗'} venue_domain</span>
                    <span>{v.mandate.exchange_domain_ok ? '✓' : '✗'} exchange_domain</span>
                    <span>{bi('授权标的', 'allowed')}: {v.mandate.allowed_instruments.length}</span>
                    {v.mandate.expires_at != null && <span>{bi('到期', 'expires')}: {stamp(v.mandate.expires_at)}</span>}
                  </div>
                  {v.venue === 'mexc' && <MexcChecksSection />}
                </div>
              ))}
            </div>
          </Panel>

          {/* 2. 策略信号面板(crowd_contra) */}
          <Panel eyebrow={bi('策略信号', 'STRATEGY SIGNAL')}
            title={bi('crowd_contra 目标持仓(离线截面信号,供人工对照 mandate)',
              'crowd_contra target legs (offline cross-sectional signal, for human cross-check against the mandate)')}
            right={crowd && <span className="text-2xs text-muted">
              {bi('决定日', 'decided')}: {crowd.decided_ts ? stamp(crowd.decided_ts) : '—'} · {crowd.n_legs} legs · gross {num(crowd.gross, 2)}
            </span>}>
            {crowd && crowd.legs.length > 0
              ? <DataTable rows={crowd.legs} columns={legCols} getKey={l => l.instrument} density="compact" />
              : <div className="py-3 text-2xs text-faint">{bi('无持仓腿(快照缺席或该策略未决定)', 'no legs (snapshot absent or strategy undecided)')}</div>}
          </Panel>

          {/* 3. Mandate 指引(向导核心) */}
          {guidance && (
            <Panel eyebrow={bi('MANDATE 指引', 'MANDATE GUIDANCE')}
              title={bi(`${guidance.strategy} × ${guidance.venue} —— 信号所需标的 vs 现有 mandate 授权`,
                `${guidance.strategy} × ${guidance.venue} — signal-required instruments vs current mandate grants`)}>
              <div className="flex flex-wrap gap-1.5">
                {covered.map(i => <Chip key={i} label={`✓ ${i}`} tone="pos" />)}
                {guidance.missing_from_mandate.map(i => <Chip key={i} label={`✗ ${i}`} tone="neg" />)}
                {guidance.stale_in_mandate.map(i => <Chip key={i} label={`◌ ${i} ${bi('陈旧', 'stale')}`} tone="warn" />)}
              </div>
              <div className="mt-1.5 text-3xs text-faint">
                {bi(`覆盖 ${covered.length}/${guidance.required_instruments.length} · 缺失 ${guidance.missing_from_mandate.length} · 陈旧 ${guidance.stale_in_mandate.length}`,
                  `covered ${covered.length}/${guidance.required_instruments.length} · missing ${guidance.missing_from_mandate.length} · stale ${guidance.stale_in_mandate.length}`)}
              </div>

              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <div className="rounded-lg border border-line bg-inset/30 px-3 py-2.5">
                  <div className="text-2xs font-semibold text-ink">{bi('建议限额(供真人重签 mandate 参考)', 'Suggested caps (reference for the human re-signing the mandate)')}</div>
                  <KeyValue cols={2} items={[
                    { k: bi('总名义上限', 'gross cap'), v: `${num(guidance.suggested_caps.max_gross_notional)} USDT` },
                    { k: bi('单笔上限', 'single order cap'), v: `${num(guidance.suggested_caps.max_single_order_notional)} USDT` },
                    { k: bi('净敞口上限', 'net cap'), v: `${num(guidance.suggested_caps.max_net_notional)} USDT` },
                    { k: bi('单标的上限', 'per-instrument'), v: `${num(guidance.suggested_caps.per_instrument)} USDT` },
                    { k: bi('全腿最低部署', 'min deploy all legs'), v: `${num(guidance.min_deploy_for_all_legs)} USDT` },
                    { k: bi('绑定腿', 'binding leg'), v: guidance.min_deploy_binding_leg },
                    { k: bi('部署目标', 'deploy target'), v: `${num(guidance.deploy_target_usdt)} USDT` },
                  ]} />
                </div>
                <div className="flex flex-col gap-2">
                  <CopyBlock code={mandateJson}
                    label={bi('建议 mandate 字段(只读片段)', 'Suggested mandate fields (read-only snippet)')} />
                  <div className="rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-2xs text-warn">
                    {bi('mandate 需真人在 key/ 目录签署,前端永不代填 —— 上方片段仅供人工对照,无任何编辑/提交能力。',
                      'The mandate must be signed by a human in key/ — the frontend never fills it in; the snippet above is for human reference only, with no editing/submission capability.')}
                  </div>
                </div>
              </div>

              {guidance.warnings.length > 0 && (
                <div className="mt-3 rounded-lg border border-line bg-inset/30 px-3 py-2">
                  <div className="text-2xs font-semibold text-warn">{bi('警告(原样转录)', 'Warnings (verbatim)')}</div>
                  <ul className="mt-1 list-disc pl-4 text-2xs text-muted">
                    {guidance.warnings.map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                </div>
              )}
            </Panel>
          )}

          {/* 4. 最后一步:演练(本页唯一动作)+ 人工 CLI(提交刻意仅人类) */}
          <Panel eyebrow={bi('最后一步', 'FINAL STEP')} tone="warn"
            title={bi('先跑演练(armed preview,结构性零提交),最终提交按键刻意仅人类可执行',
              'Run the drill first (armed preview, structurally zero submission) — the final submit keystroke is deliberately human-only')}>
            {/* 演练动作:POST {ops}/api/v1/liveops/preview —— 服务端固定 argv,绝不携带
                --submit;mainnet-only venue 自动 --arm-live = armed preview(真实主网账户
                只读透传,PreviewVenue 结构性无法下单)。 */}
            <div className="rounded-lg border border-line bg-inset/30 px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <ConfirmActionButton
                  label={bi('运行演练 (Preview)', 'Run Drill (Preview)')}
                  busyLabel={bi('演练中…(真实账户只读查询,约 10–60 秒)', 'drilling… (real account read-only reads, ~10–60s)')}
                  title={bi('确认运行 armed preview 演练', 'Confirm armed preview drill')}
                  message={bi(
                    `将对 ${drillVenue} 真实主网账户跑一次 armed preview 演练(${drillStrategy}):真实账户只读查询 + 完整 mandate/gate/资金费核对链评估,列出"本来会提交什么"。PreviewVenue 结构性无法下单 —— 这次调用绝不会发出任何订单;真提交仍只有下方人工 CLI 一条路。`,
                    `This runs one armed preview drill against the REAL ${drillVenue} mainnet account (${drillStrategy}): real account read-only reads + the full mandate/gate/funding-check chain, listing what it WOULD have submitted. PreviewVenue structurally cannot place orders — this call can never send any order; real submission stays human-CLI-only below.`)}
                  severity="warn"
                  confirmLabel={bi('跑演练(零提交)', 'Run drill (zero submission)')}
                  disabled={!guidance || !opsToken}
                  onConfirm={runDrill} />
                <span className="text-3xs text-faint">
                  {bi('固定 argv · 绝不 --submit · 单飞锁(409=有运行进行中)', 'server-fixed argv · never --submit · single-flight (409 = a run is in flight)')}
                </span>
              </div>

              {!opsToken && (
                <div className="mt-2 rounded border border-info/40 bg-info/[0.06] px-3 py-2 text-2xs">
                  <div className="mb-1.5 text-muted">
                    {bi('演练需要本机操作密钥 —— token 在 key/live_ops_token.txt(真人粘贴进来,与 Live Desk 的连接框是同一份 sessionStorage:只存本标签页会话,绝不打进前端构建产物)。',
                      'the drill needs the local ops token — it lives in key/live_ops_token.txt (a human pastes it here; same sessionStorage entry as the Live Desk connect box: this tab’s session only, never baked into the frontend build).')}
                  </div>
                  <form className="flex gap-2" onSubmit={e => {
                    e.preventDefault();
                    const input = (e.currentTarget.elements.namedItem('token') as HTMLInputElement);
                    setOpsToken(input.value.trim());
                  }}>
                    <label htmlFor="go-live-ops-token" className="sr-only">
                      {bi('操作密钥(key/live_ops_token.txt)', 'Ops token (key/live_ops_token.txt)')}
                    </label>
                    <input id="go-live-ops-token" name="token" type="password" autoComplete="off"
                      placeholder={bi('粘贴 key/live_ops_token.txt 的内容', 'paste key/live_ops_token.txt contents')}
                      className="flex-1 rounded border border-line bg-inset/60 px-2 py-1 text-2xs mono text-ink" />
                    <button type="submit" className="rounded border border-line px-2.5 py-1 text-2xs text-ink hover:border-ink/40">
                      {bi('保存', 'Save')}
                    </button>
                  </form>
                </div>
              )}

              {previewErr && (
                <div className="mt-2 rounded border border-neg/40 bg-neg/5 px-3 py-2 text-2xs">
                  {previewErr.status === 401 ? (
                    <span className="text-neg">
                      {bi('401 —— ops token 缺失或不对。token 在 key/live_ops_token.txt;在上方粘贴框(或 Live Desk 连接框)重新粘贴其内容后重试。',
                        '401 — ops token missing/invalid. It lives in key/live_ops_token.txt; re-paste its contents above (or in the Live Desk connect box) and retry.')}
                    </span>
                  ) : previewErr.status === 409 ? (
                    <span className="text-warn">
                      {bi('409 —— 另一个运行/演练正在进行(单飞锁)。等它完成后重试。',
                        '409 — another run/preview is in flight (single-flight lock). Retry after it finishes.')}
                    </span>
                  ) : (
                    <span className="text-neg">
                      {bi('演练请求失败', 'drill request failed')}{previewErr.status != null ? ` · HTTP ${previewErr.status}` : ''}: {previewErr.message}
                    </span>
                  )}
                </div>
              )}

              {preview && <PreviewResultBlock result={preview} />}
            </div>

            <div className="mt-3 flex flex-col gap-2">
              <CopyBlock code={previewCmd} label={bi('预览(preview,不下单)', 'preview (no orders)')} />
              <CopyBlock code={submitCmd} label={bi('提交(submit,真人确认后)', 'submit (after human confirmation)')} />
            </div>
            <div className="mt-2 text-2xs text-muted">
              {bi('按设计,前端唯一能触发的是上方演练(固定 argv,绝不携带 --submit;PreviewVenue 结构性零提交)—— --submit 永不出现在任何前端/ops API 路径,也永无任何凭证输入框;最终提交命令必须由真人在终端执行,mandate + attestation + kill-switch 全绿也只是"具备提交人工评审的材料"。',
                'By design the only thing the frontend can trigger is the drill above (server-fixed argv, never --submit; PreviewVenue structurally places zero orders) — --submit never appears in any frontend/ops-API path and there is never any credential input; the final submit command must be run by a human in a terminal. All-green mandate + attestation + kill-switch only means "materials for human review exist".')}
            </div>
          </Panel>
        </StateView>

        <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
          {p.honesty_note}
        </div>
        <EndpointFootnote path="/api/v1/console/go-live-readiness"
          note={bi('只读 · 离线 artifact 原样 serve · 绝不读 mandate 内容、绝不授予能力', 'read-only · offline artifact served verbatim · never reads mandate contents, never grants capability')} />
      </LoadingVeil>
    </PageShell>
  );
}
