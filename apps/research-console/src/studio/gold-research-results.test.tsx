import {it,expect} from 'vitest';
import {render,screen} from '@testing-library/react';
import {GoldResearchResults} from './GoldResearchResults';
import type {GoldResearchResult} from '@/lib/types/ai-factory-lifecycle';
it('shows retained costs and no-signal limitations without reporting live profit',()=>{
 const row:GoldResearchResult={attempt:7,state:'COMPLETED',candidate:{id:0,sell_threshold:'10',confirm_windows:2},trial_id:'original-trial',report_status:'AVAILABLE',outcome:'NO_SIGNAL',costs:{fee:'0.02',residual:'0'},recorded_verification_status:'INDEPENDENT_AUTHORITY_UNCONFIGURED',execution_review:{status:'NO_RECORD',records:[]} as GoldResearchResult['execution_review'],latest_review:null,economic_eligible:false,authoritative:false};
 render(<GoldResearchResults rows={[row]}/>);
 expect(screen.getByText('0.02')).toBeInTheDocument();
 expect(screen.getByText('INDEPENDENT_AUTHORITY_UNCONFIGURED')).toBeInTheDocument();
 expect(screen.getByText('本轮没有产生交易信号，不能据此认定策略盈利或通过独立验证。')).toBeInTheDocument();
 expect(document.getElementById('gold-result-7')).toBeInTheDocument();
});
it('keeps failed and unavailable reports visible without filling missing costs',()=>{
 const base={candidate:null,trial_id:null,outcome:null,costs:null,recorded_verification_status:null,execution_review:{status:'NO_RECORD',records:[]} as GoldResearchResult['execution_review'],latest_review:null,economic_eligible:false,authoritative:false} as const;
 render(<GoldResearchResults rows={[{...base,attempt:1,state:'FAILED',report_status:'NO_REPORT'},{...base,attempt:2,state:'COMPLETED',report_status:'UNAVAILABLE'}]}/>);
 expect(screen.getByText(/实验失败，未生成可用结果/)).toBeInTheDocument();
 expect(screen.getByText(/原报告不可用/)).toBeInTheDocument();
 expect(screen.queryByText('成本后权益变化')).not.toBeInTheDocument();
});

it('shows a changed receipt as pending recheck without retaining a current verified label',()=>{
 const row:GoldResearchResult={attempt:7,state:'COMPLETED',candidate:null,trial_id:'trial',report_status:'AVAILABLE',outcome:'NO_SIGNAL',costs:null,recorded_verification_status:null,execution_review:{status:'NO_RECORD',records:[]} as GoldResearchResult['execution_review'],latest_review:{status:'PENDING_RECHECK',checked_at_ms:1000,current:false},economic_eligible:false,authoritative:false};
 render(<GoldResearchResults rows={[row]}/>);
 expect(screen.getByText(/收据或登记已变化，等待重新核验/)).toBeInTheDocument();
 expect(screen.queryByText(/签名核对通过/)).not.toBeInTheDocument();
});


it('links execution review to the next research without claiming real fills',()=>{
 const row:GoldResearchResult={attempt:1,state:'COMPLETED',candidate:null,trial_id:'trial',report_status:'AVAILABLE',outcome:'FLAT_TERMINAL',costs:null,recorded_verification_status:null,latest_review:null,economic_eligible:false,authoritative:false,
 execution_review:{status:'AVAILABLE',records:[{feedback_sha256:'a'.repeat(64),schema:'DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1',available_at_ms:1000,status:'EXECUTION_REVIEW_AVAILABLE',source_verified:false,position_flat:true,next_attempts:[{attempt:2,state:'COMPLETED'}]}]}};
 render(<GoldResearchResults rows={[row]}/>);
 expect(screen.getByText('#2').closest('a')).toHaveAttribute('href','#gold-result-2');
 expect(screen.getByText(/来源尚未认证/)).toBeInTheDocument();
 expect(screen.getByText(/整轮开平仓复盘/)).toBeInTheDocument();
});
