import React, { useEffect, useState } from 'react';
import { StatusBadge } from '@/components/status-badges';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { fetchSystemHealthSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { mockHealthServices } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { IntegrationGateStatus, SystemHealthPayload } from '@/lib/types/research';

const fallbackGates: IntegrationGateStatus[] = [
  {g: 'Phase 1G.1 Research Pipeline', s: 'PASSED'},
  {g: 'Phase 1H.1 Paper Trading', s: 'PASSED'},
  {g: 'Phase 1I.1 Portfolio', s: 'READY'},
  {g: 'Phase 1J.1 Risk', s: 'READY'},
  {g: 'Phase 1K.1 Frontend', s: 'READY'},
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

export default function SystemHealth() {
  const [payload, setPayload] = useState<SystemHealthPayload | null>(null);
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
    fetchSystemHealthSnapshot(controller.signal)
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
  const services = usingBackend ? payload.services : mockHealthServices;
  const gates = usingBackend ? payload.gates : fallbackGates;
  const safetyChecks = usingBackend ? payload.safety_checks : fallbackSafety;

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <h1 className="text-lg font-bold text-slate-100">{bi('系统健康', 'System Health')}</h1>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[680px] text-xs font-mono">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">{bi('服务', 'Service')}</th>
              <th className="p-2 text-left">{bi('状态', 'Status')}</th>
              <th className="p-2 text-right">{bi('延迟(ms)', 'Latency(ms)')}</th>
              <th className="p-2 text-right">{bi('错误率', 'Error Rate')}</th>
            </tr>
          </thead>
          <tbody>
            {services.map(s => (
              <tr key={s.service} className="border-b border-slate-800/50">
                <td className="p-2 text-slate-300">{s.service}</td>
                <td className="p-2"><StatusBadge status={s.status} /></td>
                <td className="p-2 text-right text-cyan-400">{s.latency_ms.toFixed(1)}</td>
                <td className="p-2 text-right text-amber-400">{(s.error_rate*100).toFixed(2)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="mb-2 text-sm uppercase text-slate-400">{bi('集成门禁', 'Integration Gates')}</h2>
        {gates.map(g => (
          <div key={g.g} className="flex justify-between gap-2 border-b border-slate-800 py-1 text-xs">
            <span className="text-slate-300">{g.g}</span>
            <StatusBadge status={g.s === 'PASSED' ? 'COMPLETED' : g.s} />
          </div>
        ))}
      </div>
      <div className="bg-emerald-950/30 border border-emerald-900/50 rounded-lg p-4">
        <h2 className="mb-2 text-sm uppercase text-emerald-400">{bi('安全检查', 'Safety Check')}</h2>
        <div className="space-y-1 text-xs text-emerald-300">
          {safetyChecks.map(check => (
            <div key={check}>{bi(safetyZh[check] ?? check, check)}</div>
          ))}
        </div>
      </div>
    </div>
  );
}
