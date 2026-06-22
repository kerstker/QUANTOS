import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchEvidenceLedgerSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { EvidenceLedgerEntry, EvidenceLedgerPayload } from '@/lib/types/research';

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

function formatTime(seconds: number) {
  return new Date(seconds * 1000).toISOString().slice(0, 16).replace('T', ' ');
}

function shortHash(hash: string) {
  if (hash.length <= 16) {
    return hash;
  }
  return `${hash.slice(0, 8)}...${hash.slice(-6)}`;
}

function ChainRail({entries}: {entries: EvidenceLedgerEntry[]}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('哈希链', 'Hash Chain')}</h2>
      <div className="space-y-3">
        {entries.map(entry => (
          <div key={entry.evidence_id} className="grid grid-cols-[2.5rem_1fr] gap-3 text-xs">
            <div className="flex flex-col items-center">
              <div className="flex h-7 w-7 items-center justify-center rounded-full border border-cyan-700 bg-cyan-950/40 font-bold text-cyan-300">{entry.sequence}</div>
              {entry.sequence < entries.length && <div className="h-10 w-px bg-slate-700" />}
            </div>
            <div className="rounded border border-slate-800 bg-slate-950/60 p-3">
              <div className="flex flex-col gap-1 md:flex-row md:items-start md:justify-between">
                <div>
                  <div className="font-bold text-slate-100">{entry.source_module}</div>
                  <div className="mt-1 text-slate-500">{entry.evidence_id} / {entry.artifact_type}</div>
                </div>
                <span className={entry.blocks_live_authorization ? 'text-red-300' : 'text-emerald-300'}>{entry.blocks_live_authorization ? 'BLOCKS_LIVE' : 'NON_BLOCKING'}</span>
              </div>
              <div className="mt-2 grid gap-1 text-[10px] text-slate-500 md:grid-cols-2">
                <div>prev: {shortHash(entry.previous_hash)}</div>
                <div>hash: {shortHash(entry.ledger_hash)}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PolicyBoard({ledger}: {ledger: EvidenceLedgerPayload}) {
  const policies: Array<[string, boolean]> = [
    ['APPEND_ONLY', ledger.ledger_policy.append_only],
    ['PREVIOUS_HASH', ledger.ledger_policy.requires_previous_hash],
    ['CONTENT_HASH', ledger.ledger_policy.requires_content_hash],
    ['SOURCE_ENDPOINT', ledger.ledger_policy.requires_source_endpoint],
    ['READ_ONLY_SOURCE', ledger.ledger_policy.requires_read_only_source],
    ['IMMUTABLE_ENTRIES', !ledger.ledger_policy.mutable_entries],
    ['NO_LIVE_AUTHORIZATION', !ledger.ledger_policy.authorizes_live_trading],
  ];
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('证据策略', 'Ledger Policy')}</h2>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        {policies.map(([label, passed]) => (
          <div key={label} className="flex items-center justify-between gap-2 rounded bg-slate-800 px-3 py-2">
            <span className="text-slate-300">{label}</span>
            <span className={passed ? 'text-emerald-300' : 'text-red-300'}>{passed ? 'PASS' : 'FAIL'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function BlockerBoard({ledger}: {ledger: EvidenceLedgerPayload}) {
  return (
    <div className="bg-red-950/30 border border-red-900/50 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-red-300">{bi('授权阻断证据', 'Authorization Blockers')}</h2>
      <div className="space-y-3 text-xs">
        {ledger.blockers.map(blocker => (
          <div key={blocker.evidence_id} className="rounded border border-red-900/50 bg-slate-950/50 p-3">
            <div className="font-bold text-red-200">{blocker.evidence_id} / {blocker.source_module}</div>
            <div className="mt-1 text-red-100">{blocker.reason}</div>
            <div className="mt-2 font-mono text-red-300">{shortHash(blocker.ledger_hash)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function EvidenceLedger() {
  const [payload, setPayload] = useState<EvidenceLedgerPayload | null>(null);
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
    fetchEvidenceLedgerSnapshot(controller.signal)
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
  const ledger = usingBackend && payload ? payload : fallbackLedger;
  const artifactCounts = useMemo(() => ledger.entries.reduce<Record<string, number>>((acc, entry) => {
    acc[entry.artifact_type] = (acc[entry.artifact_type] ?? 0) + 1;
    return acc;
  }, {}), [ledger.entries]);

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('不可变证据链', 'Immutable Evidence Ledger')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision="L2_DRY_RUN_ONLY" />
              <DecisionBadge decision={ledger.ledger_mode} />
              <DecisionBadge decision={ledger.summary.live_authorization_allowed ? 'APPROVED' : 'NO_GO'} />
            </div>
          </div>
          <div className="text-xs text-slate-500 lg:text-right">
            <div>{bi('链 ID', 'Chain ID')}: {ledger.chain_id}</div>
            <div>root_hash: {shortHash(ledger.root_hash)}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={ledger.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('条目', 'Entries')} value={ledger.summary.entries} />
        <MetricCard label={bi('已封存', 'Sealed')} value={ledger.summary.sealed_entries} />
        <MetricCard label={bi('阻断项', 'Blockers')} value={ledger.summary.blocking_entries} />
        <MetricCard label={bi('链有效', 'Chain Valid')} value={ledger.summary.hash_chain_valid ? 'YES' : 'NO'} />
        <MetricCard label={bi('只读条目', 'Read-only')} value={`${ledger.summary.read_only_entries}/${ledger.summary.entries}`} />
        <MetricCard label={bi('产物类型', 'Artifacts')} value={Object.keys(artifactCounts).length} />
        <MetricCard label={bi('实盘授权', 'Live Auth')} value={ledger.summary.live_authorization_allowed ? 'YES' : 'NO'} />
        <MetricCard label={bi('根哈希', 'Root Hash')} value={shortHash(ledger.root_hash)} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <ChainRail entries={ledger.entries} />
        <div className="space-y-4">
          <BlockerBoard ledger={ledger} />
          <PolicyBoard ledger={ledger} />
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[1160px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">#</th>
              <th className="p-2 text-left">{bi('证据', 'Evidence')}</th>
              <th className="p-2 text-left">{bi('来源', 'Source')}</th>
              <th className="p-2 text-left">{bi('类型', 'Type')}</th>
              <th className="p-2 text-right">{bi('记录', 'Records')}</th>
              <th className="p-2 text-left">{bi('状态', 'Status')}</th>
              <th className="p-2 text-left">{bi('封存时间', 'Sealed')}</th>
              <th className="p-2 text-left">Previous</th>
              <th className="p-2 text-left">Ledger Hash</th>
              <th className="p-2 text-left">{bi('阻断', 'Blocks')}</th>
            </tr>
          </thead>
          <tbody>
            {ledger.entries.map(entry => (
              <tr key={entry.evidence_id} className="border-b border-slate-800/50">
                <td className="p-2 font-bold text-cyan-300">{entry.sequence}</td>
                <td className="p-2">
                  <div className="font-bold text-slate-200">{entry.evidence_id}</div>
                  <div className="text-slate-500">{entry.owner}</div>
                </td>
                <td className="p-2">
                  <div className="text-slate-300">{entry.source_module}</div>
                  <div className="text-slate-500">{entry.source_endpoint}</div>
                </td>
                <td className="p-2 text-cyan-300">{entry.artifact_type}</td>
                <td className="p-2 text-right text-slate-300">{entry.record_count}</td>
                <td className="p-2 text-emerald-300">{entry.status}</td>
                <td className="p-2 text-slate-400">{formatTime(entry.sealed_at)}</td>
                <td className="p-2 font-mono text-slate-500">{shortHash(entry.previous_hash)}</td>
                <td className="p-2 font-mono text-slate-500">{shortHash(entry.ledger_hash)}</td>
                <td className={entry.blocks_live_authorization ? 'p-2 text-red-300' : 'p-2 text-emerald-300'}>{entry.blocks_live_authorization ? 'YES' : 'NO'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
