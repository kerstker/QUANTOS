import {afterEach, expect, it} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';
import {DemoCanonicalCash} from './DemoCanonicalCash';
import {canonicalCashValid} from '@/lib/api/demo-canonical-cash-contract';
import type {DemoCanonicalCashReport} from '@/lib/types/demo-canonical-cash';

afterEach(cleanup);
const at = 1789170110000;
function report(): DemoCanonicalCashReport {
  return {schema:'DEMO_CANONICAL_CASH_COMPARISON_V1',environment:'BINANCE_USDM_DEMO',scope:'TECHNICAL_VALIDATION_ONLY',
    status:'MATCHED_REPORTED_INPUTS',account_ref_sha256:'a'.repeat(64),symbol:'BTCUSDC',quote_asset:'USDC',
    manifest_hash:'b'.repeat(64),journal_tail:'c'.repeat(64),cash_capture_sha256:'d'.repeat(64),
    replay_source_sha256:'e'.repeat(64),projection_source_sha256:'f'.repeat(64),cash_input_status:'MATCHED_REPORTED_WINDOW',
    cash_input_duplicates:0,observed_at_ms:at,issues:[],canonical_wallet:'1099.7802',reported_wallet:'1099.7802',wallet_residual:'0',
    fee_comparison:'MATCHED_REPORTED_TRADES',realized_pnl_comparison:'MATCHED_REPORTED_TOTAL',
    reconciliation:{initial_equity:'1000',ending_equity:'1099.7802',fee:'0.0198',funding:'-0.2',external_cash_flow:'100',
      price_pnl:'0',residual:'0',trading_and_funding_change:'-0.2198',reported_cash_policy:'REPORTED_QUOTE_CASH_AFTER_FILLS_V1'},
    ledger_state_hash:'a'.repeat(64),source_verified:false,coverage_verified:false,authoritative:false,submission_enabled:false,
    strategy_pnl:null,limitations:['IMPORTED_SOURCE_UNVERIFIED']};
}
it('shows exact ledger costs, external money and the limits of a reported match', () => {
  const r=report();expect(canonicalCashValid({status:'AVAILABLE',report:r},at)).toBe(true);
  render(<DemoCanonicalCash snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('报告输入与唯一账本相符')).toBeInTheDocument();
  expect(screen.getByText('0.0198')).toBeInTheDocument();
  expect(screen.getByText('100')).toBeInTheDocument();
  expect(screen.getByText(/转入转出单独列示/)).toBeInTheDocument();
  expect(screen.getByText(/不代表策略盈利/)).toBeInTheDocument();
});
it('keeps unresolved fees and a one-quantum difference visible', () => {
  const r=report();r.status='DIFFERENCE_FOUND';r.issues=['CANONICAL_WALLET_DIFFERENCE'];r.wallet_residual='-0.00000001';
  const v=render(<DemoCanonicalCash snapshot={{status:'AVAILABLE',report:r}} refreshFailed/>);
  expect(screen.getByText('-0.00000001')).toBeInTheDocument();
  expect(screen.getByText(/刷新失败/)).toBeInTheDocument();
  r.status='UNRESOLVED';r.issues=['COMMISSION_TRADE_SCOPE_MISMATCH'];r.reconciliation=null;r.canonical_wallet=null;r.wallet_residual=null;r.ledger_state_hash=null;
  v.rerender(<DemoCanonicalCash snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText(/佣金流水尚未逐笔对应成交/)).toBeInTheDocument();
  expect(screen.getAllByText('待核对').length).toBeGreaterThan(0);
});
it('rejects inflated authority, incomplete matches, future data and rounded numeric cash', () => {
  for (const patch of [{authoritative:true},{strategy_pnl:'1'},{coverage_verified:true},{source_verified:true},
    {observed_at_ms:at+1},{wallet_residual:'0.00000001'},{canonical_wallet:1099.7802},{reconciliation:null},
    {issues:['CANONICAL_WALLET_DIFFERENCE']},{fee_comparison:'UNKNOWN'}]) {
    expect(canonicalCashValid({status:'AVAILABLE',report:{...report(),...patch}},at)).toBe(false);
  }
});
