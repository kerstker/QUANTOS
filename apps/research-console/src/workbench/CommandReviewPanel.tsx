import { useMemo, useState } from 'react';

import {
  approvePromotionRequest,
  executePromotionRequest,
  getGovernanceAccessToken,
  governanceEnabled,
  openPromotionRequest,
  previewPromotionCommand,
  rejectPromotionRequest,
  reviewPromotionRequest,
} from '@/lib/api/promotion-api';
import type {
  PromotionCommandPreview,
  PromotionCommandReceipt,
} from '@/lib/api/promotion-api';
import {
  buildPromotionCommand,
  PROMOTION_COMMAND_ACTIONS,
} from './commands';
import type { PromotionCommandAction } from './commands';

const ACTION_LABELS: Record<PromotionCommandAction, string> = {
  OPEN_PROMOTION: '发起研究晋级评审',
  REVIEW_PROMOTION: '独立复核',
  APPROVE_PROMOTION: '批准研究资格',
  REJECT_PROMOTION: '拒绝晋级',
  EXECUTE_PROMOTION: '一次性兑现研究晋级',
};

interface CommandReviewPanelProps {
  contractVerified: boolean;
}

async function submit(
  action: PromotionCommandAction,
  targetId: string,
  preview: PromotionCommandPreview,
): Promise<PromotionCommandReceipt> {
  const command = buildPromotionCommand(preview);
  switch (action) {
    case 'OPEN_PROMOTION':
      return openPromotionRequest(targetId, command);
    case 'REVIEW_PROMOTION':
      return reviewPromotionRequest(targetId, command);
    case 'APPROVE_PROMOTION':
      return approvePromotionRequest(targetId, command);
    case 'REJECT_PROMOTION':
      return rejectPromotionRequest(targetId, command);
    case 'EXECUTE_PROMOTION':
      return executePromotionRequest(targetId, command);
  }
}

