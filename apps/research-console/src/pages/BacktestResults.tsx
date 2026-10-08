import { bi } from '@/lib/i18n/bilingual';
import { fetchBacktestResultsSnapshot } from '@/lib/api/research-console-api';
import type { BacktestResultsPayload } from '@/lib/types/research';
import {
  Callout, Chip, LoadingVeil, PageHeader, PageShell, Panel, ProvenanceRibbon,
} from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';

const INVALIDATION = 'INVALIDATED_PERP_LEDGER_V1';

export default function BacktestResults() {
  const {
    payload, apiState, error, usingBackend, snapshotState,
  } = useConsoleSnapshot<BacktestResultsPayload>(
    signal => fetchBacktestResultsSnapshot(signal),
  );
  const marker = payload?.invalidation_code ?? INVALIDATION;

  return (
    <PageShell>
      <PageHeader
        title="Backtest Evidence"
        titleZh="回测证据（已失效）"
        subtitle={bi(
          '永续合约账本 v1 及其收益、夏普、回撤、容量与组合结论已整体失效；本页不再显示旧数字。',
          'Perpetual-ledger v1 and all derived return, Sharpe, drawdown, capacity, and portfolio claims are invalidated. This page no longer displays legacy figures.',
        )}
      />
      <ProvenanceRibbon
        usingBackend={usingBackend}
        apiState={apiState}
        realData={false}
        source={usingBackend ? payload?.data_provenance ?? 'INVALIDATED_BACKTEST_EVIDENCE' : 'NO_BACKEND_RESULT'}
        error={error}
        dataStatus={snapshotState}
        extraChips={<>
          <Chip label={marker} tone="warn" />
          <Chip label="DIAGNOSTIC_ONLY" tone="muted" />
        </>}
      />

      <LoadingVeil active={apiState === 'loading'}>
        <Panel
          eyebrow={bi('权威边界', 'Authority boundary')}
          title={bi('旧回测结果不可用于任何决策', 'Legacy backtest results cannot drive decisions')}
          tone="warn"
        >
          <Callout
            tone="warn"
            summary={bi(
              'API 已清空所有旧 run-registry 指标和序列；前端也没有观察值或 mock 回退。',
              'The API clears every legacy run-registry metric and series; the frontend has no observed-value or mock fallback.',
            )}
          >
            {bi(
              '留存 artifact 只用于取证，不授予候选选择、晋级、纸面持久化、风险 READY 或运行授权。重新开放需要独立的永续账本 v2、精确数据/代码/结果绑定和新的准入证据。',
              'Retained artifacts are forensic only. They grant no candidate selection, promotion, paper persistence, risk-READY, or runtime authority. Reopening requires an independent perpetual-ledger v2 with exact data/code/result binding and new admission evidence.',
            )}
          </Callout>
          <div className="mt-4 grid gap-2 text-xs text-muted sm:grid-cols-2">
            <div className="rounded-lg border border-line bg-inset/40 p-3">carry / combo / OI / survivorship: <span className="font-mono text-warn">{marker}</span></div>
            <div className="rounded-lg border border-line bg-inset/40 p-3">factory / paper / portfolio: <span className="font-mono text-warn">{marker}</span></div>
          </div>
        </Panel>
      </LoadingVeil>
    </PageShell>
  );
}
