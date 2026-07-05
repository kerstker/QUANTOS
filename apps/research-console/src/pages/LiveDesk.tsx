import React, { useCallback, useEffect, useRef, useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';
import { PageShell, PageHeader, Panel, KpiStrip, StatTile, DataTable, Chip, type Column } from '@/ui/primitives';
import { money, fixed, stamp } from '@/ui/format';

/**
 * Live Desk 交易台 — 类交易所界面:双所实时仓位/权益/uPnL + 订单成交流水 + TCA + 受控操作。
 * 数据源 = tools/live_ops_api.py(:8440,tools 层实时查两所 REST;"实时"= 轮询粒度,默认 8s)。
 * 操作面治理:所有下单动作只经 POST /run -> 驱动器 CLI 子进程 —— 与人工命令行同一条
 * mandate → gate → 确认 → 记账链,没有旁路;submit 按钮双重确认。
 */

const OPS_URL: string = (import.meta as any).env?.VITE_OPS_URL || 'http://127.0.0.1:8440';
// 本机共享密钥认证(2026-07-05):ops API 的全部端点现在要求 X-Ops-Token 匹配
// key/live_ops_token.txt(首次启动自动生成)。开发时把该文件内容填进 .env 的
// VITE_OPS_TOKEN——前端不生成/不猜这个值,只是把你自己已经能读到的本机密钥转发过去。
const OPS_TOKEN: string = (import.meta as any).env?.VITE_OPS_TOKEN || '';
const OPS_HEADERS: Record<string, string> = OPS_TOKEN ? { 'X-Ops-Token': OPS_TOKEN } : {};
const POLL_MS = 8000;

interface PositionRow {
  instrument_id: string; qty: number; entry_price: number; mark_price: number;
  upnl: number; notional: number;
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
interface VenuePanel {
  venue: string; configured: boolean; ok?: boolean; error?: string;
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
};

function EquityCurve({ points }: { points: EquityPoint[] }) {
  // 纯 SVG 净值曲线(风险哨兵心跳积累的真实权益历史)——零图表库依赖
  if (points.length < 2) {
    return <span className="text-2xs text-faint">
      {bi('权益点不足(风险哨兵每小时心跳积累)', 'not enough equity points (sentinel accrues hourly)')}
    </span>;
  }
  const W = 560, H = 96, PAD = 4;
  const eq = points.map(p => p.equity);
  const lo = Math.min(...eq), hi = Math.max(...eq);
  const span = hi - lo || 1;
  const x = (i: number) => PAD + (i / (points.length - 1)) * (W - 2 * PAD);
  const y = (v: number) => H - PAD - ((v - lo) / span) * (H - 2 * PAD);
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(' ');
  const up = eq[eq.length - 1] >= eq[0];
  return (
    <div className="overflow-x-auto">
      <svg width={W} height={H} className="block">
        <path d={d} fill="none" strokeWidth={1.5}
          className={up ? 'stroke-emerald-400' : 'stroke-rose-400'} />
      </svg>
      <div className="mt-0.5 flex justify-between text-2xs text-faint mono">
        <span>{stamp(points[0].ts)}</span>
        <span>{fixed(lo, 2)} – {fixed(hi, 2)}</span>
        <span>{stamp(points[points.length - 1].ts)}</span>
      </div>
    </div>
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
  const [lastAction, setLastAction] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const poll = useCallback(async () => {
    try {
      const r = await fetch(`${OPS_URL}/api/v1/liveops/dashboard`, { headers: OPS_HEADERS });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
    try {
      const r2 = await fetch(`${OPS_URL}/api/v1/liveops/pending-review`, { headers: OPS_HEADERS });
      if (r2.ok) setPending((await r2.json()).pending ?? []);
    } catch { /* dashboard error already surfaced above */ }
  }, []);

  useEffect(() => {
    poll();
    timer.current = setInterval(poll, POLL_MS);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [poll]);

  const decideReview = useCallback(async (row: PendingReviewRow, status: 'APPROVED' | 'REJECTED') => {
    const reviewer = window.prompt(
      `${status === 'APPROVED' ? '批准' : '拒绝'} ${row.instrument_id} ${row.side} ${row.quantity}`
      + `\n注意:这只记录人工决定,不会自动重新提交订单。你的名字(留痕):`);
    if (!reviewer || !reviewer.trim()) return;
    try {
      const r = await fetch(`${OPS_URL}/api/v1/liveops/pending-review/${row.id}/decide`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...OPS_HEADERS },
        body: JSON.stringify({ status, reviewer: reviewer.trim() }),
      });
      setLastAction(r.ok ? `已记录:${row.instrument_id} ${status} by ${reviewer.trim()}` : `失败:HTTP ${r.status}`);
    } catch (e) {
      setLastAction(`失败:${String(e)}`);
    } finally {
      poll();
    }
  }, [poll]);

  const runDriver = useCallback(async (venue: string, submit: boolean) => {
    if (submit) {
      const expect = `submit ${venue}`;
      const typed = window.prompt(
        `真实提交确认:将以驱动器 CLI 同一条 mandate/gate 链向 ${VENUE_ZH[venue] ?? venue} 提交真实订单`
        + `(测试环境,零真实资金)。\n输入 "${expect}" 确认:`);
      if (typed !== expect) return;
    } else if (!window.confirm(`跑一轮 ${VENUE_ZH[venue] ?? venue} preview(只读评估,零提交)?`)) {
      return;
    }
    setBusy(true);
    try {
      const r = await fetch(`${OPS_URL}/api/v1/liveops/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...OPS_HEADERS },
        body: JSON.stringify({ venue, submit, refresh: false }),
      });
      const j = await r.json();
      setLastAction(r.ok ? `已启动:${venue} ${submit ? 'SUBMIT' : 'preview'}` : `失败:${JSON.stringify(j)}`);
    } catch (e) {
      setLastAction(`失败:${String(e)}`);
    } finally {
      setBusy(false);
      setTimeout(poll, 1500);
    }
  }, [poll]);

  const claimDemoFunds = useCallback(async () => {
    if (!window.confirm('向 Bybit Demo 申领 5000 模拟 USDT(demo 专用端点,零真实资产)?')) return;
    setBusy(true);
    try {
      const r = await fetch(`${OPS_URL}/api/v1/liveops/demo-funds`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...OPS_HEADERS },
        body: JSON.stringify({ amount: 5000 }),
      });
      const j = await r.json();
      setLastAction(`demo-funds rc=${j.returncode}`);
    } finally {
      setBusy(false);
      setTimeout(poll, 1500);
    }
  }, [poll]);

  const posCols: Column<PositionRow>[] = [
    { key: 'inst', header: bi('标的', 'Instrument'), render: p => <span className="mono text-ink">{p.instrument_id}</span> },
    { key: 'qty', header: bi('数量', 'Qty'), align: 'right', mono: true,
      render: p => <span className={p.qty >= 0 ? 'text-pos' : 'text-neg'}>{fixed(p.qty, 4)}</span> },
    { key: 'entry', header: bi('开仓均价', 'Entry'), align: 'right', mono: true, render: p => fixed(p.entry_price, 4) },
    { key: 'mark', header: bi('标记价', 'Mark'), align: 'right', mono: true, render: p => fixed(p.mark_price, 4) },
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

  return (
    <PageShell>
      <PageHeader title="Live Desk" titleZh="交易台"
        subtitle={bi(`双所实时仓位/权益/流水 · ${POLL_MS / 1000}s 轮询 · 操作只经驱动器 CLI 同一条治理链`,
          `Dual-venue realtime positions/equity/flow · ${POLL_MS / 1000}s polling · actions run through the driver CLI's governance chain`)} />

      <div className="flex items-center gap-3">
        <Chip label={err ? bi('ops API 不可达', 'ops API unreachable') : bi('实时', 'LIVE')} tone={err ? 'neg' : 'pos'} />
        {data && <span className="text-2xs text-muted">{bi('更新于', 'updated')} {stamp(data.generated_at)}</span>}
        {err && <span className="text-2xs text-neg mono">
          {err} · {err.includes('401')
            ? bi('认证失败:把 key/live_ops_token.txt 的内容填进 .env 的 VITE_OPS_TOKEN',
                 'auth failed: put key/live_ops_token.txt’s content into .env as VITE_OPS_TOKEN')
            : `${bi('启动:', 'start:')} python -m uvicorn tools.live_ops_api:app --port 8440`}
        </span>}
        {lastAction && <span className="text-2xs text-info">{lastAction}</span>}
      </div>

      {runState?.running && (
        <div className="rounded border border-info/40 bg-info/[0.06] px-3 py-2 text-2xs text-info">
          ⏳ {bi(`驱动器运行中:${runState.venue} ${runState.submit ? 'SUBMIT' : 'preview'}(单飞锁,完成前不能再触发)`,
            `driver running: ${runState.venue} ${runState.submit ? 'SUBMIT' : 'preview'} (single-flight)`)}
        </div>
      )}
      {runState && !runState.running && runState.finished_at != null && (
        <div className={`rounded border px-3 py-2 text-2xs ${runState.returncode === 0 ? 'border-line text-muted' : 'border-neg/40 text-neg'}`}>
          {bi('上次操作', 'last action')}: {runState.venue} {runState.submit ? 'SUBMIT' : 'preview'} · rc={String(runState.returncode)}
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

      {venues.map(v => (
        <Panel key={v.venue} eyebrow={v.venue.toUpperCase()} title={VENUE_ZH[v.venue] ?? v.venue}
          right={
            <div className="flex items-center gap-2">
              <button className="rounded border border-line px-2.5 py-1 text-2xs text-muted hover:text-ink hover:border-ink/40"
                disabled={busy || runState?.running} onClick={() => runDriver(v.venue, false)}>
                {bi('跑 Preview', 'Run Preview')}
              </button>
              <button className="rounded border border-warn/50 px-2.5 py-1 text-2xs text-warn hover:bg-warn/10"
                disabled={busy || runState?.running} onClick={() => runDriver(v.venue, true)}>
                {bi('真实提交', 'Submit')}
              </button>
              {v.venue === 'bybit_demo' && (
                <button className="rounded border border-line px-2.5 py-1 text-2xs text-muted hover:text-ink"
                  disabled={busy} onClick={claimDemoFunds}>
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
                <StatTile label={bi('总敞口', 'Gross')} value={money(v.gross_notional ?? 0, 'USD')} />
                <StatTile label={bi('净敞口', 'Net')} value={money(v.net_notional ?? 0, 'USD')} tone="muted" />
                <StatTile label="uPnL"
                  value={<span className={(v.upnl_total ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>
                    {(v.upnl_total ?? 0) >= 0 ? '+' : ''}{fixed(v.upnl_total ?? 0, 2)}</span>} />
                <StatTile label={bi('授权额度', 'Mandate')}
                  value={v.mandate ? money(v.mandate.max_gross_notional, 'USD') : bi('未签署', 'unsigned')}
                  tone={v.mandate ? 'pos' : 'warn'} />
              </KpiStrip>

              <div>
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi('净值曲线(哨兵心跳)', 'Equity curve (sentinel heartbeats)')}
                </div>
                <EquityCurve points={v.equity_curve ?? []} />
              </div>

              <div>
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi(`持仓(${v.positions?.length ?? 0})`, `Positions (${v.positions?.length ?? 0})`)}
                </div>
                <DataTable rows={v.positions ?? []} columns={posCols}
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
      ))}

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('全部为测试环境(测试网/Demo,零真实资金)· 提交动作 = 驱动器 CLI 子进程(mandate fail-closed 到底)· '
          + '实时 = REST 轮询粒度,周度策略刻意不做 websocket',
          'All simulated environments (testnet/demo, zero real capital) · submit = driver CLI subprocess (mandate fail-closed) · '
          + 'realtime = REST polling granularity; deliberately no websocket for a weekly strategy')}
      </div>
    </PageShell>
  );
}
