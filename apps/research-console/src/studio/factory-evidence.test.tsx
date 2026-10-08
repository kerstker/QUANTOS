import {it,expect,vi} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {FactoryEvidence} from './FactoryEvidence';
import type {FactoryPayload} from '@/lib/types/ai-factory';
it('explains checkpoint rejection and clears the alert after successful verification',()=>{
 const data={trials:[],resident:{status:'DEGRADED',observed_at_ms:Date.now(),checkpoint:{status:'RETRY_REQUIRED'}}} as unknown as FactoryPayload;
 const view=render(<FactoryEvidence data={data} busy={false} action={vi.fn()}/>);
 expect(screen.getByRole('alert')).toHaveTextContent('检查点校验或发布失败');
 expect(screen.getByText('运行降级')).toBeInTheDocument();
 view.rerender(<FactoryEvidence data={{...data,resident:{...data.resident!,status:'RUNNING',checkpoint:{status:'VERIFIED_DAILY_CHECKPOINT'}}}} busy={false} action={vi.fn()}/>);
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
 expect(screen.getByText(/当日检查点已复核/)).toBeInTheDocument();
});
it('keeps missing profitability evidence unknown instead of inferring gross profits',()=>{
 render(<FactoryEvidence data={null} busy={false} action={vi.fn()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'成本决策'}));
 expect(screen.getByText('完整成本归因尚不可用')).toBeInTheDocument();
});
it('queues the bounded study only for a completed frozen experiment',()=>{
 const action=vi.fn();const data={selected:{state:'COMPLETED',result:{}},trials:[]} as unknown as FactoryPayload;
 render(<FactoryEvidence data={data} busy={false} action={action}/>);
 fireEvent.click(screen.getByRole('tab',{name:'成本决策'}));
 fireEvent.click(screen.getByRole('button',{name:'运行盈利验证'}));
 expect(action).toHaveBeenCalledWith('profitability');
 expect(screen.getByText('缺少真实场所轨迹')).toBeInTheDocument();
});
it('activates observations only for a completed model and routes each action',()=>{
 const action=vi.fn();const data={selected:{state:'COMPLETED',result:{}},trials:[],forward:null,assessment:null,verification:null,pipeline:null} as unknown as FactoryPayload;
 render(<FactoryEvidence data={data} busy={false} action={action}/>);
 fireEvent.click(screen.getByRole('tab',{name:'前向观察'}));
 fireEvent.click(screen.getByRole('button',{name:'启用观察'}));expect(action).toHaveBeenCalledWith('observe');
 fireEvent.click(screen.getByRole('tab',{name:'压力与组合'}));
 fireEvent.click(screen.getByRole('button',{name:'运行诊断'}));expect(action).toHaveBeenCalledWith('assess');
 fireEvent.click(screen.getByRole('tab',{name:'运行与证据'}));
 fireEvent.click(screen.getByRole('button',{name:'准备 / 检查验证包'}));expect(action).toHaveBeenCalledWith('verify');
});
it('does not fabricate predictions or enable an unfinished model',()=>{
 render(<FactoryEvidence data={null} busy={false} action={vi.fn()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'前向观察'}));
 expect(screen.getByRole('button',{name:'启用观察'})).toBeDisabled();
 expect(screen.queryByRole('img')).not.toBeInTheDocument();
});
it('recovers failed result registration even when pipeline artifacts completed',()=>{
 const action=vi.fn();
 const data={selected:{state:'COMPLETED',result:{}},trials:[],pipeline:{status:'COMPLETED'},workflow:{status:'FAILED',stage:'COMPLETED',runner_status:'error',run_id:'run_failed',reason:'RUNNER_FAILED',can_resume:true}} as unknown as FactoryPayload;
 const view=render(<FactoryEvidence data={data} busy={false} action={action}/>);
 fireEvent.click(screen.getByRole('button',{name:'恢复后续流程'}));
 expect(action).toHaveBeenCalledWith('resume');
 view.rerender(<FactoryEvidence data={{...data,workflow:{...data.workflow,status:'RUNNING',can_resume:false}}} busy={false} action={action}/>);
 expect(screen.getByRole('button',{name:'恢复后续流程'})).toBeDisabled();
});

it('does not label a legacy archive-exists receipt as reverified',()=>{
 const data={trials:[],resident:{status:'RUNNING',observed_at_ms:Date.now(),checkpoint:{status:'DAILY_CHECKPOINT_EXISTS'}}} as unknown as FactoryPayload;
 render(<FactoryEvidence data={data} busy={false} action={vi.fn()}/>);
 expect(screen.getByText(/已有当日检查点/)).toBeInTheDocument();
 expect(screen.queryByText(/已复核/)).not.toBeInTheDocument();
});
