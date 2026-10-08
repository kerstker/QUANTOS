import type { RuntimeContractPayload } from '@/lib/types/research';
import { useMftReadModel } from './useMftReadModel';

interface RuntimeState {
  payload: RuntimeContractPayload | null;
  apiState: string;
  error: string | null;
}

function stringValue(section: Record<string, unknown> | undefined, key: string): string | null {
  const value = section?.[key];
  return typeof value === 'string' && value.trim() ? value : null;
}

function alertSummary(snapshot: ReturnType<typeof useMftReadModel>['snapshot']): string {
  if (!snapshot) return '告警 UNKNOWN';
  let count = 0;
  if (snapshot.market_gap.status !== 'CONTIGUOUS' && snapshot.market_gap.status !== 'READY') count += 1;
  if (snapshot.risk.status !== 'PASS' && snapshot.risk.status !== 'READY') count += 1;
  if (snapshot.risk.kill_switch_latched === true) count += 1;
  if (snapshot.reconciliation.status !== 'RECONCILED' && snapshot.reconciliation.status !== 'READY') count += 1;
  return `告警 ${count}`;
}

function verifiedControlPlane(runtime: RuntimeState | undefined): boolean {
  const payload = runtime?.payload;
  return Boolean(
    payload
      && payload.safety_boundary.read_only_console
      && payload.safety_boundary.authorized_level === 'L2_DRY_RUN_ONLY'
      && payload.safety_boundary.live_trading_authorized === false
      && payload.safety_boundary.order_submission_allowed === false
      && payload.safety_boundary.broker_connectivity === false,
  );
}

export function GlobalStatusBar({ runtime }: { runtime?: RuntimeState }) {
  const state = useMftReadModel();
  const snapshot = state.snapshot;
  const controlVerified = verifiedControlPlane(runtime);
  const venue = stringValue(snapshot?.market_gap, 'venue') ?? 'UNKNOWN';
  const account = stringValue(snapshot?.market_gap, 'account') ?? 'UNKNOWN';
  const freshness = snapshot?.freshness.status ?? 'UNAVAILABLE';
  const watermark = snapshot?.watermark?.sequence;
  const mandate = stringValue(snapshot?.mandate_expiry, 'status') ?? 'UNKNOWN';
  const killSwitch = snapshot?.risk.kill_switch_latched === false
    ? '熔断正常'
    : snapshot?.risk.kill_switch_latched === true
      ? '熔断已触发'
      : '熔断 UNKNOWN';

  const itemClass = 'whitespace-nowrap rounded border border-line/80 bg-inset/55 px-2 py-1';
  if (!snapshot && controlVerified) {
    const execution = state.reason === 'STORE_MISSING'
      ? 'Testnet执行 NOT_PROVISIONED'
      : `Testnet执行 ${state.phase}`;
    return (
      <div
        role="status"
        aria-label="全局运行状态"
        className="flex min-h-9 flex-wrap items-center gap-1.5 border-t border-line/60 bg-surface/90 px-3 py-1.5 text-[10px] font-semibold tracking-wide text-muted sm:px-6"
      >
        <span className={`${itemClass} text-warn`}>L2_DRY_RUN_ONLY</span>
        <span className={`${itemClass} text-pos`}>控制面 ONLINE · CONTRACT VERIFIED</span>
        <span className={`${itemClass} text-info`}>研究与数据面独立运行</span>
        <span className={`${itemClass} text-warn`}>{execution}</span>
        <span className={itemClass}>{state.reason ?? 'EXECUTION_AUTHORITY_UNAVAILABLE'}</span>
        <span className={`${itemClass} text-warn`}>强身份未验证</span>
      </div>
    );
  }
  return (
    <div
      role="status"
      aria-label="全局运行状态"
      className="flex min-h-9 flex-wrap items-center gap-1.5 border-t border-line/60 bg-surface/90 px-3 py-1.5 text-[10px] font-semibold tracking-wide text-muted sm:px-6"
    >
      <span className={`${itemClass} text-warn`}>L2_DRY_RUN_ONLY</span>
      <span className={itemClass}>{venue} · {account}</span>
      <span className={itemClass}>{freshness}</span>
      <span className={itemClass}>watermark {watermark ?? 'UNKNOWN'}</span>
      <span className={itemClass}>{killSwitch}</span>
      <span className={itemClass}>mandate {mandate}</span>
      <span className={itemClass}>{alertSummary(snapshot)}</span>
      <span className={`${itemClass} text-warn`}>强身份未验证</span>
      <span className={itemClass}>{state.phase} · {state.transport}</span>
    </div>
  );
}
