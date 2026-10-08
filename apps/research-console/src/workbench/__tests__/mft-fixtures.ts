import {
  MFT_CONSOLE_SCHEMA,
  MFT_READ_MODEL_SECTIONS,
  type MftSnapshot,
} from '../contracts';

const ZERO_HASH = '0'.repeat(64);

export function makeMftSnapshot(overrides: Record<string, unknown> = {}): MftSnapshot {
  const sections = Object.fromEntries(
    MFT_READ_MODEL_SECTIONS.map(section => [section, { status: 'READY', value: section }]),
  );
  return {
    source: 'MFT_READ_MODEL_SQLITE_AUTHORITY',
    read_only: true,
    api_version: 'v1',
    data_provenance: 'REAL_MFT_READ_MODEL',
    snapshot_id: ZERO_HASH,
    snapshot_ids: [ZERO_HASH],
    snapshot_status: 'REAL',
    data_quality: {
      status: 'REAL',
      provenance: 'REAL_MFT_READ_MODEL',
      snapshot_ids: [ZERO_HASH],
    },
    schema_version: MFT_CONSOLE_SCHEMA,
    as_of_ms: 2_000,
    status: 'AVAILABLE',
    freshness: {
      status: 'CURRENT',
      age_ms: 0,
      policy_id: 'mft-console-freshness',
      policy_version: '1',
      max_age_ms: 7_200_000,
    },
    watermark: { sequence: 0, event_at_ms: 1_000, event_hash: ZERO_HASH },
    cursor: `0:${ZERO_HASH}`,
    missing_sections: [],
    ...sections,
    safety: {
      read_only: true,
      readiness: 'L2_DRY_RUN_ONLY',
      testnet_only: true,
      live_trading_enabled: false,
      real_broker_enabled: false,
      allow_order_submission: false,
      allow_ai_direct_trading: false,
      require_human_approval: true,
      submit_capability: false,
      reset_capability: false,
      approve_capability: false,
      microstructure_scope: 'TESTNET_MICROSTRUCTURE_NOT_PRODUCTION',
    },
    ...overrides,
  } as unknown as MftSnapshot;
}
