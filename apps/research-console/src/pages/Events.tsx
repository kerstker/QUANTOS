import React, { useEffect, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { fetchEventsSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { EventsPayload, EventRecord } from '@/lib/types/research';

const fallbackEvents: EventRecord[] = Array.from({length: 10}, (_, index) => ({
  event_id: `evt-${1000 + index}`,
  event_type: `research.${['agent.created', 'loop.completed', 'alpha.approved', 'paper.passed', 'risk.approved'][index % 5]}`,
  timestamp: 1_781_222_400 - index * 300,
  source: ['research-agent-loop', 'paper-trading', 'research-risk'][index % 3],
  correlation_id: `corr-${2000 + index}`,
  payload: {read_only: true, fallback: true},
}));

export default function Events() {
  const [payload, setPayload] = useState<EventsPayload | null>(null);
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
    fetchEventsSnapshot(controller.signal)
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
  const events = usingBackend && payload ? payload.events : fallbackEvents;

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <h1 className="text-lg font-bold text-slate-100">{bi('事件流', 'Event Stream')}</h1>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[760px] text-xs font-mono">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">{bi('事件 ID', 'Event ID')}</th>
              <th className="p-2 text-left">{bi('类型', 'Type')}</th>
              <th className="p-2 text-left">{bi('来源', 'Source')}</th>
              <th className="p-2 text-left">{bi('关联 ID', 'Correlation')}</th>
              <th className="p-2 text-right">{bi('时间戳', 'Timestamp')}</th>
            </tr>
          </thead>
          <tbody>
            {events.map(event => (
              <tr key={event.event_id} className="border-b border-slate-800/50 hover:bg-slate-800/50">
                <td className="p-2 text-cyan-400">{event.event_id}</td>
                <td className="p-2 text-slate-300">{event.event_type}</td>
                <td className="p-2 text-slate-400">{event.source}</td>
                <td className="p-2 text-slate-500">{event.correlation_id}</td>
                <td className="p-2 text-right text-slate-500">{new Date(event.timestamp * 1000).toISOString().slice(11, 19)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
