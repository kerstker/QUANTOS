import { fetchSystemHealthSnapshot } from '@/lib/api/research-console-api';
import { mockHealthServices } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import type { IntegrationGateStatus, ServiceHealth, SystemHealthPayload } from '@/lib/types/research';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, Chip, LoadingVeil, type Column,
} from '@/ui/primitives';
import { BarSeries, Donut } from '@/ui/charts';
import { num, pct, fixed, statusTone, type Tone } from '@/ui/format';

const fallbackGates: IntegrationGateStatus[] = [
  { g: 'Phase 1G.1 Research Pipeline', s: 'PASSED' },
  { g: 'Phase 1H.1 Paper Trading', s: 'PASSED' },
  { g: 'Phase 1I.1 Portfolio', s: 'READY' },
  { g: 'Phase 1J.1 Risk', s: 'READY' },
  { g: 'Phase 1K.1 Frontend', s: 'READY' },
];

const fallbackSafety = [
  'No OMS/EMS imports',
  'No broker connectivity',
  'No CTP/FIX gateways',
  'No live trading controls',
  'Research-only',
];

const safetyZh: Record<string, string> = {
  'No OMS/EMS imports': '无 OMS/EMS 导入',
  'No broker connectivity': '无券商连接',
  'No CTP/FIX gateways': '无 CTP/FIX 网关',
  'No live trading controls': '无实盘交易控件',
  'Research-only': '仅研究用途',
};

const STATUS_ZH: Record<string, string> = {
  healthy: bi('健康', 'Healthy'),
  degraded: bi('降级', 'Degraded'),
  down: bi('宕机', 'Down'),
  unknown: bi('未知', 'Unknown'),
};

const statusDonutTone: Record<string, Tone> = {
  healthy: 'pos', degraded: 'warn', down: 'neg', unknown: 'muted',
};

/** A gate string counts as "passed" when it maps to a positive tone. */
const gateOk = (s: string) => statusTone(s) === 'pos' || statusTone(s) === 'info';

