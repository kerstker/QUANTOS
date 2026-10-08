import { useMemo } from 'react';

import { fetchSystemCompletenessSnapshot } from '@/lib/api/research-console-api';
import type { SystemCompletenessPayload, TopTierRequirement } from '@/lib/types/research';
import { PageHeader, PageShell } from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';

const GOVERNED_PROVENANCE = 'TOP_TIER_EVIDENCE_ASSESSMENT';

const classificationLabel: Record<TopTierRequirement['classification'], string> = {
  L2_SAFE_NOW: 'NOW',
  NEEDS_EXTERNAL: 'EXTERNAL',
  POST_L2: 'AFTER L2',
};

const classificationOrder: Record<TopTierRequirement['classification'], number> = {
  L2_SAFE_NOW: 0,
  NEEDS_EXTERNAL: 1,
  POST_L2: 2,
};

function statusClass(status: string) {
  if (status === 'PASS') return 'border-pos/35 bg-pos/10 text-pos';
  if (status === 'FAILED') return 'border-neg/35 bg-neg/10 text-neg';
  return 'border-warn/35 bg-warn/10 text-warn';
}

function Blocker({ requirement }: { requirement: TopTierRequirement }) {
  return (
    <article className="rounded border border-line bg-inset/45 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded border px-2 py-0.5 font-mono text-[9px] font-semibold ${statusClass(requirement.status)}`}>
          {requirement.status}
        </span>
        <span className="rounded border border-line px-2 py-0.5 font-mono text-[9px] text-muted">
          {classificationLabel[requirement.classification]}
        </span>
        <span className="font-mono text-[9px] text-faint">
          {requirement.current_maturity} → {requirement.target_maturity}
        </span>
      </div>
      <p className="mt-2 break-words font-mono text-xs font-semibold text-ink">{requirement.id}</p>
      <p className="mt-1 text-xs leading-5 text-muted">{requirement.detail}</p>
      <p className="mt-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">Next controlled action</p>
      <p className="mt-1 text-xs leading-5 text-ink">{requirement.next_action}</p>
      {requirement.evidence.length > 0 && (
        <div className="mt-2 space-y-1">
          {requirement.evidence.map(item => (
            <p key={item} className="break-all font-mono text-[9px] text-info">{item}</p>
          ))}
        </div>
      )}
    </article>
  );
}

export function SystemCompletenessEvidence({ compact = false }: { compact?: boolean }) {
  const state = useConsoleSnapshot<SystemCompletenessPayload>(fetchSystemCompletenessSnapshot);
  const payload = state.payload;
  const verified = state.usingBackend
    && payload !== null
    && payload.data_provenance === GOVERNED_PROVENANCE;
  const blockers = useMemo(() => {
    if (!verified || payload === null) return [];
    return payload.requirements
      .filter(requirement => requirement.status !== 'PASS')
      .sort((left, right) => (
        classificationOrder[left.classification] - classificationOrder[right.classification]
        || left.id.localeCompare(right.id)
      ));
  }, [payload, verified]);

  if (!verified || payload === null) {
    return (
      <section
        aria-label="system completeness evidence"
        className="rounded-lg border border-warn/35 bg-warn/10 p-4"
      >
        <p className="font-mono text-[9px] font-semibold uppercase tracking-[0.15em] text-faint">
          System completeness
        </p>
        <p className="mt-2 font-mono text-xs font-semibold text-warn">SYSTEM_COMPLETENESS_UNVERIFIED</p>
        <p className="mt-2 text-xs leading-5 text-muted">
          受治理的顶级能力评估不可用。控制台不会推断、缓存或回退到手写完成度分数。
        </p>
        <p className="mt-2 break-all font-mono text-[9px] text-faint">
          {state.error ?? state.apiState}
        </p>
      </section>
    );
  }

  return (
    <section
      aria-label="system completeness evidence"
      className="rounded-lg border border-line bg-panel/70 p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[9px] font-semibold uppercase tracking-[0.15em] text-info">
            {GOVERNED_PROVENANCE}
          </p>
          <h3 className="mt-1 text-sm font-semibold text-ink">系统完整性证据</h3>
        </div>
        <span className={`rounded border px-2 py-1 font-mono text-[9px] font-semibold ${statusClass(payload.assessment.verdict)}`}>
          {payload.assessment.verdict}
        </span>
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-2">
        <div className="rounded border border-line bg-inset/45 p-2">
          <dt className="text-[9px] uppercase tracking-[0.12em] text-faint">Verified</dt>
          <dd className="mt-1 font-mono text-sm font-semibold text-ink">
            {payload.summary.passed}/{payload.summary.total}
          </dd>
        </div>
        <div className="rounded border border-line bg-inset/45 p-2">
          <dt className="text-[9px] uppercase tracking-[0.12em] text-faint">Open</dt>
          <dd className="mt-1 font-mono text-sm font-semibold text-warn">{blockers.length}</dd>
        </div>
        <div className="rounded border border-line bg-inset/45 p-2">
          <dt className="text-[9px] uppercase tracking-[0.12em] text-faint">Red lines</dt>
          <dd className={`mt-1 font-mono text-sm font-semibold ${payload.summary.red_lines_failed ? 'text-neg' : 'text-pos'}`}>
            {payload.summary.red_lines_failed}
          </dd>
        </div>
      </dl>

      <p className="mt-3 text-xs leading-5 text-muted">
        仅展示后端评估制品中的要求计数、证据和下一受控动作；不计算主观完成百分比。
      </p>

      <details className="mt-4 rounded border border-line bg-inset/20" open={!compact}>
        <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-ink">
          受控阻断队列 · {blockers.length}
        </summary>
        <div className="space-y-2 border-t border-line p-3">
          {blockers.map(requirement => <Blocker key={requirement.id} requirement={requirement} />)}
          {blockers.length === 0 && (
            <p className="rounded border border-pos/35 bg-pos/10 p-3 text-xs text-pos">没有未关闭要求。</p>
          )}
        </div>
      </details>

      {!compact && (
        <details className="mt-3 rounded border border-line bg-inset/20">
          <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-ink">
            仓库证据清单
          </summary>
          <dl className="grid grid-cols-2 gap-2 border-t border-line p-3 text-xs sm:grid-cols-3">
            <div><dt className="text-faint">服务模块</dt><dd className="font-mono text-ink">{payload.inventory.total_services_modules}</dd></div>
            <div><dt className="text-faint">服务代码行</dt><dd className="font-mono text-ink">{payload.inventory.total_services_loc}</dd></div>
            <div><dt className="text-faint">测试函数</dt><dd className="font-mono text-ink">{payload.inventory.n_test_functions}</dd></div>
            <div><dt className="text-faint">测试文件</dt><dd className="font-mono text-ink">{payload.inventory.n_test_files}</dd></div>
            <div><dt className="text-faint">控制台页面</dt><dd className="font-mono text-ink">{payload.inventory.n_console_pages}</dd></div>
            <div><dt className="text-faint">控制台端点</dt><dd className="font-mono text-ink">{payload.inventory.n_console_endpoints}</dd></div>
          </dl>
        </details>
      )}
    </section>
  );
}

export default function SystemCompleteness() {
  return (
    <PageShell>
      <PageHeader
        title="System Completeness Audit"
        titleZh="系统完整性审查"
        subtitle="机器证据是唯一真源；未验证来源不会产生完成度分数。"
      />
      <SystemCompletenessEvidence />
    </PageShell>
  );
}
