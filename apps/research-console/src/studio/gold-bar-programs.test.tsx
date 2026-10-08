import {it,expect} from 'vitest';
import {render,screen} from '@testing-library/react';
import {GoldBarPrograms} from './GoldBarPrograms';
import type {GoldBarProgramCatalog} from '@/lib/types/ai-factory-lifecycle';

const catalog:GoldBarProgramCatalog={status:'AVAILABLE',errors:[],truncated:false,items:[{
 program_id:1,planned_rounds:2,completed_rounds:0,window_bars:3,recorded_stage:'COLLECTING',
 recorded_at_ms:1000,terminal_status:null,error_type:null,liveness:'UNKNOWN',economic_eligible:false,submission_enabled:false,
}]};
it('shows recorded pre-experiment progress without claiming process liveness',()=>{
 render(<GoldBarPrograms catalog={catalog}/>);
 expect(screen.getByText('采集窗口数据')).toBeInTheDocument();
 expect(screen.getByText(/进程状态未确认/)).toBeInTheDocument();
 expect(screen.getByRole('progressbar')).toHaveAttribute('value','0');
});
it('terminal failure overrides an old collecting event',()=>{
 render(<GoldBarPrograms catalog={{...catalog,items:[{...catalog.items[0],terminal_status:'RESEARCH_PROGRAM_INCOMPLETE',error_type:'ValueError'}]}}/>);
 expect(screen.getByText('计划未完成')).toBeInTheDocument();
 expect(screen.queryByText('采集窗口数据')).not.toBeInTheDocument();
 expect(screen.getByText('失败类型：ValueError')).toBeInTheDocument();
});
