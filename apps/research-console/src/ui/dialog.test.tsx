import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmDialogHost, useConfirm } from './dialog';

function Action({ done, typeToConfirm }: { done: (value: boolean) => void; typeToConfirm?: string }) {
  const confirm = useConfirm();
  return <button onClick={() => {
    void confirm({ title: 'Confirm action', confirmLabel: 'Proceed', cancelLabel: 'Cancel', typeToConfirm }).then(done);
  }}>Open</button>;
}

describe('confirmation dialog behavior', () => {
  it('requires exact confirmation text, accepts Enter and clears input on reopening', async () => {
    const done = vi.fn();
    render(<ConfirmDialogHost><Action done={done} typeToConfirm="ACK" /></ConfirmDialogHost>);
    fireEvent.click(screen.getByText('Open'));
    expect(screen.getByRole('button', { name: 'Proceed' })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: ' ACK ' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(done).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Proceed' })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'ACK' } });
    expect(screen.getByRole('button', { name: 'Proceed' })).toBeEnabled();
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(done).toHaveBeenCalledExactlyOnceWith(true));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Open'));
    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Proceed' })).toBeDisabled();
  });

  it.each(['confirm', 'cancel', 'escape', 'backdrop'])('resolves correctly via %s', async mode => {
    const done = vi.fn();
    render(<ConfirmDialogHost><Action done={done} /></ConfirmDialogHost>);
    fireEvent.click(screen.getByText('Open'));
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    if (mode === 'escape') fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    else if (mode === 'backdrop') fireEvent.click(screen.getByRole('dialog'));
    else fireEvent.click(screen.getByRole('button', { name: mode === 'confirm' ? 'Proceed' : 'Cancel' }));
    await waitFor(() => expect(done).toHaveBeenCalledExactlyOnceWith(mode === 'confirm'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
