import { fetchAlphaLifecycle } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { AlphaLifecyclePayload } from '@/lib/types/research';
import {
  Chip, LoadingVeil, PageHeader, PageShell, Panel, ProvenanceRibbon,
} from '@/ui/primitives';
import { ConsoleSnapshotUnavailablePage } from '@/ui/ConsoleSnapshotUnavailablePage';
import { selectConsoleDisplayPayload } from '@/ui/console-display-payload';
import { AsOfStamp, EndpointFootnote, StateView, StatusBanner } from '@/ui/workbench';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';

const INVALIDATION = 'INVALIDATED_PERP_LEDGER_V1';

const closedFallbackPayload: AlphaLifecyclePayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT',
  read_only: true,
  api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC',
  snapshot_status: 'NO_SNAPSHOT',
  snapshot_present: false,
  artifact: null,
  generated_at_ms: null,
  readiness: 'L2_DRY_RUN_ONLY',
  alphas: [],
  stage_legend: {},
  authoritative: false,
  evidence_status: 'DIAGNOSTIC_ONLY',
  invalidation_code: INVALIDATION,
  honesty_note: 'API 不可用时保持空态；前端不回退展示任何旧账本指标。',
};

export default function AlphaLifecycle() {
  const snapshot = useConsoleSnapshot<AlphaLifecyclePayload>(signal => fetchAlphaLifecycle(signal));
  const {
    apiState, error, usingBackend, snapshotState,
  } = snapshot;
  const data = selectConsoleDisplayPayload(snapshot, closedFallbackPayload);
  if (data === null) {
    return <ConsoleSnapshotUnavailablePage endpoint="/api/v1/console/alpha-lifecycle" apiState={apiState} error={error} />;
  }
  const marker = data.invalidation_code ?? INVALIDATION;
  const absent = data.snapshot_present === false || data.snapshot_status === 'NO_SNAPSHOT';
  const pageState = apiState === 'loading' ? 'loading' : absent ? 'empty' : 'ready';

  return (
    <PageShell>
      <PageHeader
        title="Lifecycle Cockpit"
        titleZh="生命周期身份（已失效）"
        subtitle={bi(
          '旧永续账本的生命周期证据已整体失效。本页只保留 alpha 身份用于取证，不显示经济指标、阶段结论或运行授权。',
          'Legacy perpetual-ledger lifecycle evidence is invalidated. This page retains alpha identity for forensics only and displays no economic metrics, stage conclusions, or runtime authority.',
        )}
        actions={<AsOfStamp ts={data.generated_at_ms} />}
      />
      <ProvenanceRibbon
        usingBackend={usingBackend}
        apiState={apiState}
        realData={false}
        source={usingBackend ? data.data_provenance : 'NO_BACKEND_RESULT'}
        error={error}
        dataStatus={snapshotState}
        extraChips={<>
          <Chip label={marker} tone="warn" />
          <Chip label="DIAGNOSTIC_ONLY" tone="muted" />
        </>}
      />
      <StatusBanner />

      <LoadingVeil active={apiState === 'loading'}>
        <StateView
          state={pageState}
          empty={bi(
            '没有留存的生命周期身份快照；前端不会生成示例 alpha 或结果。',
            'No retained lifecycle identity snapshot is available; the frontend does not generate example alphas or results.',
          )}
        >
          <Panel
            eyebrow={bi('取证身份', 'Forensic identity')}
            title={bi('留存 alpha（无结果字段）', 'Retained alphas (no result fields)')}
            tone="warn"
          >
            {data.alphas.length === 0 ? (
              <p className="text-xs text-muted">{bi('本快照没有 alpha 身份。', 'This snapshot contains no alpha identities.')}</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {data.alphas.map(alpha => (
                  <div key={alpha.alpha_id} className="rounded-lg border border-line bg-inset/35 p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Chip label={marker} tone="warn" />
                      <Chip label="NON_AUTHORITATIVE" tone="muted" />
                    </div>
                    <div className="mt-2 text-xs font-semibold text-ink">{alpha.display_name}</div>
                    <div className="mt-1 font-mono text-2xs text-faint">{alpha.alpha_id}</div>
                  </div>
                ))}
              </div>
            )}
            <p className="mt-4 rounded-lg border border-warn/35 bg-warn/10 p-3 text-xs text-warn">
              {marker}: {bi(
                '留存 artifact 保持原样供审计，但其回测、压力、容量、组合、纸面和执行字段都不能越过 API/前端边界。',
                'The retained artifact remains unchanged for audit, but its backtest, stress, capacity, portfolio, paper, and execution fields cannot cross the API/frontend boundary.',
              )}
            </p>
          </Panel>
        </StateView>

        <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
          {data.honesty_note}
        </div>
        <EndpointFootnote
          path="/api/v1/console/alpha-lifecycle"
          note={bi(
            '只读 · 失效身份投影 · 原 artifact 不修改 · 不授予任何能力',
            'read-only · invalidated identity projection · original artifact unchanged · grants no capability',
          )}
        />
      </LoadingVeil>
    </PageShell>
  );
}
