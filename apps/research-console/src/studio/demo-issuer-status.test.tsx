import {afterEach,expect,it} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import {DemoIssuerStatus} from './DemoIssuerStatus';
import {issuerStatusValid} from '@/lib/api/demo-issuer-status-contract';
import type {DemoIssuerPolicyReport} from '@/lib/types/demo-issuer-status';
afterEach(cleanup);
const now=1800000000000;
function report():DemoIssuerPolicyReport {
  return {schema:'DEMO_ISSUER_STATUS_V1',environment:'BINANCE_USDM_DEMO',scope:'TECHNICAL_VALIDATION_ONLY',
    phase:'PENDING_SIGNATURE',observed_at_ms:now,source_commit:'a'.repeat(40),account_ref_sha256:'b'.repeat(64),
    policy_id:'isolated-test',policy_sha256:'c'.repeat(64),symbol:'BTCUSDC',valid_from_ms:now-1000,valid_until_ms:now+30000,
    key_status:'ACTIVE',key_material_status:'NOT_CHECKED',records:1,last_record_at_ms:now-1,journal_sha256:'d'.repeat(64),
    budgets:[{purpose:'submission',reserved_grants:1,signed_grants:0,reserved_orders:1,max_grants:2,max_orders:4,
      reserved_notional:'120.000',max_notional:'300',remaining_notional:'180.000'},
      {purpose:'cancellation',reserved_grants:0,signed_grants:0,reserved_orders:0,max_grants:2,max_orders:4,
        reserved_notional:null,max_notional:null,remaining_notional:null}],
    source_verified:false,submission_enabled:false,cash_reconciled:false,economic_authority:false};
}
it('shows absent issuer evidence honestly with no action that creates approval',()=>{
  render(<DemoIssuerStatus snapshot={{status:'NO_SNAPSHOT',report:null}}/>);
  expect(screen.getByText('尚无独立签发记录')).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
it('separates reserved budget from signatures and actual orders and retains stale data',()=>{
  const raw=report();const snapshot={status:'AVAILABLE' as const,report:raw};
  expect(issuerStatusValid(snapshot,now)).toBe(true);
  const view=render(<DemoIssuerStatus snapshot={snapshot} asOf={now}/>);
  expect(screen.getByRole('table',{name:'Demo 签发额度'})).toBeInTheDocument();
  expect(screen.getByText(/预留会占用额度/)).toBeInTheDocument();
  expect(screen.getByText(/签名记录不能证明订单已发送或成交/)).toBeInTheDocument();
  expect(screen.getByText('180.000 USDC')).toBeInTheDocument();
  view.rerender(<DemoIssuerStatus snapshot={snapshot} asOf={now+30000}/>);
  expect(screen.getByText(/有效期已结束/)).toBeInTheDocument();
  view.rerender(<DemoIssuerStatus snapshot={{status:'STALE',report:raw}} asOf={now+120001} refreshFailed/>);
  expect(screen.getByRole('status')).toHaveTextContent('刷新失败');
});
it('rejects incorrect sums, future evidence, hidden fields and authority',()=>{
  const raw=report();
  for (const patch of [{submission_enabled:true},{private_key:'PRIVATE'},{phase:'POLICY_OBSERVED'},
    {last_record_at_ms:now+1},{records:0},
    {budgets:raw.budgets.map((b,i)=>i===0?{...b,signed_grants:2}:b)},
    {budgets:raw.budgets.map((b,i)=>i===0?{...b,remaining_notional:'180.000000000000000001'}:b)}]) {
    expect(issuerStatusValid({status:'AVAILABLE',report:{...raw,...patch}},now)).toBe(false);
  }
  expect(issuerStatusValid({status:'AVAILABLE',report:raw},now+120001)).toBe(false);
  expect(issuerStatusValid({status:'DATA_BLOCKED',report:raw},now)).toBe(false);
});

function futureReport() {
  const {policy_id, policy_sha256, ...base}=report();
  return {...base,schema:'DEMO_ISSUER_STATUS_V2' as const,phase:'GROUP_PENDING_SIGNATURE' as const,
    symbol:'XAUUSDT' as const,template_id:'future-'+policy_id,template_sha256:policy_sha256,
    budget_basis:'SHARED_GROUP' as const,group_id:'original-group',group_binding_sha256:'f'.repeat(64),
    continuity_event_sha256:'1'.repeat(64),records:2,
    exit_reserve:{grants:1,orders:2,notional:'100'},
    opening_remaining:{grants:1,orders:2,notional:'80.000'},
    budgets:base.budgets.map((b,i)=>i===0?{...b,max_grants:3,max_orders:5}:b)};
}
it('shows the original shared group, remaining opening capacity and reserved exits separately',()=>{
  const raw=futureReport();
  expect(issuerStatusValid({status:'AVAILABLE',report:raw},now)).toBe(true);
  render(<DemoIssuerStatus snapshot={{status:'AVAILABLE',report:raw}} asOf={now}/>);
  expect(screen.getByText('80.000 USDT')).toBeInTheDocument();
  expect(screen.getByText(/退出预留：1 次签发、2 笔订单、100 USDT/)).toBeInTheDocument();
  expect(screen.getByText(/共享预算组：original-group/)).toBeInTheDocument();
  expect(screen.getByText(/模板记录不代表执行委托已启用/)).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
it('rejects future group arithmetic drift, mixed schemas and fabricated authority',()=>{
  const raw=futureReport();
  for (const patch of [{policy_id:'pretend-exact'}, {budget_basis:'PER_POLICY'},
    {submission_enabled:true}, {continuity_event_sha256:null}, {records:1},
    {opening_remaining:{...raw.opening_remaining,notional:'80.000000000000000001'}},
    {opening_remaining:{...raw.opening_remaining,orders:3}},
    {exit_reserve:{...raw.exit_reserve,grants:3}},
    {phase:'POLICY_OBSERVED'}]) {
    expect(issuerStatusValid({status:'AVAILABLE',report:{...raw,...patch}},now)).toBe(false);
  }
  expect(issuerStatusValid({status:'STALE',report:raw},now+120001)).toBe(true);
});
it('does not present the original exit reserve as a refund after it has been used',()=>{
  const base=futureReport();
  const raw={...base,phase:'TEMPLATE_OBSERVED' as const,records:6,
    opening_remaining:{grants:0,orders:1,notional:'0'},
    budgets:base.budgets.map((b,i)=>i===0?{...b,reserved_grants:2,signed_grants:2,reserved_orders:2,
      reserved_notional:'250',remaining_notional:'50'}:b)};
  expect(issuerStatusValid({status:'AVAILABLE',report:raw},now)).toBe(true);
  render(<DemoIssuerStatus snapshot={{status:'AVAILABLE',report:raw}} asOf={now}/>);
  expect(screen.getByText('0 USDT')).toBeInTheDocument();
  expect(screen.getByText(/共享提交名义余量：50 USDT/)).toBeInTheDocument();
  expect(screen.getByText(/预留为原始配置/)).toBeInTheDocument();
});
