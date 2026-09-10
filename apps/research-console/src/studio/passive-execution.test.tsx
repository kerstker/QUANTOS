import {it,expect,vi} from 'vitest';
import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {PassiveExecutionEvidence} from './PassiveExecutionEvidence';
import {ProfitabilityInputUpload} from './ProfitabilityInputUpload';
import type {PassiveExecutionStudy} from '@/lib/types/ai-factory';

const data:PassiveExecutionStudy={status:'QUEUE_DIAGNOSTICS_COMPLETED',symbol:'BTCUSDC',quote:'USDC',
 start_ms:1000,end_ms:2000,trials_attempted:3,selected_profile:null,ledger_applied:false,
 quality:{counts:{depth_updates:123,aggregate_trades:40,mark_updates:15,duplicates:0},failures:[],
 book_samples:[{available_at_ms:1050,transaction_time_ms:1000,bids:[['99','2']],asks:[['101','3']]}]},
 trials:[{profile_ms:0,status:'COMPLETED_DIAGNOSTIC',fill_count:1,censored_orders:1,event_count:2,
 orders:[{order_id:'fixed-a',side:'BUY',price:'99',quantity:'0.02',filled_quantity:'0.01',remaining_quantity:'0.01',
 status:'PARTIALLY_FILLED',censored:true,queue_ahead_at_arrival:'3',reason:null}],
 events:[{order_id:'fixed-a',status:'ACK',venue_time_ms:1050,client_receive_ms:1075,client_time_ms:null,quantity:null,price:null,reason:null}]},
 {profile_ms:100,status:'FAILED',reason:'ValueError'}]};

it('shows queue assumptions, separate clocks and right censoring without a profit result',()=>{
 render(<PassiveExecutionEvidence data={data}/>);
 expect(screen.getByText(/尚未接入成本账本/)).toBeInTheDocument();
 expect(screen.getByRole('region',{name:'被动订单结果'})).toHaveTextContent('窗口截尾');
 expect(screen.getByRole('region',{name:'被动订单结果'})).toHaveTextContent('0.01');
 fireEvent.click(screen.getByText('逐单时间轨迹'));
 expect(screen.getByRole('region',{name:'订单时钟轨迹'})).toHaveTextContent('1050');
 expect(screen.getByRole('region',{name:'订单时钟轨迹'})).toHaveTextContent('1075');
 fireEvent.change(screen.getByLabelText('队列延迟情景'),{target:{value:'100'}});
 expect(screen.getByRole('status')).toHaveTextContent('ValueError');
 expect(screen.queryByRole('region',{name:'被动订单结果'})).not.toBeInTheDocument();
});

it('shows absent tape as missing, not zero fill success',()=>{
 render(<PassiveExecutionEvidence/>);
 expect(screen.getByText(/尚未导入原始逐笔数据/)).toBeInTheDocument();
 expect(screen.queryByText('0 次成交')).not.toBeInTheDocument();
});

it('shows exact canonical reconciliation and changes account evidence with the delay profile',()=>{
 const account={input_hash:'a'.repeat(64),result_hash:'b'.repeat(64),ledger_applied:true as const,
  reconciliation:{initial_equity:'1000',ending_equity:'1000.0901',price_pnl:'0.1',fee:'0.0099',
   spread:'0',impact:'0',funding:'0',liquidation_fee:'0',insurance_adjustment:'0',adl_adjustment:'0',attributed_change:'0.0901',residual:'0'},
  actual_venue_fees:null,simulated_fee_rate:'0.001',history_count:2,
  equity_history:[{timestamp:1000,equity:'1000',drawdown:'0'},{timestamp:1500,equity:'1000.0901',drawdown:'0'}],
  ending_state:{positions:{BTCUSDC:{quantity:'0.1'}}}};
 render(<PassiveExecutionEvidence data={{...data,ledger_applied:true,
  account_environment:{symbol:'BTCUSDC',quote:'USDC',venue_margin_tiers_verified:false,actual_venue_fees:null,limitations:[]},
  trials:[{...data.trials[0],account},data.trials[1]]}}/>);
 expect(screen.getByRole('region',{name:'唯一账本成本归因'})).toHaveTextContent('1000.0901');
 expect(screen.getByRole('region',{name:'唯一账本成本归因'})).toHaveTextContent('0.0099');
 expect(screen.getByText(/真实费用未知/)).toBeInTheDocument();
 expect(screen.getByText(/期末未平仓 BTCUSDC/)).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('队列延迟情景'),{target:{value:'100'}});
 expect(screen.queryByRole('region',{name:'唯一账本成本归因'})).not.toBeInTheDocument();
});

it('can register a passive input using the same immutable version flow',async()=>{
 const submit=vi.fn().mockResolvedValue(undefined);
 render(<ProfitabilityInputUpload complete busy={false} onImport={submit}/>);
 fireEvent.change(screen.getByLabelText('输入类型'),{target:{value:'passive'}});
 fireEvent.change(screen.getByLabelText('研究 JSON 内容'),{target:{value:'{"tape":{},"actions":[]}'}});
 fireEvent.click(screen.getByRole('button',{name:'登记输入并运行'}));
 await waitFor(()=>expect(submit).toHaveBeenCalledWith('passive','{"tape":{},"actions":[]}'));
});

it('opens the frozen profile by default and resets inspection when a new input arrives',()=>{
 const first={...data,input_sha256:'first',selected_profile:100};
 const {rerender}=render(<PassiveExecutionEvidence data={first}/>);
 expect(screen.getByLabelText('队列延迟情景')).toHaveValue('100');
 fireEvent.change(screen.getByLabelText('队列延迟情景'),{target:{value:'0'}});
 expect(screen.getByLabelText('队列延迟情景')).toHaveValue('0');
 rerender(<PassiveExecutionEvidence data={{...first,input_sha256:'second'}}/>);
 expect(screen.getByLabelText('队列延迟情景')).toHaveValue('100');
});

