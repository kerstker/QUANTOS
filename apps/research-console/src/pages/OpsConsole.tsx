import React from 'react';
import { Link } from 'react-router-dom';
import { GROUPS } from '@/app/routes';
import { bi } from '@/lib/i18n/bilingual';
import { STAGE_AUTOMATION } from '@/ui/automation-map';
import { PageShell, PageHeader, Panel, Chip, SectionHeader, StatusChip } from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  fetchSafetyPrimitivesSnapshot, fetchGonogo, fetchGoLiveReadiness,
} from '@/lib/api/research-console-api';
import { governanceEnabled } from '@/lib/api/promotion-api';
import type {
  SafetyPrimitivesPayload, GonogoPayload, GoLiveReadinessPayload,
} from '@/lib/types/research';

/* ============================================================================
   OpsConsole (人工操作台, 2026-07-24) — the human counterpart of the pipeline.
   Two things on one screen:
     1. DIVISION OF LABOUR MAP — per lifecycle stage, which steps the SYSTEM
        runs automatically (fail-closed machinery, no human in the loop) and
        which gates structurally require REAL PEOPLE.
     2. HUMAN ACTION QUEUE — the live status of every human gate, each with a
        deep link to the console surface where the action is actually taken.
   This page routes and observes; mutating actions themselves stay inside
   their dedicated pages (promotion approval, kill-switch grant, go-live).
   ========================================================================== */

/* ---------- live status chips for the human queue ---------- */

function KillSwitchStatus() {
  const { payload, usingBackend, apiState } = useConsoleSnapshot<SafetyPrimitivesPayload>(
    s => fetchSafetyPrimitivesSnapshot(s));
  if (apiState === 'loading') return <Chip label={bi('加载中…', 'loading…')} tone="info" />;
  if (!usingBackend) return <Chip label={bi('未知(离线,fail-closed 姿态)', 'UNKNOWN (offline)')} tone="warn" />;
  return payload?.kill_switch?.is_active
    ? <StatusChip value="KILL_TRIPPED" />
    : <StatusChip value="KILL_CLEAR" />;
}

function GonogoStatus() {
  const { payload, usingBackend, apiState } = useConsoleSnapshot<GonogoPayload>(s => fetchGonogo(s));
  if (apiState === 'loading') return <Chip label={bi('加载中…', 'loading…')} tone="info" />;
  if (!usingBackend || !payload?.snapshot_present) return <Chip label="NO_SNAPSHOT" tone="warn" />;
  return <Chip label={payload.verdict} tone={payload.verdict === 'GONOGO_EVIDENCE_READY' ? 'pos' : 'warn'} />;
}

function GoLiveStatus() {
  const { payload, usingBackend, apiState } = useConsoleSnapshot<GoLiveReadinessPayload>(
    s => fetchGoLiveReadiness(s));
  if (apiState === 'loading') return <Chip label={bi('加载中…', 'loading…')} tone="info" />;
  if (!usingBackend || !payload?.snapshot_present) return <Chip label="NO_SNAPSHOT" tone="warn" />;
  return <Chip label={payload.readiness} tone="info" />;
}

/* ---------- human action queue entries ---------- */

interface QueueItem {
  titleZh: string; titleEn: string;
  descZh: string; descEn: string;
  to: string;
  actionZh: string;
  renderStatus: () => React.ReactNode;
}

const QUEUE: QueueItem[] = [
  {
    titleZh: '晋级审批(研究 → APPROVED)', titleEn: 'Promotion Approval',
    descZh: '三个不同真人走 发起→复核→批准→执行;非人身份/同人复用被状态机 fail-closed 拒绝。',
    descEn: 'Three distinct real identities: open → review → approve → execute; fail-closed.',
    to: '/research/promotion-queue', actionZh: '打开审批台',
    renderStatus: () => governanceEnabled()
      ? <Chip label={bi('治理签名已启用', 'GOV_AUTH_ENABLED')} tone="pos" />
      : <Chip label={bi('治理签名未配置', 'GOV_AUTH_DISABLED')} tone="warn" />,
  },
  {
    titleZh: 'Kill-Switch 跳闸 / RESET', titleEn: 'Kill-Switch',
    descZh: '跳闸即全拒(latched);RESET 必须持多方 grant,非人/单人到不了。',
    descEn: 'Tripped = reject-all (latched); RESET requires a multi-party grant.',
    to: '/research/risk', actionZh: '查看风险仪表盘',
    renderStatus: () => <KillSwitchStatus />,
  },
  {
    titleZh: 'GoNoGo 证据复核', titleEn: 'GoNoGo Evidence',
    descZh: '前向证据 + 执行证据 + 事故记录的真人复核点;不构成上线许可。',
    descEn: 'Human review of forward + execution evidence; not a go-live permit.',
    to: '/research/gonogo', actionZh: '复核证据',
    renderStatus: () => <GonogoStatus />,
  },
  {
    titleZh: '上线向导(毕业路径)', titleEn: 'Go-Live Wizard',
    descZh: '干跑 → paper-track → testnet → smoke → mandate+attestation → 小额首单;L2 下 LIVE 结构性封锁。',
    descEn: 'Dry-run → paper → testnet → smoke → mandate → first small ticket; LIVE blocked in L2.',
    to: '/research/go-live', actionZh: '打开向导',
    renderStatus: () => <GoLiveStatus />,
  },
];

