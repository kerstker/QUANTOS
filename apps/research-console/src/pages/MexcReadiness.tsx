import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, LoadingVeil,
  DataTable, StatusChip, GateDot, Chip, type Column,
} from '@/ui/primitives';
import { EndpointFootnote, MandateGateChip, StatusBanner } from '@/ui/workbench';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchMexcReadinessSnapshot } from '@/lib/api/research-console-api';
import type { MexcReadinessCheck, MexcReadinessPayload } from '@/lib/types/research';

/**
 * MEXC 就绪 — live-readiness checklist, read-only from /api/v1/console/mexc-readiness.
 * Honest boundary: all-READY only means "materials for human review exist" — it
 * never grants live capability. mandate/attestation are existence checks only;
 * MEXC has no spot testnet, so the graduation path is dry-run/paper → tiny real
 * size under a multi-party human mandate → scale.
 */

const mockChecks: MexcReadinessCheck[] = [
  { id: 'adapter_exists', label: 'MEXC 现货适配器 live/exchange/mexc_spot.py', status: 'READY', reason: 'mock' },
  { id: 'adapter_governance_test', label: '适配器治理边界测试', status: 'READY', reason: 'mock' },
  { id: 'human_mandate', label: '人工签署 mandate(仅存在性)', status: 'BLOCKED', reason: 'mock' },
  { id: 'validation_attestation', label: '验证 attestation(仅存在性)', status: 'BLOCKED', reason: 'mock' },
  { id: 'kill_switch_clear', label: 'Kill-switch 未跳闸', status: 'READY', reason: 'mock' },
  { id: 'market_data_fresh', label: 'MEXC 行情数据新鲜度', status: 'BLOCKED', reason: 'mock' },
  { id: 'dry_run_evidence', label: 'btc_spot_trend 干跑执行证据', status: 'BLOCKED', reason: 'mock' },
  { id: 'live_flag_disarmed', label: 'live_trading_armed 默认关闭', status: 'READY', reason: 'mock' },
];

const mockPayload: MexcReadinessPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REAL_MEXC_READINESS_CHECK', snapshot_present: true, verdict: 'BLOCKED',
  venue: 'MEXC', readiness: 'L2_DRY_RUN_ONLY',
  n_ready: 3, n_blocked: 4, checks: mockChecks,
  kill_switch: { tripped: false, reason: null },
  honesty_note: 'Mock fixture — the real checklist is computed live by the backend. All-READY never grants live capability.',
};

/** MEXC 无现货测试网 → 毕业路径四阶段(文档 docs/MEXC_INTEGRATION.md §8)。 */
const GRADUATION_STAGES = [
  { id: 'dry_run', zh: '干跑执行证据', en: 'dry-run evidence' },
  { id: 'paper', zh: '前向 paper-track', en: 'forward paper-track' },
  { id: 'tiny_live', zh: '小额真实 + 人工 mandate', en: 'tiny real + human mandate' },
  { id: 'scale', zh: '规模化(新多方批准)', en: 'scale (new multi-party approval)' },
] as const;

