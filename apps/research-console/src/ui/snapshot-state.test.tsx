import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  Button,
  DataTable,
  LoadingVeil,
  ProvenanceRibbon,
  type Column,
} from './primitives';
import {
  deriveSnapshotDisplayState,
  isSnapshotActionAllowed,
  normalizeSnapshotDisplayState,
  type SnapshotDisplayState,
} from './snapshot-state';

describe('snapshot display state contract', () => {
  it.each([
    ['DATA_BLOCKED', 'BLOCKED'],
    ['SYNTHETIC_DETERMINISTIC', 'BACKEND_SYNTHETIC'],
    ['REAL_RISK_SNAPSHOT', undefined],
    ['STALE_DATA', 'STALE'],
    ['NO_RUN', 'NO_SNAPSHOT'],
    ['UNAVAILABLE', 'UNAVAILABLE'],
  ] as const)('normalizes backend status %s', (wire, expected) => {
    const normalized = normalizeSnapshotDisplayState(wire);
    if (expected === undefined) expect(normalized).toBeUndefined();
    else expect(normalized).toBe(expected);
  });

  it.each([
    [{ apiState: 'disabled', usingBackend: false }, 'MOCK'],
    [{ apiState: 'loading', usingBackend: false }, 'LOADING'],
    [{ apiState: 'error', usingBackend: false }, 'UNAVAILABLE'],
    [{ apiState: 'connected', usingBackend: true, realData: true }, 'REAL'],
    [{ apiState: 'connected', usingBackend: true, evidenceData: true }, 'EVIDENCE'],
    [{ apiState: 'connected', usingBackend: true }, 'BACKEND_SYNTHETIC'],
    [{ apiState: 'connected', usingBackend: true, noDataYet: true }, 'NO_SNAPSHOT'],
    [{ apiState: 'connected', usingBackend: true, dataStatus: 'BLOCKED' }, 'BLOCKED'],
    [{ apiState: 'connected', usingBackend: true, dataStatus: 'STALE' }, 'STALE'],
  ] as const)('classifies %j as %s', (input, expected) => {
    expect(deriveSnapshotDisplayState(input)).toBe(expected);
  });

  it('keeps explicit admission state authoritative over transport/provenance flags', () => {
    expect(deriveSnapshotDisplayState({
      apiState: 'connected', usingBackend: true, realData: true, dataStatus: 'BLOCKED',
    })).toBe('BLOCKED');
  });

  it('lets transport revalidation override a retained positive snapshot', () => {
    expect(deriveSnapshotDisplayState({
      apiState: 'loading', usingBackend: true, realData: true, dataStatus: 'REAL',
    })).toBe('LOADING');
    expect(deriveSnapshotDisplayState({
      apiState: 'error', usingBackend: true, evidenceData: true, dataStatus: 'EVIDENCE',
    })).toBe('UNAVAILABLE');
  });

  it('only permits the positive snapshot states; blocked/stale/offline states fail closed', () => {
    const denied: SnapshotDisplayState[] = [
      'MOCK', 'BLOCKED', 'STALE', 'UNAVAILABLE', 'NO_SNAPSHOT', 'LOADING',
    ];
    for (const state of denied) expect(isSnapshotActionAllowed(state), state).toBe(false);
    expect(isSnapshotActionAllowed('REAL')).toBe(true);
    expect(isSnapshotActionAllowed('EVIDENCE')).toBe(true);
    expect(isSnapshotActionAllowed('BACKEND_SYNTHETIC')).toBe(false);
  });
});

describe('ProvenanceRibbon workflow states', () => {
  const base = { usingBackend: true, apiState: 'connected' as const, source: 'TEST_SNAPSHOT' };

  it.each([
    ['BLOCKED', 'DATA_BLOCKED', 'actions are forbidden'],
    ['STALE', 'STALE_DATA', 'not usable evidence'],
    ['UNAVAILABLE', 'UNAVAILABLE', 'fallback content is not decision data'],
    ['NO_SNAPSHOT', 'NO_SNAPSHOT', 'neither approval nor tradability'],
    ['LOADING', 'LOADING', 'no decision state yet'],
  ] as const)('surfaces %s distinctly', (dataStatus, chip, headline) => {
    render(<ProvenanceRibbon {...base} dataStatus={dataStatus} />);
    expect(screen.getByText(chip)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(headline, 'i'))).toBeInTheDocument();
  });

  it('does not label a disconnected page as real or connected', () => {
    render(<ProvenanceRibbon usingBackend={false} apiState="disabled" realData source="FRONTEND_MOCK" />);
    expect(screen.getByText('MOCK_MODE')).toBeInTheDocument();
    expect(document.body.textContent).toContain('NOT_CONNECTED');
    expect(screen.queryByText('REAL_RESULTS')).not.toBeInTheDocument();
  });

  it('keeps evidence and read-only markers visible together', () => {
    render(<ProvenanceRibbon {...base} evidenceData />);
    expect(screen.getByText('VERIFIED_EVIDENCE')).toBeInTheDocument();
    expect(document.body.textContent).toContain('READ_ONLY');
  });
});

describe('fail-closed interaction primitives', () => {
  it('disabled action cannot invoke its handler', () => {
    const onClick = vi.fn();
    render(<Button disabled onClick={onClick}>Submit</Button>);
    const button = screen.getByRole('button', { name: 'Submit' });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('loading veil marks the underlying workflow as unavailable for interaction', () => {
    render(<LoadingVeil active><button>Run dry-run</button></LoadingVeil>);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run dry-run' }).parentElement).toHaveClass('pointer-events-none');
  });

  it('table filtering never changes the underlying row source', () => {
    interface Row { id: string; status: string; score: number }
    const rows: Row[] = [
      { id: 'alpha-pass', status: 'PASS', score: 2 },
      { id: 'alpha-rejected', status: 'REJECTED_OVERFIT', score: 1 },
    ];
    const columns: Column<Row>[] = [
      { key: 'id', header: 'ID', render: row => row.id },
      { key: 'score', header: 'Score', sortable: true, sortAccessor: row => row.score, render: row => row.score },
    ];
    render(<DataTable rows={rows} columns={columns} searchable={row => `${row.id} ${row.status}`} />);
    expect(screen.getByText('alpha-pass')).toBeInTheDocument();
    expect(screen.getByText('alpha-rejected')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'rejected' } });
    expect(screen.queryByText('alpha-pass')).not.toBeInTheDocument();
    expect(screen.getByText('alpha-rejected')).toBeInTheDocument();
    expect(screen.getByText(/1 \/ 2 shown/i)).toBeInTheDocument();
  });
});
