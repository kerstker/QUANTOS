import {render, screen} from '@testing-library/react';
import {describe, expect, it} from 'vitest';
import {DemoStatement} from './DemoStatement';
import {statementValid} from '@/lib/api/demo-statement-contract';
import type {DemoStatementSnapshot} from '@/lib/types/demo-statement';

const snapshot = (): DemoStatementSnapshot => ({status:'AVAILABLE',report:{
  schema:'BINANCE_DEMO_STATEMENT_V1',status:'PARTIAL',observed_at_ms:1000,completed_at_ms:1100,start_ms:1,end_ms:999,
  symbols:['BTCUSDT'],read_count:6,trade_count:1,income_count:1,failed_attempt_count:0,history_complete:false,unqueried_income_symbols:[],income_breakdown:[],
  venue_cash_reconciliation:'UNKNOWN',strategy_pnl:null,submission_enabled:false,
  assets:[{asset:'USDT',reported_commission:'0.1',reported_realized_pnl:'-2',income_total:'997.9',income_commission:'-0.1',fee_crosscheck_residual:'0'}],
  gaps:[{kind:'trades',symbol:'BTCUSDT',start_ms:1,end_ms:999,reason:'PAGE_LIMIT_REACHED'}],
  terms:[{symbol:'BTCUSDT',exchange_rules:true,margin_brackets:true,commission:{symbol:'BTCUSDT',makerCommissionRate:'0.0002',takerCommissionRate:'0.0004'}}],
}});
describe('Demo venue statement',()=>{
  it('shows transfer-inclusive cash separately from profit and retains truncated history',()=>{
    render(<DemoStatement snapshot={snapshot()} refreshFailed={false}/>);
    expect(screen.getByText('997.9')).toBeInTheDocument();
    expect(screen.getByText(/流水净额包含转入转出/)).toBeInTheDocument();
    expect(screen.getByText(/历史可能截断/)).toBeInTheDocument();
    expect(screen.getByText(/账户现金对账及策略收益仍待验证/)).toBeInTheDocument();
  });
  it('rejects false reconciliation, rewritten time and cross-symbol fee claims',()=>{
    const s=snapshot(); expect(statementValid(s,1100)).toBe(true);
    for(const patch of [{history_complete:true},{venue_cash_reconciliation:'PASS'},{strategy_pnl:'1'},
      {completed_at_ms:1200},{income_count:-1},{assets:[{asset:'USDT',income_total:3}]}]){
      expect(statementValid({...s,report:{...s.report,...patch}},1100)).toBe(false);
    }
    expect(statementValid({...s,status:'STALE'},1100)).toBe(false);
  });
});
