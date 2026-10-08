import {fireEvent,render,screen} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
import {BinanceWallets} from './BinanceWallets';
import {fetchConsoleModuleSnapshot} from '@/lib/api/research-console-api';
vi.mock('@/lib/api/research-console-api',()=>({fetchConsoleModuleSnapshot:vi.fn()}));
describe('account stablecoin overview',()=>{
 it('switches currencies without converting or combining USDT and USDC',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({wallet_status:'OBSERVED',wallets:{status:'OBSERVED',observed_at_ms:1000,
   totals:{USDT:'13.5',USDC:'99.8'},accounts:Object.fromEntries(['futures','spot','funding'].map(k=>[k,{status:'OBSERVED',observed_at_ms:1000,balances:[]}]))}});
  render(<BinanceWallets/>);
  expect(await screen.findByText('13.5')).toBeInTheDocument();
  expect(screen.queryByText('99.8')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'USDC'}));
  expect(screen.getByText('99.8')).toBeInTheDocument();
  expect(screen.queryByText('113.3')).not.toBeInTheDocument();
 });
 it('keeps missing wallet balances unknown',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({wallet_status:'NOT_CONNECTED',wallets:null});
  render(<BinanceWallets/>);
  expect(await screen.findByText('尚未读到账户资金资料。')).toBeInTheDocument();
  expect(screen.queryByRole('img',{name:/账户资金分布/})).not.toBeInTheDocument();
 });
});
