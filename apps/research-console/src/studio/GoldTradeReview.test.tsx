import {it,expect} from 'vitest';
import {render,screen} from '@testing-library/react';
import {GoldTradeReview} from './GoldTradeReview';
it('keeps unmatched cash distinct from mismatched cash and net profit',()=>{
 render(<GoldTradeReview review={{total_trades:1,mismatched_trades:1,unmatched_cash_count:2,net_pnl:null,order_lifecycle_complete:false,
 trades:[{time_ms:1000,side:'BUY',quantity:'1',commission:'0.2',commission_asset:'USDT',reported_realized_pnl:'3',order_check:'NOT_OBSERVED',commission_check:'MISMATCH',realized_pnl_check:'MATCHING_REPORTED_CASH'}],orders:[],conditional_orders:[]}}/>);
 expect(screen.getByText('存在差异')).toBeInTheDocument();
 expect(screen.getByText('未查到关联记录')).toBeInTheDocument();
 expect(screen.getByText(/暂不计算账户净盈亏/)).toBeInTheDocument();
});
