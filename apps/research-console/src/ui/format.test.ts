import { describe, it, expect } from 'vitest';
import { num, pct, pctPoints, fixed, signed, shortHash, stamp, statusTone, deltaTone } from './format';

describe('format — deterministic display formatters', () => {
  it('renders null/undefined/NaN as an em dash', () => {
    for (const f of [num, pct, fixed, signed]) {
      expect(f(null)).toBe('—');
      expect(f(undefined)).toBe('—');
      expect(f(NaN)).toBe('—');
    }
    expect(shortHash(null)).toBe('—');
    expect(stamp(null)).toBe('—');
  });

  it('formats numbers, percents and signs honestly', () => {
    expect(num(12345)).toBe('12,345');
    expect(pct(0.1234)).toBe('12.3%');
    expect(pctPoints(12.5)).toBe('12.5%');
    expect(fixed(1.2)).toBe('1.20');
    expect(signed(0.5)).toBe('+0.50');
    expect(signed(-0.5)).toBe('-0.50');
  });

  it('shortens long hashes and leaves short ones intact', () => {
    expect(shortHash('abcdef1234567890', 8)).toBe('abcdef12…');
    expect(shortHash('abc', 8)).toBe('abc');
  });

  it('stamps epochs deterministically in UTC (no tz drift)', () => {
    expect(stamp(0)).toBe('1970-01-01 00:00');           // seconds
    expect(stamp(1_700_000_000)).toBe('2023-11-14 22:13');
  });

  it('maps the new lifecycle states to a semantic tone', () => {
    expect(statusTone('APPROVED')).toBe('pos');
    expect(statusTone('REJECTED')).toBe('neg');
    expect(statusTone('DEGRADED')).toBe('warn');         // Phase 6 monitoring state
    expect(statusTone('PAUSED')).toBe('warn');
    expect(deltaTone(1)).toBe('pos');
    expect(deltaTone(-1)).toBe('neg');
    expect(deltaTone(0)).toBe('muted');
  });
});
