import {afterEach,expect,it} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {DemoMarketInputs} from './DemoMarketInputs';
import {marketInputsValid} from '@/lib/api/demo-market-inputs-contract';
import type {DemoMarketInputsReport,DemoMarketInputsSnapshots} from '@/lib/types/demo-market-inputs';
afterEach(cleanup);
const now=1800000010010,lastClose=1800000000000-1;
function report():DemoMarketInputsReport {
  return {schema:'DEMO_MARKET_INPUTS_VIEW_V1',environment:'BINANCE_USDM_DEMO',symbol:'BTCUSDC',capture_status:'CAPTURED',
    observed_at_ms:now-10,completed_at_ms:now-3,source_sha256:'a'.repeat(64),input_sha256:'b'.repeat(64),
    failure:null,source_verified:false,submission_enabled:false,economic_authority:false,
    metrics:{daily_volume_base:'123.456',instrument_vol:'0.0001',volatility_policy:'SQRT_SUM_LOG_RETURNS_SQUARED_60X1M_CEIL18_V1',
      last_close_ms:lastClose,ticker_close_ms:now-10,return_intervals:60,
      bars:Array.from({length:61},(_,i)=>({close_ms:lastClose-(60-i)*60000,open:String(60000+i),high:String(60000+i),low:String(60000+i),close:String(60000+i),volume_base:'2'}))}};
}
function snapshots():DemoMarketInputsSnapshots {return {BTCUSDC:{status:'AVAILABLE',report:report()},BTCUSDT:{status:'NO_SNAPSHOT',report:null}};}
it('preserves quote separation, inspectable closed candles and historical data',()=>{
  const data=snapshots();expect(marketInputsValid(data,now)).toBe(true);
  const view=render(<DemoMarketInputs snapshots={data}/>);
  expect(screen.getByText('123.456 BTC')).toBeInTheDocument();
  expect(screen.getByRole('img',{name:/BTCUSDC 已收盘分钟 K 线，61个样本/})).toBeInTheDocument();
  expect(screen.getByRole('slider',{name:'BTCUSDC 已收盘分钟 K 线检查样本'})).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'BTCUSDT'}));
  expect(screen.getByText('尚无 BTCUSDT 行情输入')).toBeInTheDocument();
  expect(screen.queryByText('123.456 BTC')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'BTCUSDC'}));
  view.rerender(<DemoMarketInputs snapshots={{...data,BTCUSDC:{status:'STALE',report:report()}}}/>);
  expect(screen.getByRole('status')).toHaveTextContent('历史');
  expect(screen.getByText('123.456 BTC')).toBeInTheDocument();
});
it('keeps a failed capture visible without plotting invented values',()=>{
  const raw={...report(),capture_status:'FAILED' as const,metrics:null,failure:{kind:'worker',reason:'CAPTURE_BUDGET_EXHAUSTED' as const}};
  const data:DemoMarketInputsSnapshots={BTCUSDC:{status:'AVAILABLE',report:raw},BTCUSDT:{status:'NO_SNAPSHOT',report:null}};
  expect(marketInputsValid(data,now)).toBe(true);
  render(<DemoMarketInputs snapshots={data}/>);
  expect(screen.getByText(/采集超时/)).toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});
it('rejects future, stale-labelled-fresh, wrong-quote and unclosed or malformed candles',()=>{
  const base=snapshots();
  for(const patch of [{symbol:'BTCUSDT'},{completed_at_ms:now+1},{submission_enabled:true},
    {metrics:{...report().metrics,bars:report().metrics!.bars.slice(1)}},
    {metrics:{...report().metrics,bars:report().metrics!.bars.map((b,i)=>i===60?{...b,close_ms:now+60000}:b)}},
    {metrics:{...report().metrics,instrument_vol:'-1'}},
    {metrics:{...report().metrics,bars:report().metrics!.bars.map((b,i)=>i===30?{...b,high:'1'}:b)}}]) {
    expect(marketInputsValid({...base,BTCUSDC:{status:'AVAILABLE',report:{...report(),...patch}}},now)).toBe(false);
  }
  expect(marketInputsValid(base,now+30000)).toBe(false);
});
