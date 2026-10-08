import {it,expect,vi} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {SleeveExecutionEvidence} from './SleeveExecutionEvidence';
import {PortfolioResearch} from './PortfolioResearch';
import {PortfolioForward} from './PortfolioForward';

it('offers independent sleeve research without relabeling an existing forecast blend',()=>{
 const action=vi.fn();render(<PortfolioResearch sleeves busy={false} complete action={action}/>);
 fireEvent.click(screen.getByRole('button',{name:'运行独立策略组合'}));expect(action).toHaveBeenCalledOnce();
 expect(screen.getByRole('heading',{name:'独立策略持仓与统一账户'})).toBeInTheDocument();
});

it('activates the dedicated sleeve observer and identifies raw component forecasts',()=>{
 const action=vi.fn();render(<PortfolioForward sleeves busy={false} action={action}
  data={{status:'COMPLETED_TECHNICAL_ONLY',authoritative:false,economic_eligible:false}}/>);
 fireEvent.click(screen.getByRole('button',{name:'启用独立策略观察'}));
 expect(action).toHaveBeenCalledWith('sleeve_observe');
 expect(screen.getByText(/逐组件原始预测/)).toBeInTheDocument();
 expect(screen.getByRole('heading',{name:'独立策略前向成本账户'})).toBeInTheDocument();
});

it('keeps rejected intent distinct from actual owned position and explains limited trace coverage',()=>{
 render(<SleeveExecutionEvidence data={{construction:'INDEPENDENT_DECISIONS_ONE_WAY_NET_ACCOUNT_V1',
  ending:{component_ids:[2,5],quarters:[2,2],positions:{'2':'0','5':'0'},desired_weights:{'2':'0.125','5':'0'},actual_quantity:'0'},
  decisions:[{time:1000,positions:{'2':'0','5':'0'},desired_weights:{'2':'0.125','5':'0'},gross_desired_weight:'0.125',net_desired_weight:'0.125',actual_quantity:'0',target:0.125,choices:[]}],
  execution:[{time:1000,status:'REJECTED_CAPACITY',quantity:'0',equity:'1500',reason:'DAILY_TARGET',positions:{'2':'0','5':'0'},internal_reallocation:false}],
  decision_count:150,execution_count:250,trace_hash:'a'.repeat(64)}}/>);
 expect(screen.getByRole('region',{name:'策略持仓分配'})).toHaveTextContent('0.125');
 expect(screen.getByText('容量拒绝',{exact:true})).toBeInTheDocument();
 expect(screen.getByText(/150 次决策/)).toBeInTheDocument();
 expect(screen.getByText(/250 次账户反馈/)).toBeInTheDocument();
 expect(screen.getByText(/没有独立的策略损益账本/)).toBeInTheDocument();
});
