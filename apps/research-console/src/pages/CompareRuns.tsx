import { Link, useSearchParams } from '@/lib/router';
import { fetchResearchRunsCompare } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ResearchRunsComparePayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  Chip, LoadingVeil, PageHeader, PageShell, Panel, ProvenanceRibbon, StatusChip,
} from '@/ui/primitives';
import { stamp } from '@/ui/format';

const INVALIDATION = 'INVALIDATED_PERP_LEDGER_V1';

export default function CompareRuns() {
  const [search] = useSearchParams();
  const idsKey = search.get('ids') ?? '';
  const ids = idsKey.split(',').map(value => value.trim()).filter(Boolean);
  const {
    payload, apiState, error, usingBackend, snapshotState,
  } = useConsoleSnapshot<ResearchRunsComparePayload>(
    signal => fetchResearchRunsCompare(ids, signal),
    [idsKey],
  );
  const runs = usingBackend ? payload?.runs ?? [] : [];
  const marker = payload?.invalidation_code ?? INVALIDATION;

  return (
    <PageShell>
      <PageHeader
        title="Compare Runs"
        titleZh="运行身份对照（已失效）"
        subtitle={bi(
          '仅保留运行身份用于取证。旧指标、序列、裁决和前端示例对比已移除。',
          'Only run identity is retained for forensics. Legacy metrics, series, verdicts, and frontend comparison examples were removed.',
        )}
        actions={<Link to="/research/runs" className="text-2xs text-accent hover:underline">{bi('← 研究中心', '← Runs')}</Link>}
      />
      <ProvenanceRibbon
        usingBackend={usingBackend}
        apiState={apiState}
        realData={false}
        source={usingBackend ? payload?.data_provenance ?? 'RUN_REGISTRY_IDENTITY_ONLY' : 'NO_BACKEND_RESULT'}
        error={error}
        dataStatus={snapshotState}
        extraChips={<>
          <Chip label={marker} tone="warn" />
          <Chip label="DIAGNOSTIC_ONLY" tone="muted" />
        </>}
      />

      <LoadingVeil active={apiState === 'loading'}>
        <Panel
          eyebrow={bi('身份对照', 'Identity comparison')}
          title={bi('运行身份（无结果数据）', 'Run identities (no result data)')}
          tone="warn"
        >
          {runs.length === 0 ? (
            <p className="text-xs text-muted">
              {bi('没有可验证的运行身份；不会使用前端 mock 回退。', 'No verified run identities are available; no frontend mock fallback is used.')}
            </p>
          ) : (
            <div className="grid gap-3 lg:grid-cols-2">
              {runs.map(run => (
                <Link
                  key={run.run_id}
                  to={'/research/runs/' + encodeURIComponent(run.run_id)}
                  className="rounded-lg border border-line bg-inset/30 p-3 hover:border-accent/50"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Chip label={run.kind} tone="muted" />
                    <StatusChip value={run.status.toUpperCase()} />
                    <Chip label={run.invalidation_code ?? marker} tone="warn" />
                  </div>
                  <div className="mt-2 text-xs font-medium text-accent">{run.title}</div>
                  <div className="mt-1 font-mono text-2xs text-faint">{run.run_id} · {stamp(run.created_ts)}</div>
                </Link>
              ))}
            </div>
          )}
          <p className="mt-4 rounded-lg border border-warn/35 bg-warn/10 p-3 text-xs text-warn">
            {marker}: {bi(
              '不展示或比较收益、夏普、回撤、容量、组合提升或任何选择性结论。',
              'Returns, Sharpe, drawdown, capacity, portfolio lift, and all selection-bearing conclusions are neither displayed nor compared.',
            )}
          </p>
        </Panel>
      </LoadingVeil>
    </PageShell>
  );
}
