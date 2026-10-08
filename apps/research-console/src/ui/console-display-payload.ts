export interface ConsoleDisplaySelection<T> {
  payload: T | null;
  usingBackend: boolean;
  mockFixturesAllowed: boolean;
}

/**
 * Select the payload an old detail page may render. Frontend fixtures are a
 * deliberate demo/test capability, never a transport-error fallback in a
 * production or otherwise real-API session.
 */
export function selectConsoleDisplayPayload<T>(
  result: ConsoleDisplaySelection<T>,
  fixture: T,
): T | null {
  if (result.usingBackend) return result.payload;
  return result.mockFixturesAllowed ? fixture : null;
}
