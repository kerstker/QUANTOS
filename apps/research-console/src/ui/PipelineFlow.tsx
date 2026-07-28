import React from 'react';
import { Link } from '@/lib/router';
import { GROUPS, PAGES, type GroupId } from '@/app/routes';
import { bi } from '@/lib/i18n/bilingual';
import { useConsoleSnapshot } from './useConsoleSnapshot';
import { stageAutomationCounts } from './automation-map';
import {
  fetchResearchRuns, fetchDataCatalogSnapshot, fetchAlphaZooSnapshot,
  fetchValidationSnapshot, fetchMonitoringDriftSnapshot, fetchDryRunSnapshot,
  fetchSystemHealthSnapshot,
} from '@/lib/api/research-console-api';

/* ============================================================================
   PipelineFlow — the visual research-lifecycle pipeline (2026-07-24 refactor).
   The seven zones of the console are rendered as a CONNECTED FLOW:
     总览 → 数据 → 因子研究 → 验证 → 组合与风险 → 执行与跟踪 → 治理与系统
   Every node carries ONE live metric pulled from that zone's real read-only
   endpoint. Honesty contract: no backend = muted node with "—" (never a
   fabricated number); a connected node shows the measured count. Click any
   node to jump into that stage's first page. Read-only; triggers nothing.
   ========================================================================== */

interface MetricSpec {
  fetcher: (signal: AbortSignal) => Promise<unknown>;
  /** Pull the single headline number out of the payload; null = unavailable. */
  extract: (payload: Record<string, unknown>) => string | number | null;
  labelZh: string;
  labelEn: string;
}

function nestedRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : null;
}

const STAGE_METRICS: Record<GroupId, MetricSpec> = {
  overview: {
    fetcher: fetchResearchRuns as (s: AbortSignal) => Promise<unknown>,
    extract: p => (Array.isArray(p.runs) ? p.runs.length : null),
    labelZh: '研究运行', labelEn: 'runs',
  },
  data: {
    fetcher: fetchDataCatalogSnapshot as (s: AbortSignal) => Promise<unknown>,
    extract: p => {
      const summary = nestedRecord(p.summary);
      return typeof summary?.datasets === 'number' ? summary.datasets : null;
    },
    labelZh: '数据集', labelEn: 'datasets',
  },
  alpha: {
    fetcher: fetchAlphaZooSnapshot as (s: AbortSignal) => Promise<unknown>,
    extract: p => (Array.isArray(p.rows) ? p.rows.length : null),
    labelZh: '因子', labelEn: 'alphas',
  },
  validation: {
    fetcher: fetchValidationSnapshot as (s: AbortSignal) => Promise<unknown>,
    extract: p => (Array.isArray(p.reports) ? p.reports.length : null),
    labelZh: '验证报告', labelEn: 'reports',
  },
  portfolio: {
    fetcher: fetchMonitoringDriftSnapshot as (s: AbortSignal) => Promise<unknown>,
    extract: p => {
      const summary = nestedRecord(p.summary);
      return typeof summary?.monitors === 'number' ? summary.monitors : null;
    },
    labelZh: '衰退监控', labelEn: 'monitors',
  },
  execution: {
    fetcher: fetchDryRunSnapshot as (s: AbortSignal) => Promise<unknown>,
    extract: p => (typeof p.n_runs === 'number' ? p.n_runs : null),
    labelZh: '干跑记录', labelEn: 'dry-runs',
  },
  system: {
    fetcher: fetchSystemHealthSnapshot as (s: AbortSignal) => Promise<unknown>,
    extract: p => (Array.isArray(p.services) ? p.services.length : null),
    labelZh: '服务', labelEn: 'services',
  },
};

const STAGE_ICONS: Record<GroupId, string> = {
  overview: '◎', data: '▤', alpha: 'α', validation: '✓',
  portfolio: '◈', execution: '▶', system: '⚙',
};

function firstPathOf(group: GroupId): string {
  return PAGES.find(p => p.group === group)?.path ?? '/research/getting-started';
}

/** One live metric chip inside a node. Isolated component so each node owns its hook. */
function StageMetric({ spec, compact }: { spec: MetricSpec; compact: boolean }) {
  const { payload, usingBackend, apiState } = useConsoleSnapshot<unknown>(s => spec.fetcher(s));
  const record = payload !== null && typeof payload === 'object'
    ? payload as Record<string, unknown>
    : null;
  const value = usingBackend && record ? spec.extract(record) : null;
  const live = usingBackend && value != null;
  if (compact) {
    return (
      <span className="flex items-baseline gap-1">
        <span className={`h-1.5 w-1.5 rounded-full ${apiState === 'loading' ? 'q-live' : ''}`}
          style={{ background: live ? 'var(--pos)' : 'var(--faint)' }} />
        <span className={`ticker-num text-xs font-semibold ${live ? 'text-ink' : 'text-faint'}`}>
          {live ? value : '—'}
        </span>
      </span>
    );
  }
  return (
    <div className="flex items-end justify-between gap-2">
      <div className="flex flex-col">
        <span className="text-3xs uppercase tracking-[0.12em] text-faint">
          {bi(spec.labelZh, spec.labelEn)}
        </span>
        <span className={`ticker-num text-[26px] font-bold leading-tight ${live ? 'text-ink' : 'text-faint'}`}>
          {apiState === 'loading' ? '…' : live ? value : '—'}
        </span>
      </div>
      <span className="mb-1 flex items-center gap-1 text-3xs" style={{ color: live ? 'var(--pos)' : 'var(--faint)' }}>
        <span className={`h-1.5 w-1.5 rounded-full ${apiState === 'loading' ? 'q-live' : ''}`}
          style={{ background: live ? 'var(--pos)' : 'var(--faint)' }} />
        {live ? 'LIVE' : bi('离线', 'OFFLINE')}
      </span>
    </div>
  );
}