export function CommandReviewPanel({ contractVerified }: CommandReviewPanelProps) {
  const [action, setAction] = useState<PromotionCommandAction>('OPEN_PROMOTION');
  const [targetId, setTargetId] = useState('');
  const [businessReason, setBusinessReason] = useState('');
  const [preview, setPreview] = useState<PromotionCommandPreview | null>(null);
  const [receipt, setReceipt] = useState<PromotionCommandReceipt | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const identityReady = governanceEnabled() && getGovernanceAccessToken().length > 0;
  const enabled = contractVerified && identityReady;
  const inputReady = targetId.trim().length > 0 && businessReason.trim().length > 0;
  const previewMatches = preview !== null
    && preview.action === action
    && preview.target_id === targetId.trim()
    && preview.business_reason === businessReason.trim();
  const commandReady = enabled && previewMatches && acknowledged && !busy;
  const safetyVerified = useMemo(() => preview !== null
    && preview.expected_impact.live_trading_authorized === false
    && preview.expected_impact.order_submission_allowed === false
    && preview.expected_impact.capital_scaling_allowed === false
    && preview.expected_impact.risk_limit_change === false
    && preview.expected_impact.kill_switch_reset === false, [preview]);

  const invalidate = () => {
    setPreview(null);
    setReceipt(null);
    setAcknowledged(false);
    setError(null);
  };

  const loadPreview = async () => {
    if (!enabled || !inputReady) return;
    setBusy(true);
    setError(null);
    setReceipt(null);
    try {
      const next = await previewPromotionCommand(action, targetId.trim(), businessReason.trim());
      setPreview(next);
      setAcknowledged(false);
    } catch (cause) {
      setPreview(null);
      setError(cause instanceof Error ? cause.message : 'command preview failed');
    } finally {
      setBusy(false);
    }
  };

  const execute = async () => {
    if (!commandReady || preview === null || !safetyVerified) return;
    setBusy(true);
    setError(null);
    try {
      const next = await submit(action, targetId.trim(), preview);
      setReceipt(next);
    } catch (cause) {
      setReceipt(null);
      setError(cause instanceof Error ? cause.message : 'command execution failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-lg border border-line bg-inset/35 p-4" aria-labelledby="promotion-command-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[9px] font-semibold uppercase tracking-[0.15em] text-faint">Strong identity command</p>
          <h3 id="promotion-command-title" className="mt-1 text-sm font-semibold text-ink">研究晋级命令审查</h3>
        </div>
        <span className={`rounded border px-2 py-1 font-mono text-[9px] font-semibold ${enabled ? 'border-pos/35 text-pos' : 'border-warn/35 text-warn'}`}>
          {enabled ? 'IDENTITY READY' : 'EXTERNAL IDENTITY PENDING'}
        </span>
      </div>

      {!enabled && (
        <div className="mt-3 rounded border border-warn/35 bg-warn/10 p-3" role="status">
          <p className="font-mono text-[10px] font-semibold text-warn">COMMANDS DISABLED</p>
          <p className="mt-1 text-xs leading-5 text-muted">
            需要已验证的运行时 L2 契约，以及外部 OIDC、mTLS、MFA 和应用授权。浏览器自报身份不会启用命令。
          </p>
        </div>
      )}

      <fieldset disabled={!enabled || busy} className="mt-4 space-y-3 disabled:opacity-55">
        <label className="block text-xs text-muted">
          操作
          <select
            value={action}
            onChange={event => { setAction(event.target.value as PromotionCommandAction); invalidate(); }}
            className="mt-1 w-full rounded border border-line bg-panel px-3 py-2 text-xs text-ink"
          >
            {PROMOTION_COMMAND_ACTIONS.map(value => (
              <option key={value} value={value}>{ACTION_LABELS[value]}</option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-muted">
          {action === 'OPEN_PROMOTION' ? 'Alpha ID' : '审批请求 ID'}
          <input
            value={targetId}
            onChange={event => { setTargetId(event.target.value); invalidate(); }}
            autoComplete="off"
            className="mt-1 w-full rounded border border-line bg-panel px-3 py-2 font-mono text-xs text-ink"
          />
        </label>
        <label className="block text-xs text-muted">
          业务理由
          <textarea
            value={businessReason}
            onChange={event => { setBusinessReason(event.target.value); invalidate(); }}
            rows={3}
            className="mt-1 w-full resize-y rounded border border-line bg-panel px-3 py-2 text-xs leading-5 text-ink"
          />
        </label>
        <button
          type="button"
          disabled={!inputReady || busy}
          onClick={() => { void loadPreview(); }}
          className="w-full rounded border border-accent/45 bg-accent/10 px-3 py-2 text-xs font-semibold text-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? '校验中…' : '生成服务端影响预览'}
        </button>
      </fieldset>

      {previewMatches && preview && (
        <div className="mt-4 space-y-3 rounded border border-line bg-panel p-3">
          <div>
            <p className="font-mono text-[9px] font-semibold uppercase tracking-[0.14em] text-faint">Risk diff</p>
            <ul className="mt-2 space-y-1">
              {preview.risk_diff.map(item => <li key={item} className="font-mono text-[10px] text-muted">• {item}</li>)}
            </ul>
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[10px]">
            <dt className="text-faint">identity</dt><dd className="break-all text-ink">{preview.verified_actor_id}</dd>
            <dt className="text-faint">role</dt><dd className="text-ink">{preview.required_role}</dd>
            <dt className="text-faint">version</dt><dd className="text-ink">{preview.target_version}</dd>
            <dt className="text-faint">preview</dt><dd className="break-all text-ink">{preview.preview_hash.slice(0, 16)}…</dd>
          </dl>
          <p className={`font-mono text-[10px] font-semibold ${safetyVerified ? 'text-pos' : 'text-neg'}`}>
            {safetyVerified ? 'REAL-MONEY EFFECTS: NONE' : 'SAFETY PREVIEW REJECTED'}
          </p>
          <label className="flex items-start gap-2 text-xs leading-5 text-muted">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={event => setAcknowledged(event.target.checked)}
              className="mt-1"
            />
            我已核对目标版本、风险差异与预期影响；后端仍将重新验证身份、角色、版本、审批链和审计写入。
          </label>
          <button
            type="button"
            disabled={!commandReady || !safetyVerified}
            onClick={() => { void execute(); }}
            className="w-full rounded border border-warn/45 bg-warn/10 px-3 py-2 text-xs font-semibold text-warn disabled:cursor-not-allowed disabled:opacity-50"
          >
            提交一次性治理命令
          </button>
        </div>
      )}

      {error && <p className="mt-3 break-all rounded border border-neg/35 bg-neg/10 p-3 font-mono text-[10px] text-neg" role="alert">{error}</p>}

      {receipt && (
        <div className="mt-4 rounded border border-pos/35 bg-pos/10 p-3" role="status">
          <p className="font-mono text-[10px] font-semibold text-pos">AUTHORITY REQUERY + AUDIT RECEIPT</p>
          <p className="mt-2 break-all font-mono text-[10px] text-ink">{receipt.receipt_id}</p>
          <p className="mt-1 break-all font-mono text-[9px] text-muted">audit {receipt.audit_receipt.entry_hash}</p>
          <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-all font-mono text-[9px] text-muted">
            {JSON.stringify(receipt.authority_requery, null, 2)}
          </pre>
        </div>
      )}
    </section>
  );
}
