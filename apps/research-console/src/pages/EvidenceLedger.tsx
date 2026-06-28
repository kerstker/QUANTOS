import React, { useMemo } from 'react';
import { fetchEvidenceLedgerSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { EvidenceLedgerEntry, EvidenceLedgerPayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, Chip, KeyValue, type Column,
} from '@/ui/primitives';
import { Histogram, Donut } from '@/ui/charts';
import { num, shortHash, stamp, type Tone } from '@/ui/format';

const fallbackLedger: EvidenceLedgerPayload = {
  source: 'FRONTEND_EVIDENCE_LEDGER_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  ledger_mode: 'READ_ONLY_IMMUTABLE_EVIDENCE_LEDGER',
  chain_id: 'quantos-research-ledger-fallback',
  root_hash: 'fallback-root-hash',
  summary: {
    entries: 3,
    sealed_entries: 3,
    blocking_entries: 1,
    hash_chain_valid: true,
    read_only_entries: 3,
    live_authorization_allowed: false,
  },
  entries: [
    {
      evidence_id: 'EV-EXP-REGISTRY-001',
      source_module: 'Experiment Registry',
      source_endpoint: '/api/v1/console/experiments',
      artifact_type: 'EXPERIMENT_REGISTRY',
      record_count: 3,
      status: 'SEALED',
      owner: 'Alpha Research',
      content_hash: 'fallback-exp-content',
      blocks_live_authorization: false,
      sequence: 1,
      sealed_at: 1_781_222_400,
      previous_hash: 'GENESIS',
      ledger_hash: 'fallback-ledger-001',
      immutability_status: 'IMMUTABLE_RESEARCH_SNAPSHOT',
      read_only: true,
      execution_mode: 'L2_DRY_RUN_ONLY',
      verification: {hash_chain_valid: true, content_hash_present: true, source_read_only: true, no_live_authorization: true},
    },
    {
      evidence_id: 'EV-MODEL-RISK-001',
      source_module: 'Model Risk Review',
      source_endpoint: '/api/v1/console/model-risk-review',
      artifact_type: 'MODEL_RISK_REVIEW',
      record_count: 3,
      status: 'SEALED',
      owner: 'Model Governance',
      content_hash: 'fallback-mrm-content',
      blocks_live_authorization: true,
      sequence: 2,
      sealed_at: 1_781_222_700,
      previous_hash: 'fallback-ledger-001',
      ledger_hash: 'fallback-ledger-002',
      immutability_status: 'IMMUTABLE_RESEARCH_SNAPSHOT',
      read_only: true,
      execution_mode: 'L2_DRY_RUN_ONLY',
      verification: {hash_chain_valid: true, content_hash_present: true, source_read_only: true, no_live_authorization: true},
    },
    {
      evidence_id: 'EV-PHASE3A-001',
      source_module: 'Phase 3A Governance',
      source_endpoint: '/api/v1/console/phase-3a-readiness',
      artifact_type: 'GO_NO_GO_EVIDENCE',
      record_count: 15,
      status: 'SEALED',
      owner: 'Governance',
      content_hash: 'fallback-phase3a-content',
      blocks_live_authorization: true,
      sequence: 3,
      sealed_at: 1_781_223_000,
      previous_hash: 'fallback-ledger-002',
      ledger_hash: 'fallback-root-hash',
      immutability_status: 'IMMUTABLE_RESEARCH_SNAPSHOT',
      read_only: true,
      execution_mode: 'L2_DRY_RUN_ONLY',
      verification: {hash_chain_valid: true, content_hash_present: true, source_read_only: true, no_live_authorization: true},
    },
  ],
  blockers: [
    {evidence_id: 'EV-PHASE3A-001', source_module: 'Phase 3A Governance', reason: 'Evidence blocks live authorization under current governance state', ledger_hash: 'fallback-root-hash'},
  ],
  ledger_policy: {
    append_only: true,
    requires_previous_hash: true,
    requires_content_hash: true,
    requires_source_endpoint: true,
    requires_read_only_source: true,
    mutable_entries: false,
    authorizes_live_trading: false,
  },
};

/** The four per-entry verification invariants — labelled bilingually. */
const VERIFICATION_KEYS: Array<[keyof EvidenceLedgerEntry['verification'], string]> = [
  ['hash_chain_valid', bi('链', 'CHAIN')],
  ['content_hash_present', bi('内容', 'HASH')],
  ['source_read_only', bi('只读', 'R/O')],
  ['no_live_authorization', bi('无授权', 'NO-LIVE')],
];

export default function EvidenceLedger() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<EvidenceLedgerPayload>(
    s => fetchEvidenceLedgerSnapshot(s));

  const ledger = usingBackend ? payload! : fallbackLedger;
  const { summary, ledger_policy, entries, blockers } = ledger;

  const artifactBins = useMemo(() => {
    const counts = entries.reduce<Record<string, number>>((acc, e) => {
      acc[e.artifact_type] = (acc[e.artifact_type] ?? 0) + 1;
      return acc;
    }, {});
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([label, count]) => ({ label: label.replace(/_/g, ' ').slice(0, 14), count }));
  }, [entries]);

  const blocking = entries.filter(e => e.blocks_live_authorization).length;
  const nonBlocking = entries.length - blocking;
  const composition = [
    { label: bi('非阻断', 'Non-blocking'), value: nonBlocking, tone: 'pos' as Tone },
    { label: bi('阻断', 'Blocking'), value: blocking, tone: 'neg' as Tone },
  ];

  const policies: Array<[React.ReactNode, boolean]> = [
    [bi('仅追加', 'APPEND_ONLY'), ledger_policy.append_only],
    [bi('前序哈希', 'PREVIOUS_HASH'), ledger_policy.requires_previous_hash],
    [bi('内容哈希', 'CONTENT_HASH'), ledger_policy.requires_content_hash],
    [bi('来源端点', 'SOURCE_ENDPOINT'), ledger_policy.requires_source_endpoint],
    [bi('只读来源', 'READ_ONLY_SOURCE'), ledger_policy.requires_read_only_source],
    [bi('不可变条目', 'IMMUTABLE_ENTRIES'), !ledger_policy.mutable_entries],
    [bi('禁止实盘授权', 'NO_LIVE_AUTHORIZATION'), !ledger_policy.authorizes_live_trading],
  ];

  const entryCols: Column<EvidenceLedgerEntry>[] = [
    { key: 'seq', header: '#', align: 'right', mono: true, width: '40px',
      render: e => <span className="text-accent font-semibold">{e.sequence}</span> },
    { key: 'evidence', header: bi('证据', 'Evidence'), render: e => (
      <div className="min-w-0">
        <div className="font-medium text-ink">{e.evidence_id}</div>
        <div className="text-2xs text-faint">{e.owner}</div>
      </div>
    ) },
    { key: 'source', header: bi('来源模块', 'Source Module'), render: e => (
      <div className="min-w-0">
        <div className="text-ink">{e.source_module}</div>
        <div className="mono text-2xs text-faint truncate">{e.source_endpoint}</div>
      </div>
    ) },
    { key: 'artifact', header: bi('产物类型', 'Artifact'), render: e => <Chip label={e.artifact_type} tone="info" /> },
    { key: 'records', header: bi('记录', 'Records'), align: 'right', mono: true, render: e => num(e.record_count) },
    { key: 'status', header: bi('状态', 'Status'), render: e => <StatusChip value={e.status} /> },
    { key: 'immut', header: bi('不可变性', 'Immutability'), render: e => (
      <div className="flex items-center gap-1.5">
        <GateDot ok={e.read_only} />
        <span className="text-2xs text-muted">{e.immutability_status.replace(/_/g, ' ')}</span>
      </div>
    ) },
    { key: 'chash', header: bi('内容哈希', 'Content Hash'), mono: true, render: e => (
      <span className="text-muted">{shortHash(e.content_hash)}</span>
    ) },
    { key: 'lhash', header: bi('链哈希', 'Ledger Hash'), mono: true, render: e => (
      <div className="text-2xs leading-tight">
        <div className="text-faint">↑ {shortHash(e.previous_hash, 6)}</div>
        <div className="text-ink">= {shortHash(e.ledger_hash, 6)}</div>
      </div>
    ) },
    { key: 'sealed', header: bi('封存时间', 'Sealed'), mono: true, render: e => (
      <span className="text-muted">{stamp(e.sealed_at)}</span>
    ) },
    { key: 'verify', header: bi('校验', 'Verification'), render: e => (
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        {VERIFICATION_KEYS.map(([k, label]) => (
          <GateDot key={String(k)} ok={e.verification[k]} label={label} />
        ))}
      </div>
    ) },
    { key: 'blocks', header: bi('阻断实盘', 'Blocks Live'), align: 'center', render: e => (
      e.blocks_live_authorization
        ? <Chip label={bi('阻断', 'BLOCKS')} tone="neg" />
        : <Chip label={bi('通过', 'CLEAR')} tone="pos" soft={false} />
    ) },
  ];

  const blockerCols: Column<EvidenceLedgerPayload['blockers'][number]>[] = [
    { key: 'id', header: bi('证据', 'Evidence'), render: b => (
      <span className="font-medium text-ink">{b.evidence_id}</span>
    ) },
    { key: 'module', header: bi('来源模块', 'Source Module'), render: b => b.source_module },
    { key: 'reason', header: bi('阻断原因', 'Reason'), render: b => (
      <span className="text-muted">{b.reason}</span>
    ) },
    { key: 'hash', header: bi('链哈希', 'Ledger Hash'), mono: true, align: 'right',
      render: b => <span className="text-muted">{shortHash(b.ledger_hash)}</span> },
  ];

  const chainTone: Tone = summary.hash_chain_valid ? 'pos' : 'neg';

  return (
    <PageShell>
      <PageHeader title="Evidence Ledger" titleZh="不可变证据链"
        subtitle={bi('仅追加、哈希链校验的研究证据账本 — 任何条目均不授权实盘交易',
          'Append-only, hash-chained research evidence — no entry authorizes live trading')}
        actions={
          <div className="flex items-center gap-2">
            <Chip label={ledger.ledger_mode} tone="info" />
            <Chip label={bi('根哈希', 'root')} tone="muted" />
            <span className="mono text-xs text-ink">{shortHash(ledger.root_hash, 14)}</span>
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={error}
        realData={(payload as { data_provenance?: string } | null | undefined)?.data_provenance === 'EXPERIMENT_REGISTRY'} />

      <KpiStrip cols={7}>
        <StatTile label={bi('哈希链', 'Hash Chain')}
          value={summary.hash_chain_valid ? bi('有效', 'VALID') : bi('失效', 'BROKEN')}
          tone={chainTone}
          sub={<GateDot ok={summary.hash_chain_valid} label={bi('完整性校验', 'integrity check')} />} />
        <StatTile label={bi('证据条目', 'Entries')} value={num(summary.entries)} accent />
        <StatTile label={bi('已封存', 'Sealed')} value={num(summary.sealed_entries)} tone="pos"
          sub={bi(`共 ${summary.entries} 条`, `of ${summary.entries}`)} />
        <StatTile label={bi('阻断项', 'Blockers')} value={num(summary.blocking_entries)} tone="neg" />
        <StatTile label={bi('只读条目', 'Read-only')}
          value={`${num(summary.read_only_entries)}/${num(summary.entries)}`} tone="info" />
        <StatTile label={bi('产物类型', 'Artifact Types')} value={num(artifactBins.length)} />
        <StatTile label={bi('实盘授权', 'Live Auth')}
          value={summary.live_authorization_allowed ? bi('允许', 'ALLOWED') : bi('禁止', 'DENIED')}
          tone={summary.live_authorization_allowed ? 'neg' : 'pos'}
          sub={<GateDot ok={!summary.live_authorization_allowed} label={bi('安全边界', 'safety boundary')} />} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('产物分布', 'Provenance')}
          title={bi('证据产物类型分布', 'Evidence by Artifact Type')}
          right={<Chip label={bi(`${entries.length} 条目`, `${entries.length} entries`)} tone="info" />}>
          <Histogram bins={artifactBins} height={236} tone="info" />
        </Panel>

        <Panel eyebrow={bi('授权阻断', 'Authorization')} title={bi('阻断 vs 非阻断', 'Blocking Composition')}>
          <div className="flex items-center gap-4">
            <Donut segments={composition} size={140} />
            <div className="flex flex-1 flex-col gap-2">
              <KeyValue cols={1} items={[
                { k: <span className="text-pos">{bi('非阻断', 'Non-blocking')}</span>, v: num(nonBlocking) },
                { k: <span className="text-neg">{bi('阻断实盘', 'Blocking live')}</span>, v: num(blocking) },
                { k: bi('哈希链', 'Hash chain'), v: <GateDot ok={summary.hash_chain_valid} label={summary.hash_chain_valid ? 'VALID' : 'BROKEN'} /> },
              ]} />
            </div>
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('账本', 'Ledger')} title={bi('证据链条目', 'Evidence Chain Entries')}
        bodyClass="overflow-x-auto">
        <DataTable columns={entryCols} rows={entries} dense getKey={e => e.evidence_id} />
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('安全边界', 'Safety Boundary')}
          title={bi('授权阻断证据', 'Live-Authorization Blockers')} tone={blockers.length ? 'neg' : 'pos'}
          right={<Chip label={num(blockers.length)} tone={blockers.length ? 'neg' : 'pos'} />}>
          <DataTable columns={blockerCols} rows={blockers} getKey={b => b.evidence_id}
            empty={bi('无阻断项', 'No blockers')} />
        </Panel>

        <Panel eyebrow={bi('治理策略', 'Governance')} title={bi('账本策略不变量', 'Ledger Policy Invariants')}>
          <div className="flex flex-col gap-2">
            {policies.map(([label, ok], i) => (
              <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-inset/40 px-3 py-2">
                <span className="text-xs text-ink">{label}</span>
                <GateDot ok={ok} label={ok ? bi('通过', 'PASS') : bi('未通过', 'FAIL')} />
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </PageShell>
  );
}
