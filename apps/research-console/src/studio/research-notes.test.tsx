import {it,expect,vi} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {ResearchNotes} from './ResearchNotes';
import type {FactoryResearchNotes} from '@/lib/types/ai-factory';

it('keeps missing results unknown and requires a completed experiment to explain',()=>{
 const action=vi.fn();const view=render(<ResearchNotes busy={false} complete={false} action={action}/>);
 expect(screen.getByRole('button')).toBeDisabled();expect(screen.getByRole('status')).toHaveTextContent('缺失数值保持未知');
 view.rerender(<ResearchNotes busy={false} complete action={action}/>);
 fireEvent.click(screen.getByRole('button'));expect(action).toHaveBeenCalledOnce();
});

it('exposes the exact source for a measured zero without converting missing venue evidence to zero',()=>{
 const data:FactoryResearchNotes={scope:'HUMAN_READ_ONLY',authoritative:false,outer_results_available_to_search:false,history:null,episodes:[],verdict:{schema:'QUANTOS_RESEARCH_VERDICT_V1',scope:'OUTER_EXPLANATION',decision:'STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE',known_at_ms:1000,cost_scenarios:[],source_hashes:['a'.repeat(64)],model_hashes:[],profitability_established:false,search_feedback_allowed:false,economic_eligible:false,authoritative:false,facts:[{id:'primary.net_pnl',value:0,unit:'USDC',status:'MEASURED',evidence_hash:'a'.repeat(64),pointer:'/outer/net_pnl'},{id:'actual_venue_costs',value:null,unit:'USDC',status:'UNKNOWN',evidence_hash:null,pointer:null}]}};
 render(<ResearchNotes busy={false} complete action={vi.fn()} data={data}/>);
 expect(screen.getByRole('region',{name:'研究事实与引用'})).toHaveTextContent('0.0000 USDC');
 expect(screen.getByRole('region',{name:'研究事实与引用'})).toHaveTextContent('未知');
 fireEvent.click(screen.getByText('查看来源'));expect(screen.getByText('a'.repeat(64))).toBeVisible();
 expect(screen.getByRole('status')).toHaveTextContent('没有实际交易样本');
});
