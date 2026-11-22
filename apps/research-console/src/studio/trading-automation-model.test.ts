import { describe, expect, it } from 'vitest';
import {
  DEFAULT_GRADUATION_INPUTS,
  DEFAULT_SYSTEM_CEILING,
  buildDgxRunbook,
  canonicalizeJson,
  computeCanonicalSha256,
  evaluateGraduation,
  validateAndBuildMandateProposal,
} from './trading-automation-model';

describe('trading-automation-model', () => {
  it('returns TESTNET_MANDATE_READY when all testnet gates pass', () => {
    const res = evaluateGraduation({ ...DEFAULT_GRADUATION_INPUTS, targetVenue: 'testnet' });
    expect(res.verdict).toBe('TESTNET_MANDATE_READY');
    expect(res.passedCount).toBe(6);
    expect(res.aiDirectAuthority).toBe(false);
    expect(res.requiresHumanSignature).toBe(true);
  });

  it('rejects legacy INVALIDATED_PERP_LEDGER_V1 unconditionally', () => {
    const res = evaluateGraduation({
      ...DEFAULT_GRADUATION_INPUTS,
      ledgerVersion: 'INVALIDATED_PERP_LEDGER_V1',
      oosNetSharpe: 4.5,
    });
    expect(res.verdict).toBe('REJECTED');
    const stage2 = res.stages.find((s) => s.stageId === 'stage_2_v2_validation');
    expect(stage2?.status).toBe('FAIL');
  });

  it('locks mainnet behind BLOCKED_EXTERNAL_VERIFIER_REQUIRED until integrated', () => {
    const res = evaluateGraduation({
      ...DEFAULT_GRADUATION_INPUTS,
      targetVenue: 'mainnet',
      soakHoursCompleted: 96,
      observedFills: 30,
      realizedSlippageBps: 4.5,
      ledgerReconciliationDriftUsd: 0.01,
      externalVerifierIntegrated: false,
      externalSignatureVerified: false,
    });
    expect(res.verdict).toBe('BLOCKED_EXTERNAL_VERIFIER_REQUIRED');
    const stage5 = res.stages.find((s) => s.stageId === 'stage_5_human_authority');
    expect(stage5?.status).toBe('BLOCKED');
  });

  it('enforces tightening-only ceiling and fail-closed bounds in mandate proposal drafting', async () => {
    const assessment = evaluateGraduation({ ...DEFAULT_GRADUATION_INPUTS, targetVenue: 'testnet' });

    const overCeiling = validateAndBuildMandateProposal(
      DEFAULT_GRADUATION_INPUTS,
      assessment,
      {
        instruments: ['BTCUSDT'],
        maxSingleOrderNotional: 1500,
        maxGrossNotional: 3000,
        maxNetNotional: 1500,
        validityHours: 12,
        nonce: 'quantos-nonce-00001',
      },
      DEFAULT_SYSTEM_CEILING,
      1_700_000_000_000
    );
    expect(overCeiling.valid).toBe(false);
    expect(overCeiling.proposal).toBeNull();

    const singleOverGross = validateAndBuildMandateProposal(
      DEFAULT_GRADUATION_INPUTS,
      assessment,
      {
        instruments: ['BTCUSDT'],
        maxSingleOrderNotional: 900,
        maxGrossNotional: 500,
        maxNetNotional: 400,
        validityHours: 12,
        nonce: 'quantos-nonce-00001',
      },
      DEFAULT_SYSTEM_CEILING,
      1_700_000_000_000
    );
    expect(singleOverGross.valid).toBe(false);

    const validA = validateAndBuildMandateProposal(
      DEFAULT_GRADUATION_INPUTS,
      assessment,
      {
        instruments: ['ETHUSDT', 'BTCUSDT'],
        maxSingleOrderNotional: 500,
        maxGrossNotional: 2000,
        maxNetNotional: 1000,
        validityHours: 12,
        nonce: 'quantos-nonce-00001',
      },
      DEFAULT_SYSTEM_CEILING,
      1_700_000_000_000
    );
    expect(validA.valid).toBe(true);
    expect(validA.proposal?.allowed_instruments).toEqual(['BTCUSDT', 'ETHUSDT']);
    const hash1 = await computeCanonicalSha256(validA.canonicalJson);
    const hash2 = await computeCanonicalSha256(canonicalizeJson(validA.proposal));
    expect(hash1).toHaveLength(64);
    expect(hash1).toBe(hash2);
  });

  it('builds 4 ordered DGX Spark runbook steps without secrets', () => {
    const steps = buildDgxRunbook('binance', 'testnet', 'btc_alpha_01');
    expect(steps).toHaveLength(4);
    expect(steps[0].step).toBe('01');
    expect(steps[2].command).toContain('btc_alpha_01');
  });
});
