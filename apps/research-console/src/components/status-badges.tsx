/** Back-compat shims over the new design system (src/ui/primitives).
 *  Existing page imports keep working; rendering uses the new cockpit tokens. */
import React from 'react';
import { Chip, StatusChip, StatTile } from '@/ui/primitives';

export function DecisionBadge({ decision }: { decision: string }) {
  return <StatusChip value={decision} />;
}
export function StatusBadge({ status }: { status: string }) {
  return <StatusChip value={status} />;
}
export function RiskBadge({ passed }: { passed: boolean }) {
  return <Chip label={passed ? '通过 / PASS' : '失败 / FAIL'} tone={passed ? 'pos' : 'neg'} />;
}
export function MetricCard({ label, value, unit }: { label: string; value: string | number; unit?: string }) {
  return <StatTile label={label} value={typeof value === 'number' ? value.toLocaleString() : value} unit={unit} />;
}
