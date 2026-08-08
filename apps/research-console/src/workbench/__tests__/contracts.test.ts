import { describe, expect, it } from 'vitest';

import { WORKBENCH_DATA_SOURCE_MANIFEST } from '../data-source-manifest';
import {
  parseMftSnapshot,
} from '../contracts';
import { makeMftSnapshot } from './mft-fixtures';

describe('Wave 3 data-source manifest', () => {
  it('defines exactly five unique object workspaces with unique routes and purposes', () => {
    expect(WORKBENCH_DATA_SOURCE_MANIFEST).toHaveLength(5);
    expect(new Set(WORKBENCH_DATA_SOURCE_MANIFEST.map(item => item.id)).size).toBe(5);
    expect(new Set(WORKBENCH_DATA_SOURCE_MANIFEST.map(item => item.route)).size).toBe(5);
    expect(new Set(WORKBENCH_DATA_SOURCE_MANIFEST.map(item => item.purpose)).size).toBe(5);
  });

  it('forbids synthetic fallback and direct execution dependencies everywhere', () => {
    for (const item of WORKBENCH_DATA_SOURCE_MANIFEST) {
      expect(item.allowSyntheticFallback).toBe(false);
      expect(item.forbiddenDependencies).toContain('exchange-adapter');
      expect(item.forbiddenDependencies).toContain('sqlite');
      expect(item.authoritativeEndpoints.every(path => path.startsWith('/api/v1/'))).toBe(true);
    }
  });
});

describe('parseMftSnapshot', () => {
  it('accepts a complete current MFT v1 snapshot', () => {
    const snapshot = makeMftSnapshot();
    expect(parseMftSnapshot(snapshot)).toEqual(snapshot);
  });

  it('rejects an unknown schema, missing provenance, and a missing fixed section', () => {
    expect(() => parseMftSnapshot(makeMftSnapshot({ schema_version: 'v2' }))).toThrow(/UNKNOWN_SCHEMA/);
    expect(() => parseMftSnapshot(makeMftSnapshot({ data_provenance: '' }))).toThrow(/PROVENANCE/);
    const missing = makeMftSnapshot() as unknown as Record<string, unknown>;
    delete missing.risk;
    expect(() => parseMftSnapshot(missing)).toThrow(/SECTION/);
  });

  it('rejects any drift in the fixed L2 safety declaration', () => {
    const unsafe = makeMftSnapshot();
    expect(() => parseMftSnapshot({
      ...unsafe,
      safety: { ...unsafe.safety, allow_order_submission: true },
    })).toThrow(/SAFETY/);
  });

  it('requires available snapshots to be current, complete, and cursor-addressable', () => {
    expect(() => parseMftSnapshot(makeMftSnapshot({ cursor: null }))).toThrow(/AVAILABLE/);
    expect(() => parseMftSnapshot(makeMftSnapshot({ missing_sections: ['risk'] }))).toThrow(/AVAILABLE/);
    expect(() => parseMftSnapshot(makeMftSnapshot({
      freshness: { ...makeMftSnapshot().freshness, status: 'STALE' },
    }))).toThrow(/AVAILABLE/);
  });
});
