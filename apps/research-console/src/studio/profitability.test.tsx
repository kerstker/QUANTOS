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
 expect(screen.getByText('-12.226896')).toBeInTheDocument();
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