it('shows declared dynamic bounds and keeps actual venue validation unknown',()=>{
 const environment={symbol:'BTCUSDC',quote:'USDC',venue_margin_tiers_verified:false as const,
  actual_venue_fees:null,limitations:[],venue_order_constraints:{scope:'ISOLATED_DECLARED_ORDERS_ONLY' as const,
   percent_price_up:'1.1',percent_price_down:'0.9',max_open_orders:3,market_take_bound:'0.05',
   limit_band_reference:'CURRENT_MARK_AT_ADMISSION' as const,market_notional_reference:'CURRENT_MARK_AT_ADMISSION' as const,
   market_protection:'CURRENT_MARK_AT_EACH_FILL_REJECT_REMAINDER_OUTSIDE_BOUND' as const,venue_behavior_verified:false as const}};
 const {rerender}=render(<PassiveExecutionEvidence data={{...data,account_environment:environment}}/>);
 expect(screen.getByRole('region',{name:'模拟场所订单边界'})).toHaveTextContent('1.1');
 expect(screen.getByRole('region',{name:'模拟场所订单边界'})).toHaveTextContent('最多 3 个未完成订单');
 expect(screen.getByRole('region',{name:'模拟场所订单边界'})).toHaveTextContent('真实场所行为仍未验证');
 rerender(<PassiveExecutionEvidence data={{...data,input_sha256:'legacy',account_environment:{...environment,venue_order_constraints:null}}}/>);
 expect(screen.queryByRole('region',{name:'模拟场所订单边界'})).not.toBeInTheDocument();
 expect(screen.getByText(/动态订单边界尚未建模/)).toBeInTheDocument();
});

it('shows distinct quantity filters, disabled price fields and honest legacy absence',()=>{
 const environment:NonNullable<PassiveExecutionStudy['account_environment']>={symbol:'BTCUSDC',quote:'USDC',
  venue_margin_tiers_verified:false,actual_venue_fees:null,limitations:[],venue_order_filters:{
   schema:'BINANCE_USDM_DECLARED_FILTERS_V1',price:{minimum:'0.05',maximum:'0',step:'0.1'},
   lot_size:{minimum:'0.003',maximum:'0.010',step:'0.002'},
   market_lot_size:{minimum:'0.002',maximum:'0.008',step:'0.003'},quantity_atom:'0.001',
   grid_origin:'FILTER_MINIMUM',market_application:'LOT_SIZE_AND_MARKET_LOT_SIZE',
   zero_price_fields:'DISABLE_INDIVIDUAL_CONSTRAINT',partial_fills:'ACCOUNTING_ATOM_NOT_PARENT_ORDER_GRID',
   venue_behavior_verified:false}};
 const {rerender}=render(<PassiveExecutionEvidence data={{...data,account_environment:environment}}/>);
 const region=screen.getByRole('region',{name:'冻结价格与数量规则'});
 expect(region).toHaveTextContent('最高价格：未限制');
 expect(region).toHaveTextContent('0.05');
 expect(screen.getByRole('row',{name:/普通订单数量/})).toHaveTextContent('0.010');
 expect(screen.getByRole('row',{name:/市价附加数量/})).toHaveTextContent('0.008');
 expect(region).toHaveTextContent('市价单同时检查两行数量规则');
 expect(region).toHaveTextContent('真实场所仍未验证');
 rerender(<PassiveExecutionEvidence data={{...data,input_sha256:'legacy',account_environment:{...environment,venue_order_filters:null}}}/>);
 expect(screen.queryByRole('region',{name:'冻结价格与数量规则'})).not.toBeInTheDocument();
 expect(screen.getByText(/完整价格与数量规则尚未绑定/)).toBeInTheDocument();
});

it('shows liquidity fee assumptions without calling the zero fixed fallback a real fee',()=>{
 const account:NonNullable<PassiveExecutionStudy['trials'][number]['account']>={
  input_hash:'a'.repeat(64),result_hash:'b'.repeat(64),ledger_applied:true,actual_venue_fees:null,
  simulated_fee_rate:null,history_count:0,equity_history:[],ending_state:{positions:{}},
  reconciliation:{initial_equity:'1000',ending_equity:'1000',price_pnl:'0',fee:'0',spread:'0',impact:'0',
   funding:'0',liquidation_fee:'0',insurance_adjustment:'0',adl_adjustment:'0',attributed_change:'0',residual:'0'},
  fee_model:{scope:'EXPLICIT_SIMULATION_ASSUMPTION',actual_venue_fees:null,mode:'BY_FILL_LIQUIDITY_ROLE',
   maker_fee_rate:'0',taker_fee_rate:'0.001',reservation_fee_rate:'0.001',role_source:'EXECUTION_MECHANISM_NOT_ORDER_TYPE'}};
 render(<PassiveExecutionEvidence data={{...data,trials:[{...data.trials[0],account}]}}/>);
 const fees=screen.getByRole('region',{name:'冻结成交费率'});
 expect(fees).toHaveTextContent('被动成交（maker）');
 expect(fees).toHaveTextContent('主动成交（taker）');
 expect(fees).toHaveTextContent('0.001');
 expect(fees).toHaveTextContent('预留费率');
 expect(screen.getByText(/真实费用未知/)).toBeInTheDocument();
 expect(screen.queryByText(/模拟费率 0，/)).not.toBeInTheDocument();
});
