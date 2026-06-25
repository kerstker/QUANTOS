/** Back-compat shim: DataSourceHeader now renders the new ProvenanceRibbon.
 *  Existing pages keep importing from here during the rebuild. */
import React from 'react';
import { ProvenanceRibbon } from '@/ui/primitives';
import type { ConsoleApiState } from '@/ui/useConsoleSnapshot';

export type { ConsoleApiState } from '@/ui/useConsoleSnapshot';

export function apiStateDecision(apiState: ConsoleApiState) {
  if (apiState === 'connected') return 'API_CONNECTED';
  if (apiState === 'loading') return 'API_LOADING';
  if (apiState === 'error') return 'API_ERROR';
  return 'NOT_CONNECTED';
}

export function DataSourceHeader(props: {
  usingBackend: boolean; apiState: ConsoleApiState; source?: string; error?: string | null;
}) {
  return <ProvenanceRibbon {...props} />;
}
