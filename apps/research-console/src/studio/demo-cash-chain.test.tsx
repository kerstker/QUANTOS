import {afterEach, expect, it} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';
import {DemoCashChain} from './DemoCashChain';
import {cashChainValid} from '@/lib/api/demo-cash-chain-contract';
import type {DemoCashChainReport} from '@/lib/types/demo-cash-chain';

afterEach(cleanup);
it('shows explicit gold cash scope and rejects relabeling it as a BTC record',()=>{
  const r:DemoCashChainReport={...report(),schema:'DEMO_GOLD_CASH_CHAIN_STATUS_V1',symbol:'XAUUSDT',quote_asset:'USDT'};
  expect(cashChainValid({status:'AVAILABLE',report:r},at)).toBe(true);
  render(<DemoCashChain snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('XAU；固定输入的期末状态')).toBeInTheDocument();
  expect(cashChainValid({status:'AVAILABLE',report:{...r,schema:'DEMO_CASH_CHAIN_STATUS_V1'}},at)).toBe(false);
  expect(cashChainValid({status:'AVAILABLE',report:{...r,quote_asset:'USDC'}},at)).toBe(false);
});
const at=1789170110000;
function report(): DemoCashChainReport {
  return {schema:'DEMO_CASH_CHAIN_STATUS_V1',environment:'BINANCE_USDM_DEMO',scope:'TECHNICAL_VALIDATION_ONLY',
    phase:'MATCHED_REPORTED_INPUTS',observed_at_ms:at,account_ref_sha256:'a'.repeat(64),symbol:'BTCUSDC',quote_asset:'USDC',
    batch_count:2,continuations_matched:1,issues:[],ledger_state_hash:'b'.repeat(64),strategy_pnl:null,
    source_verified:false,coverage_verified:false,authoritative:false,submission_enabled:false,cash_reconciled:false,
    report_sha256:'c'.repeat(64),cash_capture_sha256:'d'.repeat(64),replay_source_sha256:'e'.repeat(64),projection_source_sha256:'f'.repeat(64),
    initial_equity:'1000',canonical_wallet:'1104.1714',reported_wallet:'1104.1714',wallet_residual:'0',
    quantity:'0.6',entry_notional:'59.4',fee:'0.0286',funding:'-0.2',external_cash_flow:'100',
    batches:[0,1].map(i=>({manifest_hash:String(i).repeat(64),journal_tail:String(i+1).repeat(64),orders_count:1,trades_count:i===0?2:1,
      start_ms:at-100000+i*40000,end_ms:at-30000+i*25000}))};
}
it('shows the original cash origin, retained position basis and separate external funds',()=>{
  const r=report();expect(cashChainValid({status:'AVAILABLE',report:r},at)).toBe(true);
  render(<DemoCashChain snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('跨批次账本输入相符')).toBeInTheDocument();
  expect(screen.getByText('59.4')).toBeInTheDocument();
  expect(screen.getByText('0.0286')).toBeInTheDocument();
  expect(screen.getByText(/没有重置原始本金/)).toBeInTheDocument();
  expect(screen.getByText(/不是盈利/)).toBeInTheDocument();
});
it('preserves a missing window and refresh failure without claiming a current position',()=>{
  const r=report();r.phase='UNRESOLVED';r.issues=['RECEIPT_CHAIN_GAP_OR_ORDER'];r.continuations_matched=0;r.ledger_state_hash=null;
  r.initial_equity=r.canonical_wallet=r.wallet_residual=r.quantity=r.entry_notional=r.fee=r.funding=r.external_cash_flow=null;
  expect(cashChainValid({status:'AVAILABLE',report:r},at)).toBe(true);
  render(<DemoCashChain snapshot={{status:'AVAILABLE',report:r}} refreshFailed/>);
  expect(screen.getByText(/窗口存在断档/)).toBeInTheDocument();
  expect(screen.getByText(/刷新失败/)).toBeInTheDocument();
  expect(screen.getAllByText('待核对').length).toBeGreaterThan(0);
});
it('rejects authority, future availability, missing basis and false matches without rounding money',()=>{
  for(const patch of [{cash_reconciled:true},{submission_enabled:true},{source_verified:true},{strategy_pnl:'4'},
    {observed_at_ms:at+1},{quantity:null},{entry_notional:59.4},{wallet_residual:'0.00000001'},
    {canonical_wallet:'1104.171400000000000000001'},{continuations_matched:0},{batch_count:true},{issues:['X']}]){
    expect(cashChainValid({status:'AVAILABLE',report:{...report(),...patch}},at)).toBe(false);
  }
});
it('keeps missing input distinct from a matched zero balance',()=>{
  render(<DemoCashChain snapshot={{status:'NO_SNAPSHOT',report:null}} refreshFailed={false}/>);
  expect(screen.getByText('等待连续批次与现金窗口')).toBeInTheDocument();
});
