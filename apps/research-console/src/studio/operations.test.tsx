import {render, screen, fireEvent} from '@testing-library/react';
import {describe, it, expect, vi} from 'vitest';
import {MemoryRouter} from '@/lib/router';
import {fetchConsoleModuleSnapshot} from '@/lib/api/research-console-api';
import {Operations} from './Analysis';

vi.mock('@/lib/api/research-console-api', () => ({fetchConsoleModuleSnapshot: vi.fn()}));

describe('operational receipts', () => {
 it('shows failure reasons and removes old success content when refresh fails', async () => {
  vi.mocked(fetchConsoleModuleSnapshot).mockImplementation(async endpoint => (
   endpoint.includes('system-health') ? {services:[{
    service:'l2-backup-restore',status:'down',reason_code:'RESTORE_RTO_EXCEEDED',
    probe:'RUNTIME_RECEIPT:BREACH:age_ms=1000',age_ms:1000,observed_at_ms:1788882828942,
   }]} : {}
  ) as never);
  render(<MemoryRouter><Operations runs={{rows:[],loading:false,error:null,available:true,refresh:vi.fn()}}/></MemoryRouter>);
  expect(await screen.findByText('备份恢复')).toBeInTheDocument();
  expect(screen.getByText('恢复耗时超过目标')).toBeInTheDocument();
  expect(screen.getByText('RESTORE_RTO_EXCEEDED')).toBeInTheDocument();
  expect(screen.getByText('失败')).toHaveClass('bad');
  vi.mocked(fetchConsoleModuleSnapshot).mockRejectedValue(new Error('offline'));
  fireEvent.click(screen.getByRole('button',{name:'刷新状态'}));
  expect(await screen.findByText('offline')).toBeInTheDocument();
  expect(screen.queryByText('RESTORE_RTO_EXCEEDED')).not.toBeInTheDocument();
 });
});
