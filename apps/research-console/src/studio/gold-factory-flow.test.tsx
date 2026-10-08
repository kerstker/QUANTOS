import {it,expect} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {GoldFactoryFlow} from './GoldFactoryFlow';
import type {FactoryLifecyclePayload} from '@/lib/types/ai-factory-lifecycle';
const data={campaigns:[{campaign_id:1,state:'COMPLETED',config:{market:'XAUUSDT',input_id:2,input_sha256:'input',objective:'固定假设',max_cycles:1}}],rounds:[{campaign_id:1,ordinal:0,attempt:7,state:'COMPLETED'}],gold_inputs:{items:[{input_id:2,input_sha256:'input'}]},gold_results:[{attempt:7,state:'COMPLETED',report_status:'AVAILABLE',outcome:'NO_SIGNAL',latest_review:{status:'VERIFIED_SIGNATURE_ENGINEERING_ONLY',current:true},execution_review:{status:'AVAILABLE',records:[{schema:'DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1',next_attempts:[{attempt:8,state:'COMPLETED'}],source_verified:false}]}}]} as unknown as FactoryLifecyclePayload;
it('shows technical loop evidence without promoting signatures or feedback into live completion',()=>{
 const inspect:number[]=[];
 render(<GoldFactoryFlow data={data} inspect={attempt=>inspect.push(attempt)}/>);
 expect(screen.getByText('签名已核对，尚无经济准入')).toBeInTheDocument();
 expect(screen.getByText('有技术复盘，真实成交待核实')).toBeInTheDocument();
 expect(screen.getByText('后续研究 #8 已完成')).toBeInTheDocument();
 expect(screen.getByText('本轮无交易信号')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'查看本轮报告与反馈'}));
 expect(inspect).toEqual([7]);
});
it('does not borrow a different campaign result or infer completion from a reservation',()=>{
 const other={...data,campaigns:[...data.campaigns,{...data.campaigns[0],campaign_id:2,state:'RUNNING'}],rounds:[...data.rounds,{campaign_id:2,ordinal:0,attempt:9,state:'RESERVED',created_ms:0,finished_ms:null}]};
 render(<GoldFactoryFlow data={other} inspect={()=>{}}/>);
 fireEvent.change(screen.getByLabelText('查看黄金计划'),{target:{value:'2'}});
 expect(screen.getByText('本轮处理中')).toBeInTheDocument();
 expect(screen.getByText('尚无本轮成本报告')).toBeInTheDocument();
 expect(screen.queryByText('后续研究 #8 已完成')).not.toBeInTheDocument();
});
