import {afterEach,expect,it} from 'vitest';
import {cleanup,render,screen,within} from '@testing-library/react';
import {DemoExecutorObservation} from './DemoExecutorObservation';
import {executorObservationValid} from '@/lib/api/demo-executor-observation-contract';
import {executorComponents,type DemoExecutorObservationReport} from '@/lib/types/demo-executor-observation';
afterEach(cleanup);
const now=1800000000000;
function report():DemoExecutorObservationReport {
  return {schema:'DEMO_EXECUTOR_OBSERVATION_V1',environment:'BINANCE_USDM_DEMO',scope:'TECHNICAL_VALIDATION_ONLY',phase:'PARTIAL_RECORDS',
    observed_at_ms:now,account_ref_sha256:'a'.repeat(64),source_commit:'b'.repeat(40),source_verified:false,submission_enabled:false,cash_reconciled:false,economic_authority:false,
    components:executorComponents.map(name=>name==='private_stream'?{name,status:'AVAILABLE',phase:'DISCONNECTED',observed_at_ms:now-1,report_sha256:'c'.repeat(64),
      counts:{records:5,messages:2,unique_messages:1,gaps:1}}:{name,status:'NO_SNAPSHOT',phase:null,observed_at_ms:null,report_sha256:null,counts:null})};
}
it('keeps research observations separate from missing executor data',()=>{
  render(<DemoExecutorObservation snapshot={{status:'NO_SNAPSHOT',report:null}} asOf={now}/>);
  expect(screen.getByText('尚无执行侧观测')).toBeInTheDocument();
  expect(screen.getByText(/研究端的数据采集仍可运行/)).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
it('retains original duplicates and gaps and never infers readiness from record presence',()=>{
  const raw=report();const snapshot={status:'AVAILABLE' as const,report:raw};
  expect(executorObservationValid(snapshot,now)).toBe(true);
  const view=render(<DemoExecutorObservation snapshot={snapshot} asOf={now}/>);
  const list=screen.getByRole('list',{name:'执行侧原始记录状态'});
  expect(within(list).getAllByRole('listitem')).toHaveLength(6);
  expect(list.querySelector('time')).toHaveAttribute('datetime','2027-01-15T07:59:59.999Z');
  expect(list.querySelector('time')?.textContent).toMatch(/01\/15/);
  expect(screen.getByText('连接已中断')).toBeInTheDocument();
  expect(screen.getByText('重复投递')).toBeInTheDocument();
  expect(screen.getByText('已记录缺口')).toBeInTheDocument();
  expect(screen.getByText(/记录存在不代表执行验收或现金对账完成/)).toBeInTheDocument();
  view.rerender(<DemoExecutorObservation snapshot={snapshot} asOf={now} refreshFailed/>);
  expect(screen.getByRole('status')).toHaveTextContent('刷新失败');
  view.rerender(<DemoExecutorObservation snapshot={{status:'STALE',report:raw}} asOf={now+120001}/>);
  expect(screen.getByRole('status')).toHaveTextContent('历史记录');
});
it('rejects fabricated authority, hidden invalid observations and mismatched freshness',()=>{
  const raw=report();
  for (const patch of [{source_verified:true},{submission_enabled:true},{cash_reconciled:true},{economic_authority:true},
    {phase:'RECORDS_OBSERVED'},{observed_at_ms:now+1},{account_ref_sha256:'not-a-hash'},
    {components:[...raw.components].reverse()},
    {components:raw.components.map((row,i)=>i===0?{...row,counts:{...row.counts,unique_messages:3}}:row)},
    {components:raw.components.map((row,i)=>i===1?{...row,phase:'OBSERVED_FLAT_QUOTE'}:row)}]) {
    expect(executorObservationValid({status:'AVAILABLE',report:{...raw,...patch}},now)).toBe(false);
  }
  expect(executorObservationValid({status:'AVAILABLE',report:raw},now+120001)).toBe(false);
  expect(executorObservationValid({status:'STALE',report:raw},now)).toBe(false);
  expect(executorObservationValid({status:'DATA_BLOCKED',report:raw},now)).toBe(false);
});
it('marks an old origin independently even when the exporter is fresh',()=>{
  const raw=report();raw.components[1]={...raw.components[1],status:'AVAILABLE',phase:'OBSERVED_FLAT_QUOTE',observed_at_ms:now-30001,report_sha256:'d'.repeat(64)};
  render(<DemoExecutorObservation snapshot={{status:'AVAILABLE',report:raw}} asOf={now}/>);
  expect(screen.getByText(/观察已过期/)).toBeInTheDocument();
  expect(screen.getByText(/最近观察/)).toBeInTheDocument();
});
