import {fireEvent,render,screen} from '@testing-library/react';
import {describe,it,expect} from 'vitest';
import {GoldDashboard} from './GoldDashboard';
import type {GoldObservationPayload} from '@/lib/types/research';
const payload={monitor_status:'CONNECTED',monitor:{observed_at_ms:100000,event_count:8,unique_trades:2,gap_count:1,recovery_status:'WINDOW_CHECKED',positions:[],recent_trades:[
 {time_ms:1000,side:'BUY',price:'4300',quantity:'0.1',commission:'0',commission_asset:'USDT'},
 {time_ms:2000,side:'SELL',price:'4310',quantity:'0.2',commission:'0',commission_asset:'USDT'},
]}} as unknown as GoldObservationPayload;
describe('gold graphical account dashboard',()=>{
 it('plots observed fills and filters the timeline without inventing market candles',()=>{
  render(<GoldDashboard data={payload}/>);
  expect(screen.getByRole('img',{name:/成交价格分布，2个样本/})).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'仅买入'}));
  expect(screen.getByRole('img',{name:/成交价格分布，1个样本/})).toBeInTheDocument();
  expect(screen.getByText('未提供持仓观察值')).toBeInTheDocument();
  expect(screen.queryByText('净盈亏 0')).not.toBeInTheDocument();
 });
 it('does not draw missing data as zero or claim connectivity',()=>{
  render(<GoldDashboard data={null}/>);
  expect(screen.getByText('等待账户数据')).toBeInTheDocument();
  expect(screen.queryByRole('img',{name:/成交价格分布/})).not.toBeInTheDocument();
 });
});

it('keeps imported history separate from live trades when changing sources',()=>{
 const historical={...payload,history_status:'QUERIED',history:{status:'QUERIED',start_ms:0,end_ms:9000,
  completed_tasks:52,total_tasks:52,trade_count:25,income_count:30,display_limit:500,
  trades:[{time_ms:3000,side:'BUY',price:'4200',quantity:'1',commission:'0',commission_asset:'USDT'}],
  daily:[{time_ms:0,buy_count:25,sell_count:0}]}} as unknown as GoldObservationPayload;
 render(<GoldDashboard data={historical}/>);
 expect(screen.getByText('历史去重成交')).toBeInTheDocument();
 expect(screen.getByRole('img',{name:/历史每日成交笔数/})).toBeInTheDocument();
 expect(screen.getByRole('img',{name:/成交价格分布，1个样本/})).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'实时监控'}));
 expect(screen.getByRole('img',{name:/成交价格分布，2个样本/})).toBeInTheDocument();
 expect(screen.queryByRole('img',{name:/历史每日成交笔数/})).not.toBeInTheDocument();
});