/** Auto-vs-human split badge for a stage (from the honest automation map). */
function AutoHumanBadge({ id, compact }: { id: GroupId; compact: boolean }) {
  const { auto, human } = stageAutomationCounts(id);
  return (
    <span className={`flex items-center gap-2 ${compact ? 'text-3xs' : 'text-2xs'}`}>
      <span className="inline-flex items-center gap-1 text-info" title={bi('系统自动化步骤', 'automated steps')}>
        <span aria-hidden>⚙</span>{bi(`自动 ${auto}`, `auto ${auto}`)}
      </span>
      {human > 0 && (
        <span className="inline-flex items-center gap-1 font-medium" style={{ color: 'var(--warn)' }}
          title={bi('需要真人操作的门', 'human gates')}>
          <span aria-hidden>👤</span>{bi(`人工 ${human}`, `human ${human}`)}
        </span>
      )}
      {human === 0 && !compact && (
        <span className="inline-flex items-center gap-1 text-faint" title={bi('该阶段无人工门,全自动', 'no human gate in this stage')}>
          {bi('无人工门', 'no human gate')}
        </span>
      )}
    </span>
  );
}

function FlowArrow({ active }: { active: boolean }) {  return (
    <span aria-hidden
      className={`mx-1 hidden shrink-0 select-none self-center text-base md:inline ${active ? 'text-accent' : 'text-faint/60'}`}>
      ─▶
    </span>
  );
}

/**
 * @param activeGroup  highlight the stage the operator is currently inside
 * @param compact      stepper strip (app shell) vs full cards (command center)
 */
export function PipelineFlow({ activeGroup, compact = false, className = '' }: {
  activeGroup?: GroupId | null;
  compact?: boolean;
  className?: string;
}) {
  const groups = GROUPS;
  if (compact) {
    return (
      <div className={`flex items-stretch overflow-x-auto ${className}`} role="navigation"
        aria-label={bi('研究全流程管道', 'research lifecycle pipeline')}>
        {groups.map((g, i) => {
          const active = g.id === activeGroup;
          return (
            <React.Fragment key={g.id}>
              {i > 0 && <FlowArrow active={active} />}
              <Link to={firstPathOf(g.id)}
                className={`group flex shrink-0 items-center gap-2.5 px-3.5 py-2 transition-colors duration-100 ${
                  active ? 'text-ink' : 'text-muted hover:text-ink'}`}>
                <span className={`grid h-6 w-6 shrink-0 place-items-center rounded text-[11px] font-bold ${
                  active ? 'text-accent-ink' : 'text-faint group-hover:text-muted'}`}
                  style={active
                    ? { background: 'var(--grad)' }
                    : { background: 'var(--inset)', boxShadow: 'inset 0 0 0 1px var(--line)' }}>
                  {STAGE_ICONS[g.id]}
                </span>
                <span className="flex min-w-0 flex-col leading-tight">
                  <span className="whitespace-nowrap text-[13px] font-semibold">
                    {g.zh} <span className="hidden text-3xs font-normal uppercase tracking-[0.12em] text-faint lg:inline">{g.en}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    <StageMetric spec={STAGE_METRICS[g.id]} compact />
                    <AutoHumanBadge id={g.id} compact />
                  </span>
                </span>
              </Link>
            </React.Fragment>
          );
        })}
      </div>
    );
  }

  return (
    <div className={`flex flex-col gap-2 md:flex-row md:items-stretch md:gap-0 ${className}`}
      role="navigation" aria-label={bi('研究全流程管道', 'research lifecycle pipeline')}>
      {groups.map((g, i) => {
        const active = g.id === activeGroup;
        return (
          <React.Fragment key={g.id}>
            {i > 0 && <FlowArrow active={active} />}
            <Link to={firstPathOf(g.id)}
              className={`lift terminal-panel flex min-w-0 flex-1 flex-col gap-3 rounded-lg p-4 ${
                active ? 'border-line-strong' : ''}`}
              style={active ? { boxShadow: 'var(--glow-accent)' } : undefined}>
              <div className="flex items-center gap-2.5">
                <span className={`grid h-8 w-8 shrink-0 place-items-center rounded text-sm font-bold ${
                  active ? 'text-accent-ink' : 'text-accent'}`}
                  style={active
                    ? { background: 'var(--grad)' }
                    : { background: 'color-mix(in srgb, var(--accent) 10%, transparent)', boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--accent) 25%, transparent)' }}>
                  {STAGE_ICONS[g.id]}
                </span>
                <div className="min-w-0 leading-tight">
                  <div className="truncate text-sm font-semibold text-ink">{g.zh}</div>
                  <div className="truncate text-3xs uppercase tracking-[0.14em] text-faint">{g.en}</div>
                </div>
                <span className="ml-auto ticker-num text-3xs text-faint">{i + 1}/{groups.length}</span>
              </div>
              <StageMetric spec={STAGE_METRICS[g.id]} compact={false} />
              <AutoHumanBadge id={g.id} compact={false} />
              <span className={`text-2xs ${active ? 'text-accent' : 'text-faint group-hover:text-muted'}`}>
                {bi('进入该阶段 →', 'enter stage →')}
              </span>
            </Link>
          </React.Fragment>
        );
      })}
    </div>
  );
}
