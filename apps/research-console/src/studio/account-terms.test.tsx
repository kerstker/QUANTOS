import {it,expect} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {FrozenAccountEvidence} from './FrozenAccountEvidence';

const terms={schema:'BINANCE_USDM_ACCOUNT_TERMS_V1' as const,input_hash:'a'.repeat(64),account_ref_sha256:'b'.repeat(64),
 provenance:'SYNTHETIC_TEST_FIXTURE' as const,source_verified:false as const,actual_paid_fees:null,
 symbol:'BTCUSDC',quote:'USDC',selected_leverage:3,symbol_config_notional_cap:'180',new_risk_notional_cap:'100',
 cap_comparison:'STRICTLY_BELOW' as const,initial_margin_rounding:'LEVERAGE_RECIPROCAL_CEILING_1E24_V1' as const,
 overflow_policy:'LAST_BRACKET_RISK_ESTIMATE_V1' as const,maker_fee_rate:'0.0002',taker_fee_rate:'0.001',
 rpi_fee_rate_not_applied:'0.00005',limitations:[],observations:{before:{commission:{requested_at_ms:1000,received_at_ms:1010,response_sha256:'c'.repeat(64)}},after:{}},
 margin_brackets:[{notional_floor:'0',notional_cap:'100',initial_margin_rate:'0.333333333333333333333334',maintenance_margin_rate:'0.01',maintenance_amount:'0',maximum_initial_leverage:10}]};

it('separates selected leverage, simulated capacity, source and actual fees',()=>{
 render(<FrozenAccountEvidence terms={terms}/>);
 const panel=screen.getByRole('region',{name:'冻结账户条件'});
 expect(panel).toHaveTextContent('合成验证材料');
 expect(panel).toHaveTextContent('已选杠杆');
 expect(panel).toHaveTextContent('3 倍');
 expect(panel).toHaveTextContent('新增风险名义价值 < 100 USDC');
 expect(panel).toHaveTextContent('账户配置上限 180 USDC');
 expect(panel).toHaveTextContent('最大初始杠杆 10 倍');
 expect(panel).toHaveTextContent('真实已付费用未知');
 expect(panel).toHaveTextContent('0.333333333333333333333334');
 fireEvent.click(screen.getByText('查看来源与接收时间'));
 expect(panel).toHaveTextContent('c'.repeat(64));
});

it('does not authenticate an imported response or claim an unsupported mode',()=>{
 render(<FrozenAccountEvidence terms={{...terms,provenance:'IMPORTED_UNVERIFIED'}}/>);
 expect(screen.getByRole('region',{name:'冻结账户条件'})).toHaveTextContent('导入材料，来源未认证');
 expect(screen.getByText(/超过表尾后的保证金仅作风险估计/)).toBeInTheDocument();
});