export default function SystemHealth() {
  const { payload, apiState, error: apiError, usingBackend } = useConsoleSnapshot<SystemHealthPayload>(
    s => fetchSystemHealthSnapshot(s),
  );

  const services: ServiceHealth[] = usingBackend ? payload!.services : mockHealthServices;
  const gates: IntegrationGateStatus[] = usingBackend ? payload!.gates : fallbackGates;
  const safetyChecks: string[] = usingBackend ? payload!.safety_checks : fallbackSafety;

  // --- Derived, deterministic aggregates --------------------------------------
  const total = services.length;
  const healthy = services.filter(s => s.status === 'healthy').length;
  const degraded = services.filter(s => s.status === 'degraded').length;
  const down = services.filter(s => s.status === 'down').length;
  const unknown = services.filter(s => s.status === 'unknown').length;
  const measuredLatencies = services
    .map(s => s.latency_ms)
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  const measuredErrorRates = services
    .map(s => s.error_rate)
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  const avgLatency = measuredLatencies.length
    ? measuredLatencies.reduce((a, value) => a + value, 0) / measuredLatencies.length
    : 0;
  const maxErr = measuredErrorRates.length ? Math.max(...measuredErrorRates) : 0;
  const worstErrSvc = services.reduce<ServiceHealth | null>(
    (w, s) => (
      s.error_rate === null
      || (w && w.error_rate !== null && s.error_rate <= w.error_rate)
        ? w
        : s
    ), null);
  const gatesPassed = gates.filter(g => gateOk(g.s)).length;

  const overallTone: Tone = down > 0 ? 'neg' : degraded > 0 || unknown > 0 ? 'warn' : 'pos';
  const overallLabel = down > 0 ? bi('故障', 'IMPAIRED')
    : degraded > 0 ? bi('降级', 'DEGRADED')
      : unknown > 0 ? bi('部分未知', 'PARTLY UNKNOWN')
        : bi('全绿', 'ALL HEALTHY');

  const statusSegments = [
    { label: STATUS_ZH.healthy, value: healthy, tone: statusDonutTone.healthy },
    { label: STATUS_ZH.degraded, value: degraded, tone: statusDonutTone.degraded },
    { label: STATUS_ZH.down, value: down, tone: statusDonutTone.down },
    { label: STATUS_ZH.unknown, value: unknown, tone: statusDonutTone.unknown },
  ].filter(s => s.value > 0);

  const latencyData = services.map(s => s.latency_ms ?? 0);
  const latencyLabels = services.map(s => s.service.replace(/-.*/, '').slice(0, 6));

  const serviceCols: Column<ServiceHealth>[] = [
    {
      key: 'service', header: bi('服务', 'Service'),
      render: s => <span className="mono font-medium text-ink">{s.service}</span>,
    },
    {
      key: 'status', header: bi('状态', 'Status'),
      render: s => <StatusChip value={s.status} />,
    },
    {
      key: 'latency', header: bi('延迟', 'Latency'), align: 'right', mono: true, width: '210px',
      render: s => (
        <div className="flex items-center justify-end gap-2">
          {s.latency_ms === null ? (
            <span className="w-24 text-right text-faint">{bi('未测量', 'not measured')}</span>
          ) : (
            <>
              <ProgressBar value={s.latency_ms} max={Math.max(avgLatency * 2.5, 1)}
                tone={s.latency_ms > avgLatency * 1.5 ? 'warn' : 'info'} />
              <span className="tnum w-16 text-right">{fixed(s.latency_ms, 1)}<span className="text-faint"> ms</span></span>
            </>
          )}
        </div>
      ),
    },
    {
      key: 'error', header: bi('错误率', 'Error Rate'), align: 'right', mono: true, width: '170px',
      render: s => (
        <div className="flex items-center justify-end gap-2">
          {s.error_rate === null ? (
            <span className="w-24 text-right text-faint">{bi('未测量', 'not measured')}</span>
          ) : (
            <>
              <ProgressBar value={s.error_rate} max={Math.max(maxErr, 1e-9)}
                tone={s.error_rate > 0.02 ? 'neg' : s.error_rate > 0 ? 'warn' : 'pos'} />
              <span className={`tnum w-14 text-right ${s.error_rate > 0.02 ? 'text-neg' : 'text-muted'}`}>
                {pct(s.error_rate, 2)}
              </span>
            </>
          )}
        </div>
      ),
    },
  ];

  return (
    <PageShell>
      <PageHeader title="System Health" titleZh="系统健康"
        subtitle={bi('研究平台服务、集成门禁与安全边界的只读运行态快照',
          'Read-only operational snapshot of research-platform services, integration gates and the safety boundary')}
        actions={<Chip label={overallLabel} tone={overallTone} soft={false} />} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />
      <LoadingVeil active={apiState === 'loading'}>
      <div className="rounded-lg border border-warn/25 bg-warn/5 px-3 py-1.5 text-2xs text-warn">
        {bi('注:仅研究控制台自身的监控调用已实测；其余服务未配置探活时显示“未知”，不会伪报健康。错误率暂无请求计数器。',
          'Only the console process monitoring call is measured. Services without configured probes are shown as “unknown”, never as healthy; error rates require a request counter.')}
      </div>

      <KpiStrip cols={7}>
        <StatTile label={bi('服务总数', 'Services')} value={num(total)} />
        <StatTile label={bi('健康', 'Healthy')} value={num(healthy)} tone="pos"
          sub={bi(`${total} 中 ${healthy} 个`, `${healthy} of ${total}`)} />
        <StatTile label={bi('降级', 'Degraded')} value={num(degraded)} tone={degraded ? 'warn' : 'muted'} />
        <StatTile label={bi('宕机', 'Down')} value={num(down)} tone={down ? 'neg' : 'muted'} />
        <StatTile label={bi('未知', 'Unknown')} value={num(unknown)} tone={unknown ? 'warn' : 'muted'} />
        <StatTile label={bi('平均延迟', 'Avg Latency')} value={measuredLatencies.length ? fixed(avgLatency, 1) : '—'} unit={measuredLatencies.length ? 'ms' : undefined} tone="info" />
        <StatTile label={bi('峰值错误率', 'Peak Error')} value={measuredErrorRates.length ? pct(maxErr, 2) : '—'}
          tone={!measuredErrorRates.length ? 'muted' : maxErr > 0.02 ? 'neg' : maxErr > 0 ? 'warn' : 'pos'}
          sub={worstErrSvc ? worstErrSvc.service : undefined} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('时延', 'Latency')}
          title={bi('各服务延迟分布', 'Per-Service Latency')}
          right={<Chip label={bi(`均值 ${fixed(avgLatency, 1)} ms`, `avg ${fixed(avgLatency, 1)} ms`)} tone="info" />}>
          <BarSeries data={latencyData} labels={latencyLabels} height={200}
            posTone="info" valueFormat={v => fixed(v, 0)} />
        </Panel>

        <Panel eyebrow={bi('运行态', 'Liveness')} title={bi('服务状态构成', 'Status Composition')}>
          <div className="flex items-center gap-5">
            <Donut segments={statusSegments} size={140} />
            <div className="flex min-w-0 flex-1 flex-col gap-2.5">
              {([
                ['healthy', healthy] as const,
                ['degraded', degraded] as const,
                ['down', down] as const,
                ['unknown', unknown] as const,
              ]).map(([k, v]) => (
                <div key={k} className="flex flex-col gap-1">
                  <div className="flex items-center justify-between gap-2">
                    <StatusChip value={k} />
                    <span className="tnum text-xs text-muted">{num(v)}</span>
                  </div>
                  <ProgressBar value={v} max={Math.max(total, 1)} tone={statusDonutTone[k]} />
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('监控', 'Monitoring')}
          title={bi('服务健康明细', 'Service Health Detail')}
          right={<Chip label={bi(`${total} 个服务`, `${total} services`)} tone="info" />}>
          <DataTable columns={serviceCols} rows={services} getKey={s => s.service} />
        </Panel>

        <Panel eyebrow={bi('阶段门', 'Phase Gates')} title={bi('集成门禁', 'Integration Gates')}
          right={<Chip label={bi(`${gatesPassed}/${gates.length} 通过`, `${gatesPassed}/${gates.length} ready`)}
            tone={gatesPassed === gates.length ? 'pos' : 'warn'} />}>
          <div className="flex flex-col gap-2">
            {gates.map(g => (
              <div key={g.g} className="flex items-center justify-between gap-3 rounded-lg border border-line bg-inset/40 px-3 py-2">
                <div className="flex min-w-0 items-center gap-2">
                  <GateDot ok={gateOk(g.s)} />
                  <span className="truncate text-xs text-ink">{g.g}</span>
                </div>
                <StatusChip value={g.s} />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel tone="pos" eyebrow={bi('安全边界', 'Safety Boundary')}
        title={bi('安全检查 · L2 只读', 'Safety Checks · L2 Read-Only')}
        right={<Chip label={bi(`${safetyChecks.length} 项全绿`, `${safetyChecks.length} enforced`)} tone="pos" />}>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {safetyChecks.map(check => (
            <div key={check} className="flex items-center gap-2.5 rounded-lg border border-pos/25 bg-pos/5 px-3 py-2">
              <GateDot ok />
              <span className="truncate text-xs text-ink">{bi(safetyZh[check] ?? check, check)}</span>
            </div>
          ))}
        </div>
      </Panel>
      </LoadingVeil>
    </PageShell>
  );
}
