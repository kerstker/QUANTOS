import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import type {DemoFunctionalReport} from '@/lib/types/demo-functional-status';
import {DemoControlCenter} from './DemoControlCenter';

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
const report = (phase: DemoFunctionalReport['phase']): DemoFunctionalReport => ({
  schema:'DEMO_FUNCTIONAL_STATUS_V1',environment:'BINANCE_USDM_DEMO',scope:'TECHNICAL_VALIDATION_ONLY',
  phase,observed_at_ms:1800000000000,source_commit:'c'.repeat(40),cycle_sha256:'a'.repeat(64),
  orders_attempted:2,final_quantity:phase==='REDUCTION_INCOMPLETE'?'0.001':'0',fill_count:2,
  cash_status:phase==='DEMO_CYCLE_COMPLETED'?'MATCHED_REPORTED_INPUTS':null,
  cash_reconciled:phase==='DEMO_CYCLE_COMPLETED',economic_authority:false,submission_enabled:false,
});
it('provides an actionable first-use guide while missing records remain unknown', () => {
  render(<DemoControlCenter connected={false} snapshot={{status:'NO_SNAPSHOT',report:null}}/>);
  expect(screen.getByRole('heading',{name:'从这里开始 DEMO 测试'})).toBeInTheDocument();
  expect(screen.getByText('尚未收到交易周期记录')).toBeInTheDocument();
  expect(screen.getByLabelText('DGX 本机启动命令')).toHaveValue('sudo /usr/local/sbin/quantos-demo-functional');
  expect(screen.getByText(/如果原终端已在等待登记/)).toBeInTheDocument();
  expect(screen.queryByRole('textbox',{name:/确认|密钥/})).not.toBeInTheDocument();
  expect(screen.getByRole('link',{name:/连接与账户/})).toHaveAttribute('href','#demo-connection');
  expect(screen.getByRole('link',{name:/现金与对账/})).toHaveAttribute('href','#demo-cash');
});
it('copies only the selected launch command, without issuing any request', async () => {
  const copy=vi.fn().mockResolvedValue(undefined),request=vi.fn();
  vi.stubGlobal('navigator',{clipboard:{writeText:copy}});vi.stubGlobal('fetch',request);
  render(<DemoControlCenter connected snapshot={{status:'NO_SNAPSHOT',report:null}}/>);
  fireEvent.click(screen.getByRole('button',{name:'另一台电脑'}));
  const command='ssh -t -o GSSAPIAuthentication=no kerstker@192.168.5.68 "sudo /usr/local/sbin/quantos-demo-functional"';
  expect(screen.getByLabelText('另一台电脑启动命令')).toHaveValue(command);
  fireEvent.click(screen.getByRole('button',{name:'复制启动命令'}));
  await waitFor(()=>expect(screen.getByRole('status')).toHaveTextContent('命令已复制；请在终端执行。'));
  expect(copy).toHaveBeenCalledWith(command);expect(request).not.toHaveBeenCalled();
});
it('selects the command for manual copying when clipboard access is unavailable on LAN HTTP', async () => {
  vi.stubGlobal('navigator',{});
  render(<DemoControlCenter connected snapshot={{status:'NO_SNAPSHOT',report:null}}/>);
  fireEvent.click(screen.getByRole('button',{name:'复制启动命令'}));
  await waitFor(()=>expect(screen.getByRole('status')).toHaveTextContent('命令已选中'));
  const field=screen.getByLabelText('DGX 本机启动命令') as HTMLTextAreaElement;
  expect(field).toHaveFocus();expect(field.selectionEnd-field.selectionStart).toBe(field.value.length);
  expect(screen.queryByText('命令已复制；请在终端执行。')).not.toBeInTheDocument();
});
it('does not recommend a new opening when a cycle is running or needs recovery', () => {
  const view=render(<DemoControlCenter connected snapshot={{status:'AVAILABLE',report:report('OPEN_IOC')}}/>);
  expect(screen.getByRole('heading',{name:'DEMO 测试正在执行'})).toBeInTheDocument();
  expect(screen.queryByLabelText('DGX 本机启动命令')).not.toBeInTheDocument();
  view.rerender(<DemoControlCenter connected snapshot={{status:'AVAILABLE',report:report('RECOVERY_REQUIRED')}}/>);
  expect(screen.getByRole('heading',{name:'先恢复本轮订单结果'})).toBeInTheDocument();
  expect(screen.getByText(/保留原订单编号/)).toBeInTheDocument();
  expect(screen.getByRole('link',{name:'查看订单与恢复'})).toHaveAttribute('href','#demo-orders');
});
it('shows failed and expired observations explicitly instead of current completion', () => {
  const view=render(<DemoControlCenter connected snapshot={{status:'AVAILABLE',report:report('DEMO_CYCLE_COMPLETED')}} refreshFailed/>);
  expect(screen.getByRole('heading',{name:'当前进度需要重新核查'})).toBeInTheDocument();
  expect(screen.queryByRole('heading',{name:'本轮 DEMO 闭环已完成'})).not.toBeInTheDocument();
  view.rerender(<DemoControlCenter connected snapshot={{status:'STALE',report:report('REDUCE_ONLY_IOC')}}/>);
  expect(screen.getByRole('heading',{name:'当前进度需要重新核查'})).toBeInTheDocument();
  view.rerender(<DemoControlCenter connected snapshot={{status:'CORRUPT_OR_UNREADABLE',report:null}}/>);
  expect(screen.getByRole('heading',{name:'测试记录需要检查'})).toBeInTheDocument();
  expect(screen.queryByLabelText('DGX 本机启动命令')).not.toBeInTheDocument();
});
it('keeps financial completion distinct from connectivity and partial fills', () => {
  const view=render(<DemoControlCenter connected snapshot={{status:'AVAILABLE',report:report('REDUCTION_INCOMPLETE')}}/>);
  expect(screen.getByRole('heading',{name:'仍有持仓需要减仓'})).toBeInTheDocument();
  view.rerender(<DemoControlCenter connected snapshot={{status:'AVAILABLE',report:report('CASH_RECONCILIATION_REQUIRED')}}/>);
  expect(screen.getByRole('heading',{name:'成交已结束，现金仍待核对'})).toBeInTheDocument();
  view.rerender(<DemoControlCenter connected snapshot={{status:'AVAILABLE',report:report('DEMO_CYCLE_COMPLETED')}}/>);
  expect(screen.getByRole('heading',{name:'本轮 DEMO 闭环已完成'})).toBeInTheDocument();
  expect(within(screen.getByRole('navigation',{name:'DEMO 工作区'})).getAllByRole('link')).toHaveLength(5);
  expect(screen.queryByLabelText('DGX 本机启动命令')).not.toBeInTheDocument();
});
