import {it,expect,vi} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {SleeveProcessComparison} from './SleeveProcessComparison';

it('shows paired cost comparisons and keeps missing groups unknown',()=>{
 const action=vi.fn();
 render(<SleeveProcessComparison busy={false} ready action={action} data={{
  status:'COMPLETED_TECHNICAL_ONLY',new_model_calls:0,refitted_models:0,failed_groups:1,
  rows:[{fold:0,group:'baseline',status:'COMPLETED',inner_trials_attempted:6,outer_cases_attempted:6,failed_cases:0,
   portfolio:{selected:{id:1,component_ids:[1,2],quarters:[2,2]},baseline:{id:0,component_ids:[1,2],quarters:[4,0]},
    decision:'STOP_NO_STABLE_NET_INCREMENT',comparisons:[
     {fee_bps:4,slippage_bps:5,net_pnl_delta:-3,portfolio_net_pnl:-10,baseline_net_pnl:-7,portfolio_drawdown:.02,baseline_drawdown:.01,portfolio_fills:10,baseline_fills:12},
     {fee_bps:8,slippage_bps:5,net_pnl_delta:-7,portfolio_net_pnl:-20,baseline_net_pnl:-13,portfolio_drawdown:.03,baseline_drawdown:.02,portfolio_fills:8,baseline_fills:9}]}},
   {fold:0,group:'random',status:'FAILED',stage:'OUTER_COMPARISON',reason:'FileNotFoundError',inner_trials_attempted:6,outer_cases_attempted:0,failed_cases:0}]}}/>);
 expect(screen.getByRole('region',{name:'独立策略三折比较'})).toHaveTextContent('-10.00');
 fireEvent.change(screen.getByLabelText('独立策略三折成本情景'),{target:{value:'8/5'}});
 const table=screen.getByRole('region',{name:'独立策略三折比较'});
 expect(table).toHaveTextContent('-20.00');expect(table).toHaveTextContent('FileNotFoundError');
 expect(screen.getByText(/零次新模型调用/)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'运行独立策略三折比较'})).toBeDisabled();
});

it('requires frozen upstream comparison and invokes the dedicated action',()=>{
 const action=vi.fn();const {rerender}=render(<SleeveProcessComparison busy={false} ready={false} action={action}/>);
 expect(screen.getByRole('button',{name:'运行独立策略三折比较'})).toBeDisabled();
 rerender(<SleeveProcessComparison busy={false} ready action={action}/>);
 fireEvent.click(screen.getByRole('button',{name:'运行独立策略三折比较'}));expect(action).toHaveBeenCalledWith('sleeve_compare');
});
