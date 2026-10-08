import {describe,it,expect,vi} from 'vitest';
import {render,screen} from '@testing-library/react';
import {fetchConsoleModuleSnapshot} from '@/lib/api/research-console-api';
import {ResearchAssessment} from './ResearchAssessment';
vi.mock('@/lib/api/research-console-api',()=>({fetchConsoleModuleSnapshot:vi.fn()}));
describe('research assessment',()=>{
 it('does not turn missing evidence into a zero score',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'NO_SNAPSHOT',assessment:null});
  render(<ResearchAssessment/>);
  expect(await screen.findByText('尚未接入研究评估资料')).toBeInTheDocument();
  expect(screen.queryByText('0%')).not.toBeInTheDocument();
 });
 it('distinguishes historical calculations from economic qualification',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'REAL',assessment:{dimensions:[{id:'profitability_evidence',verdict:'INSUFFICIENT'}],counts:{required_research_core:7,adapted_scoped_check:9,deferred_institutional_requirement:6},run_evidence:{completed_cases:12,planned_trials:12,failed_cases:0}}});
  render(<ResearchAssessment/>);
  expect(await screen.findByText('证据不足')).toBeInTheDocument();
  expect(screen.getByText('12 / 12')).toBeInTheDocument();
  expect(screen.getByText(/完成计算不等于策略通过/)).toBeInTheDocument();
 });
 it('shows verification failure without a successful result',async()=>{
  vi.mocked(fetchConsoleModuleSnapshot).mockResolvedValue({snapshot_status:'DATA_BLOCKED',assessment:null});
  render(<ResearchAssessment/>);
  expect(await screen.findByText('研究资料校验失败')).toBeInTheDocument();
 });
});