/* ---------- division-of-labour map ---------- */

function StageLane({ id }: { id: (typeof GROUPS)[number]['id'] }) {
  const group = GROUPS.find(g => g.id === id)!;
  const spec = STAGE_AUTOMATION[id];
  return (
    <div className="terminal-panel flex min-w-0 flex-col rounded-lg">
      <div className="border-b border-line px-4 py-2.5">
        <span className="text-sm font-semibold text-ink">{group.zh}</span>
        <span className="ml-2 text-3xs uppercase tracking-[0.14em] text-faint">{group.en}</span>
      </div>
      {/* lane: system automation */}
      <div className="flex-1 px-4 py-3">
        <div className="mb-1.5 flex items-center gap-1.5 text-3xs font-semibold uppercase tracking-[0.14em] text-info">
          <span aria-hidden>⚙</span>{bi('系统自动化', 'SYSTEM AUTO')}
        </div>
        <ul className="flex flex-col gap-1.5">
          {spec.auto.map(step => (
            <li key={step.en} className="text-2xs leading-snug text-muted">
              {step.to
                ? <Link to={step.to} className="hover:text-ink hover:underline">{step.zh}</Link>
                : step.zh}
            </li>
          ))}
        </ul>
      </div>
      {/* lane: human gates */}
      <div className="flex-1 border-t border-line/60 px-4 py-3"
        style={spec.human.length ? { background: 'color-mix(in srgb, var(--warn) 4%, transparent)' } : undefined}>
        <div className="mb-1.5 flex items-center gap-1.5 text-3xs font-semibold uppercase tracking-[0.14em]"
          style={{ color: spec.human.length ? 'var(--warn)' : 'var(--faint)' }}>
          <span aria-hidden>👤</span>{bi('人工门', 'HUMAN GATES')}
        </div>
        {spec.human.length ? (
          <ul className="flex flex-col gap-1.5">
            {spec.human.map(step => (
              <li key={step.en} className="text-2xs leading-snug text-ink">
                {step.to
                  ? <Link to={step.to} className="hover:text-accent hover:underline">{step.zh} →</Link>
                  : step.zh}
              </li>
            ))}
          </ul>
        ) : (
          <span className="text-2xs text-faint">{bi('无 —— 该阶段全自动', 'none — fully automated')}</span>
        )}
      </div>
    </div>
  );
}

export default function OpsConsole() {
  return (
    <PageShell>
      <PageHeader title="Human Ops Console" titleZh="人工操作台"
        subtitle={bi('一张图看清分工:⚙ 系统自动化(fail-closed,无需人值守)与 👤 人工门(结构上必须真人)。下方队列给出每个人工门的实时状态;操作本身在对应页面完成,本台只路由与观察。',
          'The division of labour on one screen: ⚙ system automation (fail-closed, unattended) vs 👤 human gates (structurally require real people). The queue below shows live status per gate; actions themselves happen in the linked pages.')} />

      {/* ── 人工操作队列(实时状态) ── */}
      <SectionHeader title={bi('人工操作队列', 'HUMAN ACTION QUEUE')}
        sub={bi('需要真人的门 · 状态实时 · 点击进入操作面', 'gates requiring humans · live status · click through to act')} />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {QUEUE.map(item => (
          <Panel key={item.to} title={bi(item.titleZh, item.titleEn)}
            right={<item.renderStatus />}
            bodyClass="flex flex-col gap-3">
            <p className="text-2xs leading-relaxed text-muted">{bi(item.descZh, item.descEn)}</p>
            <Link to={item.to}
              className="mt-auto inline-flex w-fit items-center gap-1 rounded-md border border-accent/50 px-3 py-1.5 text-xs text-accent transition hover:bg-accent/10">
              {bi(item.actionZh, 'open')} →
            </Link>
          </Panel>
        ))}
      </div>

      {/* ── 分工地图:七阶段 × 双泳道 ── */}
      <SectionHeader title={bi('全流程分工地图', 'DIVISION OF LABOUR MAP')}
        sub={bi('每个阶段:系统跑什么(上)vs 人做什么(下,琥珀色)', 'per stage: what the system runs (top) vs what humans do (bottom, amber)')} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-7">
        {GROUPS.map(g => <StageLane key={g.id} id={g.id} />)}
      </div>

      <p className="text-2xs leading-relaxed text-faint">
        {bi('诚实边界:本图描述的是系统设计层面的真实分工(晋级=三方真人、RESET=多方 grant、L2 下 LIVE 结构性封锁),不是权限系统本身;自动化任务的运行状态见顶部管道各节点的实时读数。',
          'Honesty: this map describes the real design-level division of labour (promotion = 3 humans, RESET = multi-party grant, LIVE structurally blocked in L2) — it is not itself a permission system; automation health is on the pipeline nodes above.')}
      </p>
    </PageShell>
  );
}
