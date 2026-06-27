// Vitest global setup (Phase 8). Registers @testing-library/jest-dom matchers (toBeInTheDocument,
// toHaveTextContent, ...) and cleans up the DOM between tests so component renders are isolated.
import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
});
