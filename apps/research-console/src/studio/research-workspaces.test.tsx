import {beforeEach,expect,it,vi} from 'vitest';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from '@/lib/router';
import {Factory} from './Factory';
import {FactoryLifecycle} from './FactoryLifecycle';
import {fetchFactory,controlFactory} from '@/lib/api/ai-factory-api';
import {fetchLifecycle,controlCampaign} from '@/lib/api/ai-factory-lifecycle-api';

vi.mock('@/lib/api/ai-factory-api',()=>({fetchFactory:vi.fn(),controlFactory:vi.fn(),importProfitabilityInput:vi.fn()}));
vi.mock('@/lib/api/ai-factory-lifecycle-api',()=>({fetchLifecycle:vi.fn(),controlCampaign:vi.fn()}));
vi.mock('@/lib/api/research-runner-api',()=>({getOperator:()=> 'researcher',getRunnerToken:()=> 'test-token',setOperator:vi.fn(),setRunnerToken:vi.fn()}));
const current={id:'current',label:'当前研究',writable:true};
const frozen={id:'frozen',label:'冻结观察',writable:false};
const payload={sessions:[],selected:null,trials:[],events:[],errors:[],provider:{status:'NOT_CONFIGURED',model:'',connected:false},workspace:frozen,workspaces:[current,frozen]};
beforeEach(()=>{
 vi.mocked(fetchFactory).mockResolvedValue(payload as never);
 vi.mocked(controlFactory).mockReset();vi.mocked(controlCampaign).mockReset();
 vi.mocked(fetchLifecycle).mockResolvedValue({...payload,models:[],campaigns:[],rounds:[],comparisons:[],sources:[],capabilities:[],scheduler_enabled:true} as never);
});

it('reads the linked workspace and cannot submit to the current runner while viewing it',async()=>{
 render(<MemoryRouter initialEntries={['/studio/factory?workspace=frozen&attempt=4']}><Factory/></MemoryRouter>);
 await waitFor(()=>expect(fetchFactory).toHaveBeenCalledWith(4,expect.any(AbortSignal),'frozen'));
 expect(await screen.findByLabelText('研究空间')).toHaveValue('frozen');
 expect(screen.queryByRole('button',{name:'启动研究工厂'})).not.toBeInTheDocument();
 expect(screen.queryByText('AI 模型尚未配置')).not.toBeInTheDocument();
 expect(controlFactory).not.toHaveBeenCalled();
});

it('clears the selected attempt and keeps writes disabled while a different workspace loads',async()=>{
 render(<MemoryRouter initialEntries={['/studio/factory?workspace=frozen&attempt=4']}><Factory/></MemoryRouter>);
 await screen.findByLabelText('研究空间');
 let finish:(value:never)=>void=()=>{};
 vi.mocked(fetchFactory).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 fireEvent.change(screen.getByLabelText('研究空间'),{target:{value:'current'}});
 await waitFor(()=>expect(fetchFactory).toHaveBeenLastCalledWith(null,expect.any(AbortSignal),'current'));
 expect(screen.getByRole('button',{name:'启动研究工厂'})).toBeDisabled();
 finish({...payload,workspace:current} as never);
 await waitFor(()=>expect(screen.getByRole('button',{name:'启动研究工厂'})).toBeEnabled());
});

it('keeps lifecycle navigation in the same read-only workspace',async()=>{
 render(<MemoryRouter initialEntries={['/studio/lifecycle?workspace=frozen']}><FactoryLifecycle/></MemoryRouter>);
 await waitFor(()=>expect(fetchLifecycle).toHaveBeenCalledWith(expect.any(AbortSignal),'frozen'));
 expect(screen.getByRole('link',{name:'打开实验工厂 →'})).toHaveAttribute('href','/studio/factory?workspace=frozen');
 fireEvent.click(screen.getByRole('tab',{name:'持续研究'}));
 expect(screen.getByRole('button',{name:'启动持续研究计划'})).toBeDisabled();
 expect(controlCampaign).not.toHaveBeenCalled();
});
