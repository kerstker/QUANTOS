import { fetchSystemHealthSnapshot, getConsoleRuntimeMode } from '@/lib/api/research-console-api';
import type { SystemHealthPayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';

export function HealthDrawer() {
  const state = useConsoleSnapshot<SystemHealthPayload>(fetchSystemHealthSnapshot);
  if (!state.usingBackend || state.payload === null) {
    const code = getConsoleRuntimeMode() === 'MOCK' ? 'DEMO_MODE_NO_HEALTH_AUTHORITY' : 'HEALTH_UNAVAILABLE';
    return (
      <div className="rounded-lg border border-warn/35 bg-warn/10 p-4" role="status">
        <p className="font-mono text-xs font-semibold text-warn">{code}</p>
        <p className="mt-2 break-all font-mono text-[10px] text-muted">{state.error ?? state.apiState}</p>
        <p className="mt-3 text-xs leading-5 text-muted">未配置探活或读取失败时状态保持 UNKNOWN，不把缺测量解释为健康。</p>
      </div>
    );
  }

  const payload = state.payload;
  const unhealthy = payload.services.filter(service => service.status !== 'healthy');
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-2">
        {(['healthy', 'degraded', 'down'] as const).map(status => (
          <div key={status} className="rounded border border-line bg-inset/45 p-3 text-center">
            <p className="text-[9px] uppercase text-faint">{status}</p>
            <p className={`mt-1 font-mono text-lg font-semibold ${status === 'healthy' ? 'text-pos' : status === 'down' ? 'text-neg' : 'text-warn'}`}>
              {payload.services.filter(service => service.status === status).length}
            </p>
          </div>
        ))}
      </div>
      <div>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">Non-healthy or unmeasured services</p>
        {unhealthy.length === 0 ? <p className="font-mono text-xs text-pos">NO_REPORTED_DEGRADATION</p> : (
          <div className="space-y-2">
            {unhealthy.slice(0, 12).map(service => (
              <div key={service.service} className="flex items-center justify-between gap-3 rounded border border-line bg-inset/45 px-3 py-2">
                <span className="break-all font-mono text-[10px] text-ink">{service.service}</span>
                <span className={`font-mono text-[10px] ${service.status === 'down' ? 'text-neg' : 'text-warn'}`}>{service.status.toUpperCase()}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">Safety checks · reported</p>
        <div className="space-y-1 font-mono text-[10px] text-muted">
          {payload.safety_checks.length === 0 ? 'EMPTY · NO_SAFETY_CHECKS_REPORTED' : payload.safety_checks.map(check => <p key={check}>· {check}</p>)}
        </div>
      </div>
    </div>
  );
}
