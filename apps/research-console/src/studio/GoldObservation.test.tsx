import {describe,expect,it,vi} from 'vitest';
import {render,screen} from '@testing-library/react';
import {fetchConsoleModuleSnapshot} from '@/lib/api/research-console-api';
import {GoldObservation} from './GoldObservation';

vi.mock('@/lib/api/research-console-api',()=>({fetchConsoleModuleSnapshot:vi.fn()}));
describe('gold observation honesty',()=>{
 it('keeps stale public prices separate from an unconnected account',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'NO_SNAPSHOT',fills:null,
   market_status:'STALE',market:{last_trade_price:'4000',last_event_ms:1000,unique_aggregates:2}});
  render(<GoldObservation/>);
  expect(await screen.findByText(/行情记录已过期/)).toBeInTheDocument();
  expect(screen.getByText('最近记录的成交价').nextElementSibling).toHaveTextContent('4000');
  expect(screen.getByText('尚未导入黄金成交资料')).toBeInTheDocument();
  expect(screen.getByText(/账户实时连接尚未接通/)).toBeInTheDocument();
 });
 it('shows cash discrepancies independently and never labels them net profit',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'NO_SNAPSHOT',fills:null,
   cash_status:'STALE',cash_reconciliation_status:'TOTALS_DIFFER',cash:{
    start_ms:1000,end_ms:2000,exported_at_ms:3000,unique_events:3,duplicate_events:0,
    coverage_age_ms:200000,funding_by_asset:{USDT:'-0.1'}}});
  render(<GoldObservation/>);
  expect(await screen.findByText(/流水覆盖时间已过期/)).toBeInTheDocument();
  expect(screen.getByText(/成交与流水金额不符/)).toBeInTheDocument();
  expect(screen.getByText('去重后资金记录').nextElementSibling).toHaveTextContent('3');
  expect(screen.getByText(/当前持仓与账户净盈亏未知/)).toBeInTheDocument();
 });
 it('labels recomputed signals as historical even without account data',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'NO_SNAPSHOT',fills:null,
   signal_status:'INPUT_INCOMPLETE',signal:{decision_count:1,entry_signal_count:0,last_decision_ms:2010}});
  render(<GoldObservation/>);
  expect(await screen.findByText(/历史回放输入不完整/)).toBeInTheDocument();
  expect(screen.getByText('已重算的历史决策').nextElementSibling).toHaveTextContent('1');
  expect(screen.getByText(/不是当前入场建议/)).toBeInTheDocument();
 });
 it('keeps conflicting and unlinked fills visible in association review',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'NO_SNAPSHOT',fills:null,
   links_status:'DECLARED_ASSOCIATIONS_ONLY',links:{declared_entry_fills:1,declared_exit_fills:0,
    fills_by_status:{DECLARED_LINK:1,DIRECTION_CONFLICT:2,UNLINKED:3},
    orphan_annotations:1,signals_without_declared_entry:2}});
  render(<GoldObservation/>);
  expect(await screen.findByText(/仅为事后声明关联/)).toBeInTheDocument();
  expect(screen.getByText('方向冲突').nextElementSibling).toHaveTextContent('2');
  expect(screen.getByText('未关联成交').nextElementSibling).toHaveTextContent('3');
  expect(screen.getByText(/不证明策略采纳或收益归属/)).toBeInTheDocument();
 });
 it('shows missing account information instead of zero returns',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'NO_SNAPSHOT',fills:null});
  render(<GoldObservation/>);
  expect(await screen.findByText('尚未导入黄金成交资料')).toBeInTheDocument();
  expect(screen.getByText(/当前持仓与账户净盈亏未知/)).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'下单'})).not.toBeInTheDocument();
 });
 it('labels old records as historical even when present',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'STALE',fills:{
   start_ms:1000,end_ms:2000,exported_at_ms:3000,unique_fills:3,duplicate_fills:1,
  }});
  render(<GoldObservation/>);
  expect(await screen.findByText('成交覆盖时间已过期，仅供历史查看')).toBeInTheDocument();
  expect(screen.getByText('去重后成交记录').nextElementSibling).toHaveTextContent('3');
 });
 it('hides previous facts when the API fails',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockRejectedValue(new Error('offline'));
  render(<GoldObservation/>);
  expect(await screen.findByText('offline')).toBeInTheDocument();
  expect(screen.queryByText('去重后成交记录')).not.toBeInTheDocument();
 });
});

describe('account monitor view',()=>{
 it('shows stale connection and recovery discrepancy separately from observed positions',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'NO_SNAPSHOT',fills:null,
   monitor_status:'STALE',monitor:{observed_at_ms:1000,event_count:4,unique_trades:1,gap_count:2,
    recovery_status:'MISMATCH',recovered_fills:2,income_records:3,recent_trades:[],
    positions:[{side:'BOTH',quantity:'0.01',entry_price:'4000',observed_at_ms:900,source:'SNAPSHOT'}]}});
  render(<GoldObservation/>);
  expect(await screen.findByText(/监控状态已过期/)).toBeInTheDocument();
  expect(screen.getByText(/核对发现差异/)).toBeInTheDocument();
  expect(screen.getByText('BOTH 持仓观察值').nextElementSibling).toHaveTextContent('0.01');
  expect(screen.getByText(/历史完整性仍未确认/)).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'下单'})).not.toBeInTheDocument();
 });
});
