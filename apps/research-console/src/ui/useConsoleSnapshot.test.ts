import { describe, expect, it } from 'vitest';

import { selectConsoleDisplayPayload } from './console-display-payload';

const backend = { source: 'backend' };
const fixture = { source: 'fixture' };

describe('selectConsoleDisplayPayload', () => {
  it('uses a verified backend payload in every mode', () => {
    expect(selectConsoleDisplayPayload({
      payload: backend,
      usingBackend: true,
      mockFixturesAllowed: false,
    }, fixture)).toBe(backend);
  });

  it('admits a fixture only after explicit mock-mode authorization', () => {
    expect(selectConsoleDisplayPayload({
      payload: null,
      usingBackend: false,
      mockFixturesAllowed: true,
    }, fixture)).toBe(fixture);
  });

  it('fails closed on real-API transport failure', () => {
    expect(selectConsoleDisplayPayload({
      payload: null,
      usingBackend: false,
      mockFixturesAllowed: false,
    }, fixture)).toBeNull();
  });

  it('does not substitute a fixture for an inconsistent backend state', () => {
    expect(selectConsoleDisplayPayload({
      payload: null,
      usingBackend: true,
      mockFixturesAllowed: true,
    }, fixture)).toBeNull();
  });
});
