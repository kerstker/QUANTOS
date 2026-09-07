import {fireEvent,render,screen,within} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import {MemoryRouter} from '@/lib/router';
import {Compare} from './Compare';
import type {ResearchRunSummary} from '@/lib/types/research';
vi.mock('@/lib/api/research-console-api',()=>({fetchTechnicalReplay:vi.fn(async(id:string)=>({technical_replay:{metrics:{input_hash:'same-input'},params:{attempt:id==='a'?1:2},factory_report:{cases:[{policy:'FACTORY',slip_bps:5,stop:true,metrics:{net_pnl:id==='a'?-10:-14,fees:'2',funding:'-1',max_drawdown:.01,fills:1}},{policy:'FACTORY',slip_bps:10,stop:true,status:'FAILED'}]}}}))}));
it('compares the same policy and preserves missing failed-scenario values',async()=>{
 const rows: ResearchRunSummary[]=['a','b'].map((run_id,i)=>({run_id,kind:'btc_factory',status:'done',title:run_id,created_ts:100+i,params:{attempt:i+1},headline:{label:null,value:null},verdict:'TECHNICAL_VALIDATION_ONLY',code_version:null}));
 render(<MemoryRouter><Compare runs={{rows,loading:false,error:null,available:true,refresh:vi.fn()}}/></MemoryRouter>);
 const row=(await screen.findByText('净收益 USDC')).closest('tr')!;
 expect(within(row).getByText('-4.000000')).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('对比滑点'),{target:{value:'10'}});
 expect(screen.getByText('选定情景没有可比较的指标')).toBeInTheDocument();
 expect(screen.queryByText('-4.000000')).not.toBeInTheDocument();
});
