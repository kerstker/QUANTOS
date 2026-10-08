import {render,screen} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
import {fetchConsoleModuleSnapshot} from '@/lib/api/research-console-api';
import {KrakenPortfolio} from './KrakenPortfolio';
vi.mock('@/lib/api/research-console-api',()=>({fetchConsoleModuleSnapshot:vi.fn()}));
describe('Kraken observation flow',()=>{
 it('shows waiting policy without implying trading is active',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'REAL',session_status:'OBSERVATION_COMPLETE',observed_at_ms:1800000000000,
   stages:[{id:'portfolio',status:'WAITING_POLICY'}],blockers:[],submission_enabled:false,authoritative:false});
  render(<KrakenPortfolio/>);
  expect(await screen.findByText('等待共同确定策略',{exact:false})).toBeInTheDocument();
  expect(screen.getByText(/自动交易尚未接通/)).toBeInTheDocument();
 });
 it('does not display old completed stages when the snapshot is stale',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'STALE',session_status:'OBSERVATION_COMPLETE',
   stages:[{id:'cash',status:'BALANCE_MOVEMENTS_MATCHED'}],blockers:[]});
  render(<KrakenPortfolio/>);
  expect(await screen.findByText('会话记录已过期')).toBeInTheDocument();
  expect(screen.queryByText('余额变动已核对')).not.toBeInTheDocument();
 });
});
