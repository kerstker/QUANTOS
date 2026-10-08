import {it,expect} from 'vitest';
import {render,screen} from '@testing-library/react';
import {NativeOrderReceiptsEvidence} from './NativeOrderReceiptsEvidence';
import type {NativeOrderReceiptEvidence} from '@/lib/types/ai-factory';

const data:NativeOrderReceiptEvidence={schema:'BINANCE_USDM_ORDER_RECEIPTS_V1',input_hash:'a'.repeat(64),
 account_ref_sha256:'b'.repeat(64),symbol:'BTCUSDC',quote:'USDC',provenance:'SYNTHETIC_TEST_FIXTURE',
 source_verified:false,actual_paid_fees:null,messages_count:4,unique_messages:3,duplicate_messages:1,trades_count:2,
 missing_commission_trades:0,all_commissions_in_quote:true,reported_commission_by_asset:{USDC:'0.0198'},
 reported_quote_commission:'0.0198',reported_filled_quantity:'1',unobserved_order_ids:[],coverage:{start_ms:100,end_ms:300,gaps:[]},
 trade_rows_total:2,artifact:'native-observations.json',projection:'LAST_50_TRADES_FULL_MESSAGES_IN_IMMUTABLE_INPUT',
 economic_eligible:false,limitations:[],fee_comparisons:[{profile_ms:25,simulated_fee:'0.099',reported_minus_simulated_fee:'-0.0792',
 comparison_available:true,comparison_scope:'DECLARED_WHOLE_WINDOW_INCLUDES_QUANTITY_DIFFERENCES',source_verified:false,actual_paid_fees:null}],
 trade_rows:[{order_id:'fixed-buy',venue_order_id:'123',trade_id:'8',event_ms:152,transaction_ms:151,venue_trade_ms:151,
 received_at_ms:171,quantity:'0.6',price:'99',liquidity_role:'MAKER',reported_commission:'0.01188',commission_asset:'USDC',response_sha256:'c'.repeat(64)}]};

it('shows imported fee amounts separately from the simulated account and all four clocks',()=>{
 render(<NativeOrderReceiptsEvidence data={data}/>);
 expect(screen.getByRole('region',{name:'币安原始订单回执'})).toHaveTextContent('4 条消息');
 expect(screen.getByText(/1 条重复/)).toBeInTheDocument();
 expect(screen.getByRole('region',{name:'报告费用与模拟费用比较'})).toHaveTextContent('-0.0792');
 expect(screen.getByText(/真实已付费用仍未知/)).toBeInTheDocument();
 const table=screen.getByRole('region',{name:'原始成交时钟与费用'});
 for(const text of ['152','151','171','0.01188','maker'])expect(table).toHaveTextContent(text);
 expect(screen.getByText(/费用差包含成交数量差异/)).toBeInTheDocument();
});

it('keeps missing and other-asset commissions unknown without creating a quote-currency zero',()=>{
 render(<NativeOrderReceiptsEvidence data={{...data,missing_commission_trades:1,all_commissions_in_quote:false,
  reported_commission_by_asset:{BNB:'0.000001'},reported_quote_commission:null,
  fee_comparisons:[{...data.fee_comparisons[0],comparison_available:false,reported_minus_simulated_fee:null}]}}/>);
 expect(screen.getByRole('region',{name:'回执报告费用'})).toHaveTextContent('BNB');
 expect(screen.getByRole('region',{name:'报告费用与模拟费用比较'})).toHaveTextContent('未知');
 expect(screen.queryByText('-0.0792')).not.toBeInTheDocument();
 expect(screen.getByText(/1 笔成交缺少完整手续费/)).toBeInTheDocument();
});
