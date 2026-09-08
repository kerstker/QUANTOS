import {it,expect,vi,beforeEach} from 'vitest';
import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {Factory} from './Factory';
import {fetchFactory,controlFactory} from '@/lib/api/ai-factory-api';
vi.mock('@/lib/api/ai-factory-api',()=>({fetchFactory:vi.fn(),controlFactory:vi.fn()}));
vi.mock('@/lib/api/research-runner-api',()=>({getOperator:()=> 'researcher',getRunnerToken:()=> 'local-test-token',setOperator:vi.fn(),setRunnerToken:vi.fn()}));
const empty={sessions:[],selected:null,trials:[],events:[],errors:[],provider:{status:'NOT_CONFIGURED',model:'',connected:false},authoritative:false,read_only:true};
beforeEach(()=>{vi.mocked(fetchFactory).mockResolvedValue(empty as never);vi.mocked(controlFactory).mockReset();});
it('uses the configured local model and distinguishes recent successful calls',async()=>{
 vi.mocked(fetchFactory).mockResolvedValue({...empty,provider:{status:'RECENT_CALL_SUCCEEDED',model:'qwen3.8-27b',connected:true}} as never);
 vi.mocked(controlFactory).mockResolvedValue({session:{attempt:1}});
 render(<Factory/>);
 expect(await screen.findByText('模型最近调用成功')).toBeInTheDocument();
 expect(screen.getByText('qwen3.8-27b')).toBeInTheDocument();
 await waitFor(()=>expect(screen.getByLabelText('编排方式')).toHaveValue('ai'));
 fireEvent.click(screen.getByRole('button',{name:'启动研究工厂'}));
 await waitFor(()=>expect(controlFactory).toHaveBeenCalledWith(expect.objectContaining({mode:'ai'}),expect.any(String)));
});
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
it('distinguishes input features and execution thresholds for otherwise identical models',async()=>{
 const first={id:1,model:'ridge',features:[0,1,2,3],threshold_bps:20};
 const second={id:10,model:'ridge',features:[4],threshold_bps:30};
 vi.mocked(fetchFactory).mockResolvedValue({...empty,trials:[{ordinal:0,status:'PASSED',spec:first},{ordinal:1,status:'FAILED',spec:second,reason:'MODEL_FIT_ValueError'}]} as never);
 render(<Factory/>);
 const table=await screen.findByRole('region',{name:'实验对比表格'});
 await waitFor(()=>expect(table).toHaveTextContent('阈值 20 bp'));
 expect(table).toHaveTextContent('阈值 30 bp');
 expect(table).toHaveTextContent('1h 收益');
 expect(table).toHaveTextContent('主动成交不平衡');
});