export default function MexcReadiness() {
  const { payload, apiState, error, usingBackend, snapshotState } = useConsoleSnapshot<MexcReadinessPayload>(
    s => fetchMexcReadinessSnapshot(s));
  const p: MexcReadinessPayload = usingBackend ? payload! : mockPayload;
  const check = (id: string) => p.checks.find(c => c.id === id);
  const ready = (id: string) => check(id)?.status === 'READY';

  // Graduation path: stage 1 needs dry-run evidence; stage 2+ honestly not yet
  // tracked by the checklist (no forward-track check exists yet) → always pending.
  const stageState: Record<string, 'done' | 'current' | 'pending'> = {
    dry_run: ready('dry_run_evidence') ? 'done' : 'current',
    paper: ready('dry_run_evidence') ? 'current' : 'pending',
    tiny_live: 'pending',
    scale: 'pending',
  };

  const checkCols: Column<MexcReadinessCheck>[] = [
    { key: 'state', header: '', width: '24px', render: c => <GateDot ok={c.status === 'READY'} /> },
    { key: 'label', header: bi('检查项', 'Check'), render: c => <span className="text-ink">{c.label}</span> },
    { key: 'status', header: bi('状态', 'Status'), render: c => <StatusChip value={c.status} /> },
    { key: 'reason', header: bi('依据', 'Reason'), render: c => <span className="text-2xs text-muted">{c.reason}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="MEXC Readiness" titleZh="MEXC 就绪"
        subtitle={bi('实盘就绪清单 · 全部 READY 也只是"具备提交人工评审的材料",绝不授予任何实盘能力',
          'Live-readiness checklist · all-READY only means "materials for human review exist" — it never grants live capability')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_MEXC_READINESS_CHECK'}
        source={usingBackend ? 'REAL_MEXC_READINESS_CHECK' : 'FRONTEND_MEXC_READINESS_MOCK'}
        error={error}
        dataStatus={snapshotState} />
      <StatusBanner />

      <LoadingVeil active={apiState === 'loading'}>
        <div className="flex flex-wrap items-center gap-3">
          <StatusChip value={p.verdict} />
          <Chip label={p.readiness} tone="info" />
          <MandateGateChip state={ready('human_mandate') ? 'SIGNED' : 'UNSIGNED'} />
          <Chip
            label={ready('validation_attestation') ? 'ATTESTATION_PRESENT' : 'ATTESTATION_MISSING'}
            tone={ready('validation_attestation') ? 'pos' : 'warn'} />
          <span className={`text-2xs ${p.kill_switch.tripped ? 'text-neg' : 'text-pos'}`}>
            {p.kill_switch.tripped
              ? bi(`kill-switch TRIPPED(${p.kill_switch.reason ?? '?'})`, `kill-switch TRIPPED (${p.kill_switch.reason ?? '?'})`)
              : bi('kill-switch CLEAR', 'kill-switch CLEAR')}
          </span>
        </div>

        <KpiStrip cols={3}>
          <StatTile label="READY" value={String(p.n_ready)} tone="pos" accent />
          <StatTile label="BLOCKED" value={String(p.n_blocked)} tone={p.n_blocked > 0 ? 'neg' : 'muted'} />
          <StatTile label={bi('检查总数', 'Total checks')} value={String(p.checks.length)} tone="muted" />
        </KpiStrip>

        <Panel eyebrow={bi('就绪清单', 'CHECKLIST')} title={bi('逐项检查(存在性检查,绝不读取 mandate/attestation 内容)',
          'Per-item checks (existence only — mandate/attestation contents are never read)')}>
          <DataTable rows={p.checks} columns={checkCols} getKey={c => c.id} density="compact" />
        </Panel>

        <Panel eyebrow={bi('毕业路径', 'GRADUATION PATH')} title={bi('MEXC 无现货测试网 —— 四阶段人工毕业',
          'No MEXC spot testnet — four-stage human graduation')}>
          <div className="flex flex-wrap items-stretch gap-1.5">
            {GRADUATION_STAGES.map((s, i) => {
              const st = stageState[s.id];
              const tone = st === 'done' ? 'var(--pos)' : st === 'current' ? 'var(--warn)' : 'var(--faint)';
              return (
                <React.Fragment key={s.id}>
                  <div className="min-w-[140px] flex-1 rounded-md border px-2.5 py-2"
                    style={{
                      borderColor: `color-mix(in srgb, ${tone} 35%, transparent)`,
                      background: `color-mix(in srgb, ${tone} ${st === 'done' ? 8 : 4}%, transparent)`,
                    }}>
                    <div className="text-2xs" style={{ color: tone }}>
                      {i + 1}. {st === 'done' ? '✓' : st === 'current' ? '◐' : '○'} {bi(s.zh, s.en)}
                    </div>
                    <div className="mt-1 text-3xs text-faint">
                      {st === 'done' ? bi('已完成', 'done')
                        : st === 'current' ? bi('当前阶段', 'current stage')
                          : bi('待解锁', 'pending')}
                    </div>
                  </div>
                  {i < GRADUATION_STAGES.length - 1 && <div className="self-center text-muted">→</div>}
                </React.Fragment>
              );
            })}
          </div>
          <div className="mt-2 text-2xs text-muted">
            {bi('阶段 2(前向 paper-track)的就绪尚未被清单机器跟踪 —— 诚实标为当前/待解锁,不假装完成。任何阶段跨越都是人工动作(手写 mandate + attestation + 显式 arm),AI 绝不触碰凭证、绝不下真实订单。',
              'Stage 2 (forward paper-track) is not yet machine-tracked by the checklist — honestly shown as current/pending, never faked as done. Every stage crossing is a human action (hand-written mandate + attestation + explicit arm); AI never touches credentials, never places real orders.')}
          </div>
        </Panel>

        <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
          {p.honesty_note}
        </div>
        <EndpointFootnote path="/api/v1/console/mexc-readiness"
          note={bi('只读 · kill-switch 读取 fail-closed', 'read-only · kill-switch read fail-closed')} />
      </LoadingVeil>
    </PageShell>
  );
}
