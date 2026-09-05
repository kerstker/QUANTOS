import { useEffect, useState } from 'react';
import { Link, useParams } from '@/lib/router';
import { fetchResearchRun, fetchTechnicalReplay } from '@/lib/api/research-console-api';
import { cancelRun, runnerEnabled } from '@/lib/api/research-runner-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ResearchRunPayload, TechnicalReplayPayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  Button, Chip, LoadingVeil, PageHeader, PageShell, Panel, ProvenanceRibbon, StatusChip,
} from '@/ui/primitives';
import { stamp } from '@/ui/format';

const INVALIDATION = 'INVALIDATED_PERP_LEDGER_V1';

export default function RunDetail() {
  const { id = '' } = useParams();
  const [reloadKey, setReloadKey] = useState(0);
  const [cancelMsg, setCancelMsg] = useState<string | null>(null);
  const {
    payload, apiState, error, usingBackend, snapshotState,
  } = useConsoleSnapshot<ResearchRunPayload>(
    signal => fetchResearchRun(id, signal),
    [id, reloadKey],
  );
  const run = usingBackend ? payload?.run ?? null : null;
  const technicalSnapshot = useConsoleSnapshot<TechnicalReplayPayload>(
    signal => fetchTechnicalReplay(id, signal), [id, reloadKey],
  );
  const technical = technicalSnapshot.usingBackend
    && technicalSnapshot.payload?.authoritative === false
    && technicalSnapshot.payload?.evidence_status === 'TECHNICAL_VALIDATION_ONLY'
    && technicalSnapshot.payload.technical_replay?.run_id === id
    && technicalSnapshot.payload.technical_replay.authoritative === false
    && technicalSnapshot.payload.technical_replay.evidence_status === 'TECHNICAL_VALIDATION_ONLY'
    && run?.kind === 'perpetual_replay'
    ? technicalSnapshot.payload.technical_replay : null;
  const active = run != null && (run.status === 'queued' || run.status === 'running');

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setReloadKey(value => value + 1), 2500);
    return () => clearInterval(timer);
  }, [active]);

  async function onCancel() {
    if (!run) return;
    try {
      const response = await cancelRun(run.run_id);
      setCancelMsg(response.cancelled
        ? bi('已请求软取消；运行中的计算可能继续，结果将丢弃。', 'Soft cancellation requested; computation may continue and its result will be discarded.')
        : bi('无法取消（运行已结束）', 'cannot cancel (terminal)'));
      setReloadKey(value => value + 1);
    } catch (cause) {
      setCancelMsg(cause instanceof Error ? cause.message : String(cause));
    }
  }

  if (!run) {
    return (
      <PageShell>
        <PageHeader
          title="Run Detail"
          titleZh="运行身份"
          subtitle={bi('未取得可验证的运行身份；不会回退展示示例收益。', 'No verified run identity is available; no example result fallback is shown.')}
        />
        <ProvenanceRibbon
          usingBackend={usingBackend}
          apiState={apiState}
          source={usingBackend ? payload?.data_provenance ?? 'RUN_REGISTRY' : 'NO_BACKEND_RESULT'}
          error={error}
          dataStatus={snapshotState}
        />
        <Panel eyebrow={bi('不可用', 'Unavailable')} title={id || 'UNKNOWN_RUN'}>
          <Link to="/research/runs" className="text-xs text-accent hover:underline">
            {bi('返回研究中心', 'Back to research')}
          </Link>
        </Panel>
      </PageShell>
    );
  }

  const marker = technical ? 'TECHNICAL_VALIDATION_ONLY'
    : run.invalidation_code ?? payload?.invalidation_code ?? INVALIDATION;
  return (
    <PageShell>
      <PageHeader
        title={run.title}
        titleZh={run.title}
        subtitle={technical ? bi('冻结公开数据的引擎技术验证，不构成策略或交易准入证据。',
          'Engine validation on frozen public data; no strategy or trading authority.') : bi(
          '仅显示队列/审计身份。旧指标、序列、日志中的结果数字与裁决已从投影移除。',
          'Queue and audit identity only. Legacy metrics, series, result-bearing logs, and verdicts were removed from the projection.',
        )}
        actions={<div className="flex items-center gap-2">
          <StatusChip value={run.status.toUpperCase()} />
          {active && runnerEnabled() && <Button onClick={onCancel} variant="neg">{bi('取消', 'Cancel')}</Button>}
          <Link to="/research/runs" className="text-2xs text-accent hover:underline">{bi('← 研究中心', '← Runs')}</Link>
        </div>}
      />
      <ProvenanceRibbon
        usingBackend={usingBackend}
        apiState={apiState}
        realData={false}
        source={payload?.data_provenance ?? 'RUN_REGISTRY_IDENTITY_ONLY'}
        error={error}
        dataStatus={snapshotState}
        extraChips={<>
          <Chip label={marker} tone="warn" />
          <Chip label="DIAGNOSTIC_ONLY" tone="muted" />
        </>}
      />

      <LoadingVeil active={apiState === 'loading'}>
        <Panel eyebrow={bi('运行身份', 'Run identity')} title={run.run_id} tone="warn">
          <dl className="grid gap-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
            <div><dt className="text-faint">kind</dt><dd className="mt-1 font-mono text-ink">{run.kind}</dd></div>
            <div><dt className="text-faint">execution status</dt><dd className="mt-1 font-mono text-ink">{run.status}</dd></div>
            <div><dt className="text-faint">created</dt><dd className="mt-1 font-mono text-ink">{stamp(run.created_ts)}</dd></div>
            <div><dt className="text-faint">code</dt><dd className="mt-1 font-mono text-ink">{run.code_version ?? 'UNAVAILABLE'}</dd></div>
          </dl>
          <p className="mt-4 rounded-lg border border-warn/35 bg-warn/10 p-3 text-xs text-warn">
            {marker}: {bi('留存结果不可用于选择、晋级、风险批准或纸面/实盘授权。', 'Retained results cannot drive selection, promotion, risk approval, or paper/live authority.')}
          </p>
          {cancelMsg && <p className="mt-2 text-xs text-warn">{cancelMsg}</p>}
        </Panel>
        {technical && <>
          <Panel eyebrow="TECHNICAL_VALIDATION_ONLY" title="配置、身份与对账">
            <dl className="grid gap-3 text-xs sm:grid-cols-2">
              {Object.entries(technical.metrics).map(([key, value]) => <div key={key}>
                <dt className="text-faint">{key}</dt>
                <dd className="mt-1 break-all font-mono text-ink">{value ?? 'UNAVAILABLE'}</dd>
              </div>)}
            </dl>
          </Panel>
          <Panel eyebrow="NET EQUITY" title="定频净权益">
            <div className="max-h-72 overflow-auto text-xs">
              <table className="w-full text-left"><thead><tr><th>UTC</th><th>USDT</th></tr></thead>
                <tbody>{(technical.series.net_equity ?? []).map(point => <tr key={point.label}>
                  <td>{new Date(Number(point.label)).toISOString()}</td><td>{point.value}</td>
                </tr>)}</tbody>
              </table>
            </div>
          </Panel>
          <Panel eyebrow="AUDIT" title="订单、拒绝原因与适用边界">
            <ul className="space-y-2 text-xs">{technical.caveats.map((line, index) =>
              <li key={index} className="break-all font-mono">{line}</li>)}</ul>
          </Panel>
        </>}
      </LoadingVeil>
    </PageShell>
  );
}
