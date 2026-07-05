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
interface VenuePanel {
  venue: string; configured: boolean; ok?: boolean; error?: string;
  equity?: number; available?: number; positions?: PositionRow[];
  gross_notional?: number; net_notional?: number; upnl_total?: number;
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

export default function LiveDesk() {
  const [data, setData] = useState<DeskPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastAction, setLastAction] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const poll = useCallback(async () => {
    try {
      const r = await fetch(`${OPS_URL}/api/v1/liveops/dashboard`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    poll();
    timer.current = setInterval(poll, POLL_MS);
    return () => { if (timer.current) clearInterval(timer.current); };
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
        method: 'POST', headers: { 'Content-Type': 'application/json' },
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
        method: 'POST', headers: { 'Content-Type': 'application/json' },
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
        {err && <span className="text-2xs text-neg mono">{err} · {bi('启动:', 'start:')} python -m uvicorn tools.live_ops_api:app --port 8440</span>}
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
