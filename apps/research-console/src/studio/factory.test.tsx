import {it,expect,vi,beforeEach} from 'vitest';
import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {Factory} from './Factory';
import {fetchFactory,controlFactory} from '@/lib/api/ai-factory-api';
vi.mock('@/lib/api/ai-factory-api',()=>({fetchFactory:vi.fn(),controlFactory:vi.fn()}));
vi.mock('@/lib/api/research-runner-api',()=>({getOperator:()=> 'researcher',getRunnerToken:()=> 'local-test-token',setOperator:vi.fn(),setRunnerToken:vi.fn()}));
const empty={sessions:[],selected:null,trials:[],events:[],errors:[],provider:{status:'NOT_CONFIGURED',model:'',connected:false},authoritative:false,read_only:true};
beforeEach(()=>{vi.mocked(fetchFactory).mockResolvedValue(empty as never);vi.mocked(controlFactory).mockReset();});
it('shows disconnected AI and submits an executable baseline configuration',async()=>{
 render(<Factory/>);
 expect(await screen.findByText('AI 模型尚未配置')).toBeInTheDocument();
 expect(screen.getByRole('option',{name:/AI 编排/})).toBeDisabled();
 vi.mocked(controlFactory).mockResolvedValue({session:{attempt:1}});
 fireEvent.click(screen.getByRole('button',{name:'启动研究工厂'}));
 await waitFor(()=>expect(controlFactory).toHaveBeenCalledWith(expect.objectContaining({mode:'baseline',budget:3,attempt:1,action:'create'}),expect.any(String)));
});
it('retains the invocation key when retrying a failed request',async()=>{
 vi.mocked(controlFactory).mockRejectedValue(new Error('network unavailable'));render(<Factory/>);
 fireEvent.click(screen.getByRole('button',{name:'启动研究工厂'}));
 await screen.findByText('network unavailable');
 fireEvent.click(screen.getByRole('button',{name:'启动研究工厂'}));
 await waitFor(()=>expect(controlFactory).toHaveBeenCalledTimes(2));
 expect(vi.mocked(controlFactory).mock.calls[0][1]).toBe(vi.mocked(controlFactory).mock.calls[1][1]);
});
it('does not show invented equity when no experiments exist',async()=>{
 render(<Factory/>);await screen.findByText('AI 模型尚未配置');
 expect(screen.queryByRole('img')).not.toBeInTheDocument();
});
it('submits on an HTTP LAN origin where randomUUID is unavailable',async()=>{
 const getRandomValues=globalThis.crypto.getRandomValues.bind(globalThis.crypto);
 vi.stubGlobal('crypto',{getRandomValues});
 try {
  vi.mocked(controlFactory).mockResolvedValue({session:{attempt:1}});
  render(<Factory/>);await screen.findByText('AI 模型尚未配置');
  fireEvent.click(screen.getByRole('button',{name:'启动研究工厂'}));
  await waitFor(()=>expect(controlFactory).toHaveBeenCalledWith(expect.any(Object),expect.stringMatching(/^[0-9a-f-]{36}$/)));
 } finally {vi.unstubAllGlobals();}
});
