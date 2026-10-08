import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import type {BinanceDemoPayload} from '@/lib/types/binance-demo';
import {DemoRuntimeFlow,runtimeNodes} from './DemoRuntimeFlow';
afterEach(()=>{cleanup();vi.useRealTimers();});
const now=1800000000000;
function data():BinanceDemoPayload {
  return {as_of_ms:now,snapshot_status:'REAL',market_inputs:{BTCUSDC:{status:'AVAILABLE',report:{observed_at_ms:now-1000,capture_status:'CAPTURED',metrics:{bars:[{}]}}}},
    execution_origin:{status:'AVAILABLE',report:{observed_at_ms:now-1000,status:'UNRESOLVED',issues:['ORIGIN_STALE']}},
    portfolio_cycle:{status:'NO_SNAPSHOT',report:null},portfolio_cash:{status:'NO_SNAPSHOT',report:null},
    backup_status:{status:'NO_SNAPSHOT',report:null},functional_cycle:{status:'AVAILABLE',report:{phase:'DEMO_CYCLE_COMPLETED'}}} as unknown as BinanceDemoPayload;
}
it('keeps historical completion out of the current portfolio flow and exposes blocked inputs',()=>{
  const nodes=runtimeNodes(data(),now,false);
  expect(nodes.find(n=>n.id==='market')?.state).toBe('ready');
  expect(nodes.find(n=>n.id==='account')?.state).toBe('blocked');
  expect(nodes.find(n=>n.id==='cash')?.state).toBe('missing');
  expect(nodes.find(n=>n.id==='research')?.state).toBe('missing');
});
it('stops indicating live data when refresh fails or timestamps age or come from the future',()=>{
  expect(runtimeNodes(data(),now,true).every(n=>n.state!=='ready')).toBe(true);
  expect(runtimeNodes(data(),now+121000,false).find(n=>n.id==='market')?.state).toBe('stale');
  expect(runtimeNodes(data(),now-2000,false).find(n=>n.id==='market')?.state).toBe('stale');
});
it('does not call a prepared task a filled order',()=>{
  const d=data();d.portfolio_cycle={status:'AVAILABLE',report:{observed_at_ms:now,status:'TASK_READY',task_sha256:'a'.repeat(64),fill_count:0}} as BinanceDemoPayload['portfolio_cycle'];
  expect(runtimeNodes(d,now,false).find(n=>n.id==='execution')?.state).toBe('waiting');
});
it('requires current matching cash evidence for completion',()=>{
  const d=data();d.portfolio_cycle={status:'AVAILABLE',report:{observed_at_ms:now,status:'CASH_RECONCILED',cash_reconciled:true,cash_report_sha256:'a',final_quantity:'0',fill_count:2}} as BinanceDemoPayload['portfolio_cycle'];
  d.portfolio_cash={status:'AVAILABLE',report:{observed_at_ms:now,report_sha256:'b'}} as BinanceDemoPayload['portfolio_cash'];
  expect(runtimeNodes(d,now,false).find(n=>n.id==='cash')?.state).toBe('blocked');
});
it('lets operators inspect a blocked node and pause movement without issuing commands',()=>{
  vi.useFakeTimers();vi.setSystemTime(now);render(<DemoRuntimeFlow data={data()} refreshFailed={false}/>);
  fireEvent.click(screen.getByRole('button',{name:/账户核验/}));
  expect(screen.getByText('ORIGIN_STALE')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'暂停动画'}));
  expect(screen.getByRole('button',{name:'恢复动画'})).toHaveAttribute('aria-pressed','true');
});
