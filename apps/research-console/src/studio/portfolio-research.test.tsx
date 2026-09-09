import {it,expect,vi} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {PortfolioResearch} from './PortfolioResearch';
import type {PortfolioResearch as Evidence} from '@/lib/types/ai-factory';

vi.mock('./Charts',()=>({Chart:({rows}:{rows:unknown[]})=><output aria-label="账户曲线">{JSON.stringify(rows)}</output>}));

it('enables research after the primary experiment completes and retains interrupted work',()=>{
 const action=vi.fn();const view=render(<PortfolioResearch busy={false} complete={false} action={action}/>);
 expect(screen.getByRole('button')).toBeDisabled();
 view.rerender(<PortfolioResearch busy={false} complete action={action} data={{authoritative:false,economic_eligible:false,status:'ERROR',reason:'ValueError'}}/>);
 fireEvent.click(screen.getByRole('button',{name:'恢复组合研究'}));expect(action).toHaveBeenCalledOnce();
 expect(screen.getByRole('alert')).toHaveTextContent('已有尝试保留');
});

it('keeps cash and missing costs distinct from evidence of profit',()=>{
 render(<PortfolioResearch busy={false} complete action={vi.fn()} data={{authoritative:false,economic_eligible:false,status:'COMPLETED_TECHNICAL_ONLY',decision:'STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE',selected:{id:0,component_ids:[1],quarters:[0]},inner_trials:[{spec:{id:1,component_ids:[1],quarters:[4]},status:'FAILED',reason:'INTERRUPTED'}]}}/>);
 expect(screen.getByRole('status')).toHaveTextContent('缺少实际交易样本');
 expect(screen.getByText('空仓')).toBeInTheDocument();
 expect(screen.getByRole('region',{name:'组合内层尝试'})).toHaveTextContent('未知');
 expect(screen.getByRole('button')).toBeDisabled();
});

it('aligns account curves by timestamp without substituting zero for missing baseline values',()=>{
 const data:Evidence={authoritative:false,economic_eligible:false,status:'COMPLETED_TECHNICAL_ONLY',outer_cases:[
  {name:'portfolio',fee_bps:4,slippage_bps:5,status:'PASSED',metrics:{equity:[{time:1000,equity:'1500'},{time:2000,equity:'1499'}]}},
  {name:'single_baseline',fee_bps:4,slippage_bps:5,status:'PASSED',metrics:{equity:[{time:2000,equity:'1498'}]}}]};
 render(<PortfolioResearch busy={false} complete action={vi.fn()} data={data}/>);
 const rows=JSON.parse(screen.getByLabelText('账户曲线').textContent!);
 expect(rows[0].baseline).toBeUndefined();expect(rows[1].baseline).toBe(1498);
});

it('keeps portfolio observation controls separate from the original model observer',async()=>{
 const {PortfolioForward}=await import('./PortfolioForward');const action=vi.fn();
 const view=render(<PortfolioForward busy={false} action={action}/>);
 expect(screen.getByRole('button',{name:'启用组合观察'})).toBeDisabled();
 view.rerender(<PortfolioForward busy={false} action={action} data={{status:'COMPLETED_TECHNICAL_ONLY',authoritative:false,economic_eligible:false}}/>);
 fireEvent.click(screen.getByRole('button',{name:'启用组合观察'}));expect(action).toHaveBeenCalledWith('portfolio_observe');
 expect(screen.getByRole('button',{name:'停止组合观察'})).toBeDisabled();
 expect(screen.getByRole('region',{name:'组合配对预测'})).not.toHaveTextContent('0.000');
});
