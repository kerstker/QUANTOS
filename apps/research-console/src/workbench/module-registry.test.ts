import { describe, expect, it } from 'vitest';

import {
  classifyConsoleModule,
  CONSOLE_MODULE_PATHS,
  CONSOLE_MODULES,
} from './module-registry';

describe('console module registry', () => {
  it('registers each fixed read-only endpoint exactly once', () => {
    expect(CONSOLE_MODULES).toHaveLength(56);
    expect(new Set(CONSOLE_MODULE_PATHS).size).toBe(CONSOLE_MODULE_PATHS.length);
    expect(CONSOLE_MODULE_PATHS.every(path => path.startsWith('/api/v1/console/'))).toBe(true);
  });

  it('admits the two specialised authority source markers only on their owning modules', () => {
    const special = CONSOLE_MODULES
      .filter(module => module.acceptedSources)
      .map(module => [module.endpoint, module.acceptedSources]);
    expect(special).toEqual([
      ['/api/v1/console/hourly-alpha', ['HOURLY_ALPHA_SQLITE_AUTHORITIES']],
      ['/api/v1/console/mft-execution', ['MFT_READ_MODEL_SQLITE_AUTHORITY']],
    ]);
  });

  it('separates readable evidence from selection authority', () => {
    expect(classifyConsoleModule({
      data_provenance: 'REAL_ALPHA_LIFECYCLE',
      evidence_status: 'DIAGNOSTIC_ONLY',
      authoritative: false,
    })).toBe('DIAGNOSTIC');
    expect(classifyConsoleModule({
      data_provenance: 'REPOSITORY_EVIDENCE',
      evidence_status: 'TECHNICAL_VALIDATION_ONLY',
      authoritative: false,
    })).toBe('TECHNICAL');
    expect(classifyConsoleModule({
      data_provenance: 'REAL',
      snapshot_status: 'REAL',
    })).toBe('REAL');
    expect(classifyConsoleModule({
      data_provenance: 'REAL_ASHARE_TECHNICAL_RUNTIME',
      evidence_status: 'TECHNICAL_VALIDATION_ONLY',
      authoritative: false,
    })).toBe('TECHNICAL');
    expect(classifyConsoleModule({
      data_provenance: 'REAL_ASHARE_FORWARD_OBSERVATION',
      evidence_status: 'TECHNICAL_VALIDATION_ONLY',
      authoritative: false,
    })).toBe('TECHNICAL');
  });

  it('keeps stale and corrupt snapshots distinct from missing modules', () => {
    expect(classifyConsoleModule({
      data_provenance: 'REAL_ALPHA_FAMILY_LEDGER',
      snapshot_status: 'STALE',
    })).toBe('STALE');
    expect(classifyConsoleModule({
      data_provenance: 'REAL_ALPHA_FAMILY_LEDGER',
      snapshot_status: 'CORRUPT_OR_UNREADABLE',
    })).toBe('CORRUPT_OR_UNREADABLE');
    expect(classifyConsoleModule({
      data_provenance: 'REAL_MEXC_MARKET_SNAPSHOT',
      status: 'CORRUPT_OR_UNREADABLE',
    })).toBe('CORRUPT_OR_UNREADABLE');
  });

  it('does not present placeholders or missing stores as real data', () => {
    expect(classifyConsoleModule({
      data_provenance: 'SYNTHETIC_DETERMINISTIC',
      snapshot_status: 'SYNTHETIC',
    })).toBe('SYNTHETIC');
    expect(classifyConsoleModule({
      data_provenance: 'UNAVAILABLE',
      availability_mode: 'NOT_PROVISIONED',
      snapshot_status: 'NO_SNAPSHOT',
    })).toBe('UNAVAILABLE');
    expect(classifyConsoleModule({
      data_provenance: 'UNAVAILABLE',
      evidence_status: 'UNAVAILABLE',
      authoritative: false,
    })).toBe('UNAVAILABLE');
    expect(classifyConsoleModule({
      data_provenance: 'REAL_ASHARE_TECHNICAL_RUNTIME',
      snapshot_present: false,
      evidence_status: 'TECHNICAL_VALIDATION_ONLY',
      authoritative: false,
    })).toBe('UNAVAILABLE');
    expect(classifyConsoleModule({
      data_provenance: 'UNAVAILABLE',
      status: 'INVALID_ARTIFACT',
      authoritative: false,
    })).toBe('UNAVAILABLE');
    expect(classifyConsoleModule({
      data_provenance: 'UNAVAILABLE',
      availability_mode: 'SEARCH_EVIDENCE_NOT_PUBLISHED',
      authoritative: false,
    })).toBe('UNAVAILABLE');
    expect(classifyConsoleModule({
      data_provenance: 'UNAVAILABLE',
      availability_mode: 'FORWARD_OBSERVATION_NOT_STARTED',
      authoritative: false,
    })).toBe('UNAVAILABLE');
  });
});
