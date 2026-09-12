import {afterEach, expect, it} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';
import {DemoResidentStatus} from './DemoResidentStatus';
import {residentStatusValid} from '@/lib/api/demo-resident-status-contract';
import type {DemoResidentStatusReport} from '@/lib/types/demo-resident-status';
afterEach(cleanup);
const now = 1800000000000;
function report(): DemoResidentStatusReport {
  return {schema:'DEMO_RESIDENT_STATUS_V1', environment:'BINANCE_USDM_DEMO', scope:'TECHNICAL_VALIDATION_ONLY',
    phase:'WAITING_QUERY_INTERVAL', reason:null, observed_at_ms:now, executor_boot_sha256:'a'.repeat(64), source_commit:'b'.repeat(40),
    attempt_count:2, last_attempt_at_ms:now-1000, next_query_at_ms:now+29000, reservation_sha256:null,
    query_status:null, query_reason:null, query_sha256:null, resolution_status:null, resolution_issues:[], resolution_sha256:null,
    report_sha256:'c'.repeat(64), source_verified:false, new_order_submission_enabled:false, economic_authority:false, cash_reconciled:false};
}
it('shows the last failed query while the current worker waits, with original timestamps', () => {
  const r = report();
  const previous = {...r, phase:'QUERY_RECOVERY_REQUIRED' as const, observed_at_ms:now-500,
    query_status:'QUERY_RECOVERY_REQUIRED' as const, query_reason:'QUERY_CLOCK_UNRELIABLE', query_sha256:'d'.repeat(64)};
  const snapshot = {status:'AVAILABLE' as const, report:r, last_result:previous};
  expect(residentStatusValid(snapshot, now)).toBe(true);
  render(<DemoResidentStatus snapshot={snapshot} asOf={now} refreshFailed={false}/>);
  expect(screen.getByText('等待下一次原编号查询')).toBeInTheDocument();
  expect(screen.getByText(/上次查询：时钟依据未通过/)).toBeInTheDocument();
  expect(screen.getByText('2027-01-15T08:00:29.000Z')).toBeInTheDocument();
  expect(screen.queryByText('恢复成功')).not.toBeInTheDocument();
});
it('does not label an old heartbeat as a running executor or missing input as zero attempts', () => {
  const r = report();
  render(<DemoResidentStatus snapshot={{status:'AVAILABLE', report:r, last_result:null}} asOf={now+120001} refreshFailed={false}/>);
  expect(screen.getByText('恢复工作者心跳已过期')).toBeInTheDocument();
  cleanup();
  render(<DemoResidentStatus asOf={now} refreshFailed={false}/>);
  expect(screen.getByText('等待驻留恢复记录')).toBeInTheDocument();
  expect(screen.queryByText('0 次')).not.toBeInTheDocument();
});
it('rejects future evidence, altered schedule, invalid booleans and invented authority', () => {
  const r = report();
  for (const change of [{cash_reconciled:true}, {attempt_count:true}, {attempt_count:0}, {next_query_at_ms:now+1},
    {observed_at_ms:now+1}, {query_status:'ORDER_SNAPSHOTS_OBSERVED'}, {phase:'LIVE_READY'}, {extra:'field'}]) {
    expect(residentStatusValid({status:'AVAILABLE', report:{...r,...change}, last_result:null}, now)).toBe(false);
  }
  expect(residentStatusValid({status:'AVAILABLE', report:r, last_result:{...r,observed_at_ms:now+1}}, now)).toBe(false);
});
