import {it,expect,vi} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {ProfitabilityAttribution} from './ProfitabilityAttribution';
import {ProfitabilityStudy} from './ProfitabilityStudy';
import type {ProfitabilityAttribution as Attribution} from '@/lib/types/ai-factory';

it('shows reconciled loss and unknown real fees without calling the replay profitable',()=>{
 const data={status:'RECONCILED_TECHNICAL_ONLY',reason:null,economic_eligible:false,
 diagnosis:'NO_POSITIVE_GROSS_EDGE',net_pnl:'-12.226896',gross_reference_pnl:'-11.5632',
 embedded_slippage:'0.3654',fees:'0.291998',funding:'-0.006298',explicit_spread:'0',explicit_impact:'0',
 turnover_notional:'729.99',turnover_initial_equity:'0.48666',position_contribution:{long:'-11.5632',short:'0'},
 actual_venue_fees:null,spread_component:null,reconciliation_residual:'0'} satisfies Attribution;
 render(<ProfitabilityAttribution data={data}/>);
 expect(screen.getByTitle('-12.226896')).toHaveTextContent('-12.2269');
 expect(screen.getByText(/真实场所费用：未知/)).toBeInTheDocument();
 expect(screen.getByText(/本段毛收益未显示正优势/)).toBeInTheDocument();
});

it('does not allow overwriting a failed registered study',()=>{
 const action=vi.fn();
 render(<ProfitabilityStudy complete busy={false} action={action} data={{p0:null,p1:null,failures:[{stage:'p0',reason:'INPUT_GAP'}],authoritative:false}}/>);
 fireEvent.click(screen.getByRole('button',{name:'检查后续输入'}));
 expect(action).not.toHaveBeenCalled();
 expect(screen.getByRole('status')).toHaveTextContent('INPUT_GAP');
});

it('separates synchronized prices from independent basis experiment results',async()=>{
 const {ProfitabilityReadiness}=await import('./ProfitabilityReadiness');
 render(<ProfitabilityReadiness study={{p0:null,p1:null,p3_study:{status:'COMPLETED_TECHNICAL_ONLY',trials_attempted:4,selected_id:1,decision:'STOP_COST_UNSTABLE_INCREMENT',cases:[{id:0,fee_bps:4,slippage_bps:5,metrics:{net_pnl:-2},state_controls:{matched_rows:0,within_state_covariance:null}}]},failures:[],authoritative:false}}/>);
 expect(screen.getByText('P3 基差增量')).toBeInTheDocument();
 expect(screen.getByText('价格 / 主动成交量基线')).toBeInTheDocument();
 expect(screen.getByText(/4 次尝试 · STOP_COST_UNSTABLE_INCREMENT/)).toBeInTheDocument();
});

it('submits a new research input revision and retains content on errors',async()=>{
 const {ProfitabilityInputUpload}=await import('./ProfitabilityInputUpload');
 const {waitFor}=await import('@testing-library/react');
 const submit=vi.fn().mockRejectedValue(new Error('输入未通过时间检查'));
 render(<ProfitabilityInputUpload complete busy={false} onImport={submit}/>);
 fireEvent.change(screen.getByLabelText('研究 JSON 内容'),{target:{value:'{"quote":"USDC","observations":[]}'}});
 fireEvent.click(screen.getByRole('button',{name:'登记输入并运行'}));
 await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('输入未通过时间检查'));
 expect(submit).toHaveBeenCalledWith('basis','{"quote":"USDC","observations":[]}');
 expect(screen.getByLabelText('研究 JSON 内容')).toHaveValue('{"quote":"USDC","observations":[]}');
});

it('does not show zero venue profit for a pending forward account',async()=>{
 const {ForwardCostEvidence}=await import('./ForwardCostEvidence');
 render(<ForwardCostEvidence data={{status:'WAITING_MATURITY',economic_eligible:false,actual_venue_costs:null,missing_slots:[]}}/>);
 expect(screen.getByRole('status')).toHaveTextContent('等待样本成熟');
 expect(screen.queryByRole('region',{name:'前向账户成本对照'})).not.toBeInTheDocument();
 expect(screen.getByText(/未下单/)).toBeInTheDocument();
});

it('keeps prospective feature counts separate from profitability and exposes missing hours',async()=>{
 const {CausalInformationEvidence}=await import('./CausalInformationEvidence');
 const action=vi.fn();
 render(<CausalInformationEvidence busy={false} complete action={action} data={{status:'COLLECTING',observed_at_ms:Date.now(),economic_eligible:false,quotes:[{quote:'USDC',status:'COLLECTING',ready_rows:2,required_rows:504,missing_slots:[]},{quote:'USDT',status:'COLLECTING',ready_rows:0,required_rows:504,missing_slots:[3600000]}],study:null}}/>);
 expect(screen.getByLabelText('USDC 采集进度')).toHaveAttribute('value','2');
 expect(screen.getByText('1 个缺口')).toBeInTheDocument();
 expect(screen.getByText(/不算提前发出的预测/)).toBeInTheDocument();
 expect(screen.getByText('尚未建立')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'更新信息窗口'}));
 expect(action).toHaveBeenCalledOnce();
});

it('shows collection retries and actionable source errors',async()=>{
 const {CausalInformationEvidence}=await import('./CausalInformationEvidence');
 render(<CausalInformationEvidence busy={false} complete action={vi.fn()} data={{status:'COLLECTING',observed_at_ms:Date.now(),economic_eligible:false,collection:{slot_ms:3600000,status:'PARTIAL',request_attempts:14,recovered_requests:1,elapsed_ms:1200,failures:[{quote:'USDT',kind:'oi',category:'HTTP_ERROR',http_status:429}]}}}/>);
 expect(screen.getByRole('region',{name:'最近采集诊断'})).toHaveTextContent('14');
 expect(screen.getByRole('region',{name:'最近采集诊断'})).toHaveTextContent('429');
 expect(screen.getByText(/交易所限流/)).toBeInTheDocument();
});
