import { createElement } from 'react';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  buildPromotionCommand,
  isPromotionCommandAction,
  newIdempotencyKey,
} from '../commands';
import type { PromotionCommandPreview } from '@/lib/api/promotion-api';

const preview: PromotionCommandPreview = {
  action: 'OPEN_PROMOTION' as const,
  target_id: 'alpha-1',
  target_version: 7,
  business_reason: 'advance reproducible research evidence',
  verified_actor_id: 'https://identity.example.test|alice',
  required_role: 'PROMOTION_REQUESTER',
  strong_identity: 'OIDC_MTLS_MFA',
  expected_impact: {
    scope: 'RESEARCH_PROMOTION_ONLY',
    target_id: 'alpha-1',
    approval_state: 'REQUESTED',
    research_state: 'UNCHANGED',
    live_trading_authorized: false,
    order_submission_allowed: false,
    capital_scaling_allowed: false,
    risk_limit_change: false,
    kill_switch_reset: false,
  },
  risk_diff: ['approval_request: absent -> REQUESTED'],
  preview_hash: 'a'.repeat(64),
};

describe('governed promotion commands', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('uses a fresh RFC 4122 version-4 idempotency key', () => {
    const first = newIdempotencyKey();
    const second = newIdempotencyKey();
    expect(first).not.toBe(second);
    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  });

  it('builds an exact preview-bound command without client identity fields', () => {
    const command = buildPromotionCommand(preview, '11111111-1111-4111-8111-111111111111');
    expect(command).toEqual({
      idempotency_key: '11111111-1111-4111-8111-111111111111',
      target_version: 7,
      business_reason: preview.business_reason,
      expected_impact: preview.expected_impact,
      risk_diff: preview.risk_diff,
      preview_hash: preview.preview_hash,
    });
    expect(command).not.toHaveProperty('operator');
    expect(command).not.toHaveProperty('actor_id');
    expect(command).not.toHaveProperty('roles');
  });

  it('exposes only research-promotion governance actions', () => {
    for (const action of ['OPEN_PROMOTION', 'REVIEW_PROMOTION', 'APPROVE_PROMOTION', 'REJECT_PROMOTION', 'EXECUTE_PROMOTION']) {
      expect(isPromotionCommandAction(action)).toBe(true);
    }
    for (const unsafe of ['SUBMIT_ORDER', 'SCALE_CAPITAL', 'MODIFY_RISK_LIMIT', 'RESET_KILL_SWITCH']) {
      expect(isPromotionCommandAction(unsafe)).toBe(false);
    }
  });

  it('keeps the command panel disabled without external strong identity', async () => {
    vi.stubEnv('VITE_GOVERNANCE_URL', '');
    const { CommandReviewPanel } = await import('../CommandReviewPanel');
    render(createElement(CommandReviewPanel, { contractVerified: true }));

    expect(screen.getByText('EXTERNAL IDENTITY PENDING')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成服务端影响预览' })).toBeDisabled();
    expect(screen.queryByText('SUBMIT_ORDER')).not.toBeInTheDocument();
  });
});
