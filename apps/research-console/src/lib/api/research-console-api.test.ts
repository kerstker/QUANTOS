import { describe, expect, it } from 'vitest';

import { assertConsoleSnapshot } from './research-console-api';

// The frontend trusts the backend via a compile-time cast and silently falls
// back to mock on error. assertConsoleSnapshot is the runtime line of defense:
// a body without the read-only snapshot markers must throw so the page shows an
// error + honest mock fallback instead of rendering a drifted/forged payload.
describe('assertConsoleSnapshot', () => {
  const valid = { source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0', stats: {} };

  it('returns the body when both contract markers are present', () => {
    expect(assertConsoleSnapshot(valid, '/x')).toBe(valid);
  });

  it('throws when source marker is wrong (e.g. a proxy/HTML error page)', () => {
    expect(() => assertConsoleSnapshot({ read_only: true }, '/x')).toThrow(/non-snapshot/);
    expect(() => assertConsoleSnapshot({ source: 'SOMETHING_ELSE', read_only: true }, '/x')).toThrow();
  });

  it('throws when read_only is not strictly true', () => {
    expect(() => assertConsoleSnapshot({ source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: 'true' }, '/x')).toThrow();
    expect(() => assertConsoleSnapshot({ source: 'BACKEND_COMPUTED_SNAPSHOT' }, '/x')).toThrow();
  });

  it('throws on null / non-object bodies', () => {
    expect(() => assertConsoleSnapshot(null, '/x')).toThrow();
    expect(() => assertConsoleSnapshot('<!doctype html>', '/x')).toThrow();
    expect(() => assertConsoleSnapshot(undefined, '/x')).toThrow();
  });

  it('names the failing path in the error for debuggability', () => {
    expect(() => assertConsoleSnapshot({}, '/api/v1/console/overview')).toThrow(/overview/);
  });
});
