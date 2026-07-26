import React, { useCallback, useEffect, useRef, useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';
import { PageShell, PageHeader, Panel, KpiStrip, StatTile, DataTable, Chip, type Column } from '@/ui/primitives';
import { money, fixed, stamp } from '@/ui/format';
import { EquityCurveChart } from '@/ui/workbench';
import { useConfirm, usePrompt } from '@/ui/dialog';
import { useToast } from '@/ui/toast';
import {
  LiveOpsHttpError, OPS_URL, postLiveOpsPreview, useOpsToken, venueDefaultStrategy,
} from '@/lib/api/live-ops-api';

/**
 * Live Desk 交易台 — 类交易所界面:双所实时仓位/权益/uPnL + 订单成交流水 + TCA + 受控操作。
 * 数据源 = tools/live_ops_api.py(:8440,tools 层实时查两所 REST;"实时"= 轮询粒度,默认 8s)。
 * 操作面治理(2026-07-24 收紧):页面唯一动作 = POST /preview -> 驱动器 CLI 子进程
 * (服务端固定 argv,绝不携带 --submit;mainnet-only venue 自动 --arm-live = armed preview,
 * 真实账户只读透传,PreviewVenue 结构性无法下单)——与人工命令行同一条 mandate → gate 链,
 * 没有旁路;真提交仅人工 CLI,本页永不提供 Submit。
 * ops token 机制(X-Ops-Token / sessionStorage,绝不打进构建产物)见 lib/api/live-ops-api.ts。
 */

const POLL_MS = 8000;

interface PositionRow {
  instrument_id: string; qty: number; entry_price: number; mark_price: number;
  upnl: number; notional: number;
  // 审计 §4.4 [P1-R3]:交易所已算好的真实强平价(Bybit V5 position/list 的 liqPrice),
  // 纯只读透传展示——不驱动任何判断/告警。Binance 侧未接(需要额外一次 API 调用,不在本次
  // 范围内),字段缺失时前端如实显示"—"。
  liquidation_price?: number | null;
}
interface OrderRow {
  client_order_id: string; instrument_id: string; side: string; quantity: number;
  submitted_at: number; status: string;
}
interface FillRow {
  fill_id: string; instrument_id: string; side: string; quantity: number;
  price: number; filled_at: number; client_order_id: string;
}
interface EquityPoint { ts: number; equity: number; gross_notional?: number; upnl_total?: number; }
interface BalanceRow { asset: string; free: number; locked?: number }
interface VenuePanel {
  venue: string; configured: boolean; ok?: boolean; error?: string;
  /** 'spot' = cash venue (MEXC): no liq price / leverage fields (honestly absent, not zero). */
  kind?: 'futures' | 'spot';
  balances?: BalanceRow[];
  equity?: number; available?: number; positions?: PositionRow[];
  gross_notional?: number; net_notional?: number; upnl_total?: number;
  equity_curve?: EquityPoint[];
  recent_orders?: OrderRow[]; recent_fills?: FillRow[];
  tca?: { n_with_slippage: number; notional_weighted_slippage_bps: number | null;
          assumed_cost_bps: number; weighted_minus_assumed_bps: number | null } | null;
  mandate?: { mandate_id: string; exchange: string; max_gross_notional: number;
              max_single_order_notional: number; max_net_notional: number;
              expires_at: number | null } | null;
  last_run?: { ran_at?: number; mode?: string; tier?: string; hard_stop?: boolean;
               n_submitted?: number; n_pending_review?: number; n_submission_errors?: number;
               deploy_aum?: number; alerts?: string[] } | null;
}
interface DeskPayload {
  source: string; generated_at: number; note: string;
  venues: Record<string, VenuePanel>;
  run_state: { running: boolean; venue: string | null; submit: boolean | null;
               started_at: number | null; finished_at: number | null;
               returncode: number | null; tail: string };
}

const VENUE_ZH: Record<string, string> = {
  binance: 'Binance 测试网', bybit: 'Bybit 测试网(弃用)', bybit_demo: 'Bybit Demo',
  mexc: 'MEXC 现货(mainnet · 人工门)',
};

function VenueEquityCurve({ points }: { points: EquityPoint[] }) {
  // 风险哨兵心跳积累的真实权益历史 —— 共享 EquityCurveChart(workbench kit,抽取自三处手写实现)
  if (points.length < 2) {
    return <span className="text-2xs text-faint">
      {bi('权益点不足(风险哨兵每小时心跳积累)', 'not enough equity points (sentinel accrues hourly)')}
    </span>;
  }
  const up = points[points.length - 1].equity >= points[0].equity;
  return (
    <EquityCurveChart height={110} xFormat={v => stamp(v)}
      series={[{ label: bi('权益', 'equity'), points: points.map(p => ({ t: p.ts, equity: p.equity })), tone: up ? 'pos' : 'neg' }]} />
  );
}

interface PendingReviewRow {
  id: number; venue: string; decided_ts: number; client_order_id: string;
  instrument_id: string; side: string; quantity: number; reasons: string; recorded_at: number;
}

export default function LiveDesk() {
  const [data, setData] = useState<DeskPayload | null>(null);
  const [pending, setPending] = useState<PendingReviewRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [opsToken, setOpsToken] = useOpsToken();
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const opsHeaders: Record<string, string> = opsToken ? { 'X-Ops-Token': opsToken } : {};
  const confirm = useConfirm();
  const prompt = usePrompt();
  const toast = useToast();

  const poll = useCallback(async () => {
    if (!opsToken) { setErr('need-token'); return; }
    try {
      const r = await fetch(`${OPS_URL}/api/v1/liveops/dashboard`, { headers: opsHeaders });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
    try {
      const r2 = await fetch(`${OPS_URL}/api/v1/liveops/pending-review`, { headers: opsHeaders });
      if (r2.ok) setPending((await r2.json()).pending ?? []);
    } catch { /* dashboard error already surfaced above */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opsToken]);

  useEffect(() => {
    poll();
    timer.current = setInterval(poll, POLL_MS);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [poll]);

  const decideReview = useCallback(async (row: PendingReviewRow, status: 'APPROVED' | 'REJECTED') => {
    const reviewer = await prompt({
      title: `${status === 'APPROVED' ? bi('批准', 'Approve') : bi('拒绝', 'Reject')} ${row.instrument_id} ${row.side} ${row.quantity}`,
      message: bi('注意:这只记录人工决定,不会自动重新提交订单。', 'Note: this only records the human decision — it does NOT auto-resubmit the order.'),
      label: bi('你的名字(留痕)', 'Your name (for the audit trail)'),
      severity: status === 'APPROVED' ? 'info' : 'warn',
      confirmLabel: status === 'APPROVED' ? bi('批准', 'Approve') : bi('拒绝', 'Reject'),
    });
    if (!reviewer) return;
    try {
      const r = await fetch(`${OPS_URL}/api/v1/liveops/pending-review/${row.id}/decide`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...opsHeaders },
        body: JSON.stringify({ status, reviewer }),
      });
      if (r.ok) toast.success(`${row.instrument_id} ${status} by ${reviewer}`);
      else toast.error(`HTTP ${r.status}`);
    } catch (e) {
      toast.error(String(e));
    } finally {
      poll();
    }
  }, [poll, prompt, toast]);

  const runPreview = useCallback(async (venue: string) => {
    if (!await confirm({
      title: bi('跑一轮 Preview', 'Run preview'),
      message: bi(`对 ${VENUE_ZH[venue] ?? venue} 跑一轮 armed preview 演练(真实账户只读 + 完整 mandate/gate 链,PreviewVenue 结构性零提交,约 10–60 秒)。`,
        `Run one armed-preview drill against ${VENUE_ZH[venue] ?? venue} (real account read-only + full mandate/gate chain; PreviewVenue structurally places zero orders; ~10–60s).`),
      severity: 'info', confirmLabel: bi('跑 Preview', 'Run Preview'),
    })) {
      return;
    }
    setBusy(true);
    try {
      const j = await postLiveOpsPreview(venue, venueDefaultStrategy(venue), opsToken);
      if (j.ok) {
        const n = j.status?.would_submit?.length;
        toast.success(`${venue} preview ${bi('完成', 'done')} · ${j.status?.batch_skipped
          ? `skipped: ${j.status.batch_skipped}`
          : bi(`本会提交 ${n ?? '—'} 单`, `would submit ${n ?? '—'}`)}`);
      } else {
        toast.error(`${venue} preview rc=${j.returncode ?? '—'}`);
      }
    } catch (e) {
      if (e instanceof LiveOpsHttpError && e.status === 409) {
        toast.error(bi('409 —— 另一个运行/演练正在进行(单飞锁)', '409 — another run/preview is in flight (single-flight)'));
      } else if (e instanceof LiveOpsHttpError && e.status === 401) {
        toast.error(bi('401 —— token 不对,重新粘贴 key/live_ops_token.txt 的内容', '401 — wrong token, re-paste key/live_ops_token.txt'));
      } else {
        toast.error(String(e));
      }
    } finally {
      setBusy(false);
      setTimeout(poll, 1500);
    }
  }, [poll, confirm, toast, opsToken]);

  const claimDemoFunds = useCallback(async () => {
    if (!await confirm({
      title: bi('申领模拟资金', 'Claim demo funds'),
      message: bi('向 Bybit Demo 申领 5000 模拟 USDT(demo 专用端点,零真实资产)。', 'Claim 5000 simulated USDT from Bybit Demo (demo-only endpoint, zero real assets).'),
      severity: 'info', confirmLabel: bi('申领', 'Claim'),
    })) return;
    setBusy(true);
    try {
      const r = await fetch(`${OPS_URL}/api/v1/liveops/demo-funds`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...opsHeaders },
        body: JSON.stringify({ amount: 5000 }),
      });
      const j = await r.json();
      if (j.returncode === 0) toast.success(`demo-funds rc=${j.returncode}`);
      else toast.error(`demo-funds rc=${j.returncode}`);
    } finally {
      setBusy(false);
      setTimeout(poll, 1500);
    }
  }, [poll, confirm, toast]);

  const posCols: Column<PositionRow>[] = [
    { key: 'inst', header: bi('标的', 'Instrument'), render: p => <span className="mono text-ink">{p.instrument_id}</span> },
    { key: 'qty', header: bi('数量', 'Qty'), align: 'right', mono: true,
      render: p => <span className={p.qty >= 0 ? 'text-pos' : 'text-neg'}>{fixed(p.qty, 4)}</span> },
    { key: 'entry', header: bi('开仓均价', 'Entry'), align: 'right', mono: true, render: p => fixed(p.entry_price, 4) },
    { key: 'mark', header: bi('标记价', 'Mark'), align: 'right', mono: true, render: p => fixed(p.mark_price, 4) },
    { key: 'liq', header: bi('强平价', 'Liq'), align: 'right', mono: true,
      render: p => p.liquidation_price != null ? fixed(p.liquidation_price, 4) : '—' },
    { key: 'notional', header: bi('名义额', 'Notional'), align: 'right', mono: true,
      render: p => money(Math.abs(p.notional), 'USD') },
    { key: 'upnl', header: 'uPnL', align: 'right', mono: true,
      render: p => <span className={p.upnl >= 0 ? 'text-pos' : 'text-neg'}>{p.upnl >= 0 ? '+' : ''}{fixed(p.upnl, 2)}</span> },
  ];

  const orderCols: Column<OrderRow>[] = [
    { key: 't', header: bi('时间', 'Time'), mono: true, render: o => stamp(o.submitted_at) },
    { key: 'inst', header: bi('标的', 'Inst'), render: o => <span className="mono">{o.instrument_id}</span> },
    { key: 'side', header: bi('方向', 'Side'), render: o => <Chip label={o.side} tone={o.side === 'BUY' ? 'pos' : 'neg'} /> },
    { key: 'qty', header: bi('数量', 'Qty'), align: 'right', mono: true, render: o => fixed(o.quantity, 4) },
    { key: 'status', header: bi('状态', 'Status'),
      render: o => <Chip label={o.status} tone={o.status === 'FILLED' ? 'pos' : o.status === 'CANCELED' ? 'warn' : o.status === 'REJECTED' ? 'neg' : 'muted'} /> },
  ];

  const fillCols: Column<FillRow>[] = [
    { key: 't', header: bi('时间', 'Time'), mono: true, render: f => stamp(f.filled_at) },
    { key: 'inst', header: bi('标的', 'Inst'), render: f => <span className="mono">{f.instrument_id}</span> },
    { key: 'side', header: bi('方向', 'Side'), render: f => <Chip label={f.side} tone={f.side === 'BUY' ? 'pos' : 'neg'} /> },
    { key: 'qty', header: bi('数量', 'Qty'), align: 'right', mono: true, render: f => fixed(f.quantity, 4) },
    { key: 'px', header: bi('价格', 'Price'), align: 'right', mono: true, render: f => fixed(f.price, 4) },
  ];

  const runState = data?.run_state;
  const venues = Object.values(data?.venues ?? {}).filter(v => v.configured && v.venue !== 'bybit');
  const venueUnavailableReason = (venue: VenuePanel): string | null => {
    if (err || !data) return bi('操作快照不可用', 'operations snapshot unavailable');
    if (venue.ok !== true) return bi('交易所状态不可用', 'venue state unavailable');
    return null;
  };
  // 治理提示(2026-07-24):此前是 submit 的阻断理由;submit UI 已随 ops API 治理收紧移除
  // (真提交仅人工 CLI)。这些理由仍值得**展示**(mandate 未签/过期、上轮 HARD STOP)——但不再
  // 阻断 preview:演练正是诊断这些问题的工具,驱动器自身 fail-closed。
  const venueGovernanceNote = (venue: VenuePanel): string | null => {
    if (err || !data) return null;
    if (venue.ok !== true) return null;
    if (venue.last_run?.hard_stop === true) return bi('上轮触发 HARD STOP', 'last run triggered HARD STOP');
    if (!venue.mandate) return bi('mandate 未签署', 'mandate is unsigned');
    if (venue.mandate.expires_at != null && venue.mandate.expires_at <= data.generated_at) {
      return bi('mandate 已过期', 'mandate has expired');
    }
    return null;
  };

  return (
    <PageShell>
      <PageHeader title="Live Desk" titleZh="交易台"
        subtitle={bi(`双所实时仓位/权益/流水 · ${POLL_MS / 1000}s 轮询 · 操作只经驱动器 CLI 同一条治理链`,
          `Dual-venue realtime positions/equity/flow · ${POLL_MS / 1000}s polling · actions run through the driver CLI's governance chain`)} />

      <div className="flex items-center gap-3">
        <Chip label={err ? bi('ops API 不可达', 'ops API unreachable') : bi('实时', 'LIVE')} tone={err ? 'neg' : 'pos'} />
        {data && <span className="text-2xs text-muted">{bi('更新于', 'updated')} {stamp(data.generated_at)}</span>}
        {err && err !== 'need-token' && <span className="text-2xs text-neg mono">
          {err} · {err.includes('401')
            ? bi('token 不对——重新粘贴 key/live_ops_token.txt 的内容', 'wrong token — re-paste key/live_ops_token.txt')
            : `${bi('启动:', 'start:')} python -m uvicorn tools.live_ops_api:app --port 8440`}
        </span>}
      </div>

      {(err === 'need-token' || !opsToken) && (
        <div className="rounded-lg border border-info/40 bg-info/[0.06] px-3 py-2.5 text-2xs">
          <div className="mb-1.5 text-muted">
            {bi('需要本机操作密钥——只存在本标签页会话里(sessionStorage),绝不打进前端构建产物',
              'ops token required — kept only in this tab’s session (sessionStorage), never baked into the build')}
          </div>
          <form className="flex gap-2" onSubmit={e => {
            e.preventDefault();
            const input = (e.currentTarget.elements.namedItem('token') as HTMLInputElement);
            setOpsToken(input.value.trim());
          }}>
            <label htmlFor="live-desk-ops-token" className="sr-only">
              {bi('操作密钥(key/live_ops_token.txt)', 'Ops token (key/live_ops_token.txt)')}
            </label>
            <input id="live-desk-ops-token" name="token" type="password" autoComplete="off"
              placeholder={bi('粘贴 key/live_ops_token.txt 的内容', 'paste key/live_ops_token.txt contents')}
              className="flex-1 rounded border border-line bg-inset/60 px-2 py-1 text-2xs mono text-ink" />
            <button type="submit" className="rounded border border-line px-2.5 py-1 text-2xs text-ink hover:border-ink/40">
              {bi('连接', 'Connect')}
            </button>
          </form>
        </div>
      )}

      {runState?.running && (
        <div className="rounded border border-info/40 bg-info/[0.06] px-3 py-2 text-2xs text-info">
          ⏳ {bi(`驱动器运行中:${runState.venue} preview(单飞锁,完成前不能再触发)`,
            `driver running: ${runState.venue} preview (single-flight)`)}
        </div>
      )}
      {runState && !runState.running && runState.finished_at != null && (
        <div className={`rounded border px-3 py-2 text-2xs ${runState.returncode === 0 ? 'border-line text-muted' : 'border-neg/40 text-neg'}`}>
          {bi('上次操作', 'last action')}: {runState.venue} preview · rc={String(runState.returncode)}
          {runState.tail && <span className="mono"> · {runState.tail.slice(-160)}</span>}
        </div>
      )}

      {pending.length > 0 && (
        <Panel eyebrow={bi('人工审查', 'HUMAN REVIEW')}
          title={bi(`待人工审查(${pending.length})`, `Pending Human Review (${pending.length})`)}
          right={<Chip label={bi('批准/拒绝仅记录,不自动重提交', 'decision recorded only, no auto-resubmit')} tone="warn" />}>
          <DataTable rows={pending} getKey={r => r.id} columns={[
            { key: 'venue', header: bi('交易所', 'Venue'), render: r => <span className="mono">{r.venue}</span> },
            { key: 'inst', header: bi('标的', 'Inst'), render: r => <span className="mono text-ink">{r.instrument_id}</span> },
            { key: 'side', header: bi('方向', 'Side'), render: r => <Chip label={r.side} tone={r.side === 'BUY' ? 'pos' : 'neg'} /> },
            { key: 'qty', header: bi('数量', 'Qty'), align: 'right', mono: true, render: r => fixed(r.quantity, 6) },
            { key: 'reasons', header: bi('原因', 'Reasons'), render: r => <span className="text-2xs text-warn">{r.reasons}</span> },
            { key: 't', header: bi('记录于', 'Recorded'), mono: true, render: r => stamp(r.recorded_at) },
            { key: 'actions', header: bi('操作', 'Actions'), render: r => (
                <div className="flex gap-1.5">
                  <button className="rounded border border-pos/50 px-2 py-0.5 text-2xs text-pos hover:bg-pos/10"
                    onClick={() => decideReview(r, 'APPROVED')}>{bi('批准', 'Approve')}</button>
                  <button className="rounded border border-neg/50 px-2 py-0.5 text-2xs text-neg hover:bg-neg/10"
                    onClick={() => decideReview(r, 'REJECTED')}>{bi('拒绝', 'Reject')}</button>
                </div>
              ) },
          ]} />
        </Panel>
      )}

      {venues.map(v => {
        const unavailableReason = venueUnavailableReason(v);
        const govNote = venueGovernanceNote(v);
        const commonBlocked = busy || Boolean(runState?.running);
        const previewBlocked = commonBlocked || unavailableReason !== null;
        const demoFundsBlocked = commonBlocked || unavailableReason !== null;
        // 现货场地(MEXC):余额账户、无强平价/杠杆字段(诚实省略,非零);
        // 真提交一律人工 CLI(--submit --arm-live)专属动作,本页只提供 armed-preview 演练。
        const isSpot = v.kind === 'spot' || Array.isArray(v.balances);
        const balCols: Column<BalanceRow>[] = [
          { key: 'asset', header: bi('资产', 'Asset'), render: b => <span className="mono text-ink">{b.asset}</span> },
          { key: 'free', header: bi('可用', 'Free'), align: 'right', mono: true, render: b => fixed(b.free, 8) },
          { key: 'locked', header: bi('冻结', 'Locked'), align: 'right', mono: true, render: b => fixed(b.locked ?? 0, 8) },
        ];
        const visiblePosCols = isSpot ? posCols.filter(c => c.key !== 'liq') : posCols;
        return (
        <Panel key={v.venue} eyebrow={v.venue.toUpperCase()} title={VENUE_ZH[v.venue] ?? v.venue}
          right={
            <div className="flex items-center gap-2">
              {govNote && <span className="text-2xs text-warn">{govNote}</span>}
              <button className="rounded border border-line px-2.5 py-1 text-2xs text-muted hover:text-ink hover:border-ink/40"
                disabled={previewBlocked} onClick={() => runPreview(v.venue)}>
                {bi('运行演练 Preview', 'Run Preview')}
              </button>
              {isSpot && (
                <span className="text-2xs text-warn">
                  {bi('现货 mainnet:真提交仅人工 CLI(--arm-live),本页永不提供 Submit', 'spot mainnet: real submission is human CLI (--arm-live) only — never a Submit here')}
                </span>
              )}
              {v.venue === 'bybit_demo' && (
                <button className="rounded border border-line px-2.5 py-1 text-2xs text-muted hover:text-ink"
                  disabled={demoFundsBlocked} onClick={claimDemoFunds}>
                  {bi('+5000 模拟金', '+5000 demo')}
                </button>
              )}
            </div>
          }>
          {!v.ok ? (
            <div className="py-4 text-center text-2xs text-neg">{v.error ?? bi('查询失败', 'query failed')}</div>
          ) : (
            <div className="flex flex-col gap-4">
              <KpiStrip cols={6}>
                <StatTile label={bi('权益', 'Equity')} value={money(v.equity ?? 0, 'USD')} />
                <StatTile label={bi('可用', 'Available')} value={money(v.available ?? 0, 'USD')} tone="muted" />
                {!isSpot && (
                  <>
                    <StatTile label={bi('总敞口', 'Gross')} value={money(v.gross_notional ?? 0, 'USD')} />
                    <StatTile label={bi('净敞口', 'Net')} value={money(v.net_notional ?? 0, 'USD')} tone="muted" />
                    <StatTile label="uPnL"
                      value={<span className={(v.upnl_total ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>
                        {(v.upnl_total ?? 0) >= 0 ? '+' : ''}{fixed(v.upnl_total ?? 0, 2)}</span>} />
                  </>
                )}
                {isSpot && (
                  <StatTile label={bi('余额币种', 'Spot assets')} value={String(v.balances?.length ?? 0)} tone="muted"
                    sub={bi('现货:无强平价/杠杆(不适用)', 'spot: no liq/leverage (n/a)')} />
                )}
                <StatTile label={bi('授权额度', 'Mandate')}
                  value={v.mandate ? money(v.mandate.max_gross_notional, 'USD') : bi('未签署', 'unsigned')}
                  tone={v.mandate ? 'pos' : 'warn'} />
              </KpiStrip>

              {isSpot && (
                <div>
                  <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                    {bi(`现货余额(${v.balances?.length ?? 0})`, `Spot balances (${v.balances?.length ?? 0})`)}
                  </div>
                  <DataTable rows={v.balances ?? []} columns={balCols}
                    getKey={b => b.asset} empty={bi('无余额', 'no balances')} density="compact" />
                </div>
              )}

              <div>
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi('净值曲线(哨兵心跳)', 'Equity curve (sentinel heartbeats)')}
                </div>
                <VenueEquityCurve points={v.equity_curve ?? []} />
              </div>

              <div>
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi(`持仓(${v.positions?.length ?? 0})`, `Positions (${v.positions?.length ?? 0})`)}
                </div>
                <DataTable rows={v.positions ?? []} columns={visiblePosCols}
                  getKey={p => p.instrument_id} empty={bi('无持仓', 'flat')} />
              </div>

              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <div>
                  <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                    {bi('最近订单', 'Recent orders')}
                  </div>
                  <DataTable rows={(v.recent_orders ?? []).slice(0, 10)} columns={orderCols}
                    getKey={(o, i) => `${o.client_order_id}-${i}`} empty={bi('暂无', 'none')} />
                </div>
                <div>
                  <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                    {bi('最近成交', 'Recent fills')}
                  </div>
                  <DataTable rows={(v.recent_fills ?? []).slice(0, 10)} columns={fillCols}
                    getKey={(f, i) => `${f.fill_id}-${i}`} empty={bi('暂无', 'none')} />
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-3 text-2xs text-muted">
                {v.tca && v.tca.n_with_slippage > 0 && (
                  <span className="mono">
                    TCA: {v.tca.notional_weighted_slippage_bps != null ? fixed(v.tca.notional_weighted_slippage_bps, 1) : '—'}bps
                    (vs {fixed(v.tca.assumed_cost_bps, 0)}bps {bi('假设', 'assumed')})
                  </span>
                )}
                {v.last_run && (
                  <span>
                    {bi('上轮', 'last run')}: {v.last_run.mode} tier={v.last_run.tier}{' '}
                    {bi('提交', 'submitted')} {v.last_run.n_submitted ?? 0}
                    {(v.last_run.n_submission_errors ?? 0) > 0 &&
                      <span className="text-warn"> · {v.last_run.n_submission_errors} {bi('错误', 'errors')}</span>}
                    {' '}· {stamp(v.last_run.ran_at)}
                  </span>
                )}
                {v.mandate?.expires_at && (
                  <span>{bi('mandate 到期', 'mandate expires')}: {stamp(v.mandate.expires_at)}</span>
                )}
              </div>
            </div>
          )}
        </Panel>
        );
      })}

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('全部为测试环境(测试网/Demo,零真实资金)· 页面唯一动作 = armed-preview 演练(驱动器 CLI 子进程,固定 argv 绝不携带 --submit,mandate fail-closed 到底;真提交仅人工 CLI)· '
          + '实时 = REST 轮询粒度,周度策略刻意不做 websocket',
          'All simulated environments (testnet/demo, zero real capital) · the only page action is an armed-preview drill (driver CLI subprocess, fixed argv never carries --submit, mandate fail-closed; real submission is human-CLI-only) · '
          + 'realtime = REST polling granularity; deliberately no websocket for a weekly strategy')}
      </div>
    </PageShell>
  );
}
