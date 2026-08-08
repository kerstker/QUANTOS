import type {
  PromotionCommandBody,
  PromotionCommandPreview,
  PromotionCommandAction,
} from '@/lib/api/promotion-api';

export const PROMOTION_COMMAND_ACTIONS = [
  'OPEN_PROMOTION',
  'REVIEW_PROMOTION',
  'APPROVE_PROMOTION',
  'REJECT_PROMOTION',
  'EXECUTE_PROMOTION',
] as const;

export type { PromotionCommandAction } from '@/lib/api/promotion-api';

export function isPromotionCommandAction(value: string): value is PromotionCommandAction {
  return (PROMOTION_COMMAND_ACTIONS as readonly string[]).includes(value);
}

export function newIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID !== 'function') {
    throw new Error('secure random UUID generation is unavailable');
  }
  return globalThis.crypto.randomUUID();
}

export function buildPromotionCommand(
  preview: PromotionCommandPreview,
  idempotencyKey = newIdempotencyKey(),
): PromotionCommandBody {
  if (!isPromotionCommandAction(preview.action)) {
    throw new Error('unsupported promotion command action');
  }
  if (!preview.business_reason.trim()) {
    throw new Error('business reason is required');
  }
  return {
    idempotency_key: idempotencyKey,
    target_version: preview.target_version,
    business_reason: preview.business_reason,
    expected_impact: preview.expected_impact,
    risk_diff: preview.risk_diff,
    preview_hash: preview.preview_hash,
  };
}
