import { fetchRuntimeContract, getConsoleRuntimeMode } from '@/lib/api/research-console-api';
import type { RuntimeContractPayload } from '@/lib/types/research';
import { SystemCompletenessEvidence } from '@/pages/SystemCompleteness';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { CommandReviewPanel } from '../CommandReviewPanel';

function unavailable(apiState: string, error: string | null) {
  const mode = getConsoleRuntimeMode();
  const code = mode === 'MOCK' ? 'DEMO_MODE_NO_GOVERNANCE_AUTHORITY' : 'GOVERNANCE_UNAVAILABLE';
  return (
    <div className="rounded-lg border border-warn/35 bg-warn/10 p-4" role="status">
      <p className="font-mono text-xs font-semibold text-warn">{code}</p>
      <p className="mt-2 break-all font-mono text-[10px] text-muted">{error ?? apiState}</p>
      <p className="mt-3 text-xs leading-5 text-muted">未验证的浏览器状态不构成身份、授权或治理批准。</p>
    </div>
  );
}

function BoundaryRow({ label, blocked }: { label: string; blocked: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded border border-line bg-inset/45 px-3 py-2">
      <span className="text-xs text-muted">{label}</span>
      <span className={`font-mono text-[10px] font-semibold ${blocked ? 'text-pos' : 'text-neg'}`}>
        {blocked ? 'BLOCKED' : 'UNVERIFIED'}
      </span>
    </div>
  );
}

export function GovernanceDrawer() {
  const state = useConsoleSnapshot<RuntimeContractPayload>(fetchRuntimeContract);
  if (!state.usingBackend || state.payload === null) {
    return (
      <div className="space-y-4">
        {unavailable(state.apiState, state.error)}
        <SystemCompletenessEvidence compact />
        <CommandReviewPanel contractVerified={false} />
      </div>
    );
  }

  const payload = state.payload;
  const boundary = payload.safety_boundary;
  const verified = boundary.read_only_console
    && boundary.authorized_level === 'L2_DRY_RUN_ONLY'
    && boundary.live_trading_authorized === false
    && boundary.order_submission_allowed === false
    && boundary.broker_connectivity === false;

  return (
    <div className="space-y-4">
      <div className={`rounded-lg border p-4 ${verified ? 'border-pos/35 bg-pos/10' : 'border-neg/35 bg-neg/10'}`} role="status">
        <p className={`font-mono text-xs font-semibold ${verified ? 'text-pos' : 'text-neg'}`}>
          {verified ? 'GOVERNANCE_CONTRACT_VERIFIED' : 'GOVERNANCE_CONTRACT_REJECTED'}
        </p>
        <p className="mt-2 font-mono text-sm font-semibold text-ink">{boundary.authorized_level}</p>
        <p className="mt-1 break-all font-mono text-[10px] text-muted">
          {payload.contract_version} · instance {payload.instance_id} · build {payload.build_sha_known ? payload.build_sha.slice(0, 12) : 'UNKNOWN'}
        </p>
      </div>
      <div className="space-y-2">
        <BoundaryRow label="READ-ONLY CONSOLE" blocked={boundary.read_only_console} />
        <BoundaryRow label="LIVE TRADING" blocked={!boundary.live_trading_authorized} />
        <BoundaryRow label="ORDER SUBMISSION" blocked={!boundary.order_submission_allowed} />
        <BoundaryRow label="BROKER CONNECTIVITY" blocked={!boundary.broker_connectivity} />
      </div>
      <p className="font-mono text-[10px] text-faint">
        ORDER SUBMISSION BLOCKED · schema catalog {payload.event_schema_catalog.sha256.slice(0, 12)}
      </p>
      <SystemCompletenessEvidence compact />
      <CommandReviewPanel contractVerified={verified} />
    </div>
  );
}
