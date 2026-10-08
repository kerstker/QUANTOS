import {it,expect,vi} from 'vitest';
import {render,fireEvent,waitFor} from '@testing-library/react';
import {MemoryRouter} from '@/lib/router';
import {Launch} from './Research';
import {triggerRun} from '@/lib/api/research-runner-api';
vi.mock('@/lib/api/research-runner-api',()=>({getOperator:()=> 'test',getRunnerToken:()=> 'token',setOperator:vi.fn(),setRunnerToken:vi.fn(),triggerRun:vi.fn(),cancelRun:vi.fn()}));
it.each(['gold_prepare_input', 'gold_bar_recover', 'gold_bar_receipt'])('submits numbered %s through the research launcher',async(kind)=>{
 vi.mocked(triggerRun).mockClear();
 vi.mocked(triggerRun).mockResolvedValue({run_id:'prepared'} as never);
 render(<MemoryRouter><Launch initialKind={kind} initialAttempt="7" health={{kinds:[kind],status:'ok'} as never}/></MemoryRouter>);
 fireEvent.submit(document.querySelector('form')!);
 await waitFor(()=>expect(triggerRun).toHaveBeenCalledWith(kind,{attempt:7},expect.any(String)));
});
it('submits paired collection with a separate new snapshot and explicit template',async()=>{
 vi.mocked(triggerRun).mockClear();
 vi.mocked(triggerRun).mockResolvedValue({run_id:'collected'} as never);
 render(<MemoryRouter><Launch initialKind="gold_collect_input" initialAttempt="7" health={{kinds:['gold_collect_input'],status:'ok'} as never}/></MemoryRouter>);
 fireEvent.submit(document.querySelector('form')!);
 await waitFor(()=>expect(triggerRun).toHaveBeenCalledWith('gold_collect_input',{attempt:7,template_id:1,seconds:30},expect.any(String)));
});
