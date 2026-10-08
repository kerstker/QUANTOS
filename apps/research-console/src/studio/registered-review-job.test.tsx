import {describe, expect, it, vi} from 'vitest';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter} from '@/lib/router';
import {Launch, RegisteredReviewResults} from './Research';
import {triggerRun} from '@/lib/api/research-runner-api';
import {fetchRegisteredReviewRequest} from '@/lib/api/research-console-api';
import {download} from './data';
import type {RegisteredReviewReport} from '@/lib/types/research';

vi.mock('@/lib/api/research-runner-api', () => ({
  getOperator: () => 'researcher', getRunnerToken: () => 'fixture',
  setOperator: vi.fn(), setRunnerToken: vi.fn(), cancelRun: vi.fn(),
  triggerRun: vi.fn().mockResolvedValue({run_id:'review-job', status:'queued'}),
}));
vi.mock('@/lib/api/research-console-api', () => ({
  fetchRegisteredReviewRequest: vi.fn(), fetchResearchRun: vi.fn(),
  fetchTechnicalReplay: vi.fn(), fetchForwardDashboard: vi.fn(),
  registeredReviewArchiveUrl: (id: string) => '/api/v1/console/registered-review-archive?review_request_id='+id,
}));
vi.mock('./data', async importOriginal => ({
  ...await importOriginal<typeof import('./data')>(), download:vi.fn(),
}));

const requestId = 'a'.repeat(64), invocationId = 'b'.repeat(64);
const report: RegisteredReviewReport = {
  schema:'QUANTOS_REGISTERED_RESEARCH_REVIEW_JOB_V1', status:'TECHNICAL_VALIDATION_ONLY',
  phase:'REVIEW_RECORDED', review_request_id:requestId, native_invocation_id:invocationId,
  attempt_count:3, statistical_decision:'PASS', authentication:'VERIFIED_SOURCE_AND_RESULT',
  qualification:'REJECTED', review_id:'c'.repeat(64), reasons:['NO_INDEPENDENT_OOS_PROOF'],
  oos_status:'UNKNOWN', risk_status:'REJECTED', checked_at_ms:1,
  economic_authority:false, promotion_approved:false, submission_enabled:false,
};

describe('registered project review product handoff', () => {
  it.each(['registered_review_request', 'registered_review', 'registered_review_recover'])('submits one bounded identity for %s', async kind => {
    vi.mocked(triggerRun).mockClear();
    render(<MemoryRouter><Launch initialKind={kind} health={{mode:'RESEARCH_RUNNER',
      live_disallowed:true, kinds:[kind], kind_policies:{}}}/></MemoryRouter>);
    const label = kind==='registered_review_request' ? '冻结运行编号' : '评审请求编号';
    const button = screen.getByRole('button', {name:'提交研究任务'});
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(label), {target:{value:'A'.repeat(64)}});
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(label), {target:{value:requestId}});
    fireEvent.click(button);
    await waitFor(() => expect(triggerRun).toHaveBeenCalledWith(kind,
      {[kind==='registered_review_request' ? 'native_invocation_id' : 'review_request_id']:requestId}, expect.any(String)));
    expect(screen.queryByLabelText('实验编号')).not.toBeInTheDocument();
  });

  it('retains rejection despite a valid signature and passing statistics', () => {
    render(<MemoryRouter><RegisteredReviewResults report={report}/></MemoryRouter>);
    expect(screen.getByText('签名已核验')).toBeInTheDocument();
    expect(screen.getByText('统计通过')).toBeInTheDocument();
    expect(screen.getByText('候选未准入')).toBeInTheDocument();
    expect(screen.getByText('锁定样本外验证')).toBeInTheDocument();
    expect(screen.getByText('未通过')).toBeInTheDocument();
    expect(screen.getByRole('link', {name:'恢复评审结果'})).toHaveAttribute('href',
      `/studio/new?kind=registered_review_recover&review_request_id=${requestId}`);
  });

  it('does not download missing material or claim unsigned verification', async () => {
    vi.mocked(download).mockClear();
    vi.mocked(fetchRegisteredReviewRequest).mockResolvedValue({source:'BACKEND_COMPUTED_SNAPSHOT',
      read_only:true, api_version:'0.1.0', authoritative:false, evidence_status:'TECHNICAL_VALIDATION_ONLY',
      data_provenance:'UNSIGNED_ORIGINAL_PROJECT_REVIEW_REQUEST', material_status:'MISSING',
      snapshot_status:'NO_SNAPSHOT', snapshot_id:null, snapshot_ids:[], review_request:null});
    render(<MemoryRouter><RegisteredReviewResults report={{...report, phase:'REQUEST_PREPARED',
      authentication:'UNSIGNED_REQUEST', qualification:'NOT_REVIEWED', review_id:null}}/></MemoryRouter>);
    expect(screen.getByText('等待独立评审')).toBeInTheDocument();
    expect(screen.queryByText('签名已核验')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name:'下载完整评审材料'}));
    await waitFor(() => expect(screen.getByText('评审材料缺失或无效，无法导出。')).toBeInTheDocument());
    expect(download).not.toHaveBeenCalled();
  });

  it('downloads all six original document roles with the immutable request identity', async () => {
    const packet={request:{schema:'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V1' as const,
      review_request_id:requestId, native_invocation_id:invocationId, project_cut_sha256:'d'.repeat(64),
      subject_sha256:'e'.repeat(64), created_at_ms:1, files_sha256:{},
      economic_authority:false as const, submission_enabled:false as const},
      subject:{}, project_cut:{}, source_manifest:{}, statistical_evidence:{}, projection:{},
      source_files:{schema:'QUANTOS_REVIEW_SOURCE_FILES_V1' as const, files:{'tools/fixture.py':'Cg=='}}};
    vi.mocked(fetchRegisteredReviewRequest).mockResolvedValue({source:'BACKEND_COMPUTED_SNAPSHOT',
      read_only:true, api_version:'0.1.0', authoritative:false, evidence_status:'TECHNICAL_VALIDATION_ONLY',
      data_provenance:'UNSIGNED_ORIGINAL_PROJECT_REVIEW_REQUEST', material_status:'VALID',
      snapshot_status:'REAL', snapshot_id:requestId, snapshot_ids:[requestId], review_request:packet});
    render(<MemoryRouter><RegisteredReviewResults report={report}/></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', {name:'下载完整评审材料'}));
    await waitFor(() => expect(download).toHaveBeenCalledWith(`project-review-${requestId}.json`,packet));
  });

  it('prefills the linked request without accepting a path as an identity', () => {
    render(<MemoryRouter><Launch initialKind="registered_review" initialReviewRequest={requestId}
      health={{mode:'RESEARCH_RUNNER',live_disallowed:true,kinds:['registered_review'],kind_policies:{}}}/></MemoryRouter>);
    expect(screen.getByLabelText('评审请求编号')).toHaveValue(requestId);
    expect(screen.getByRole('button', {name:'提交研究任务'})).toBeEnabled();
  });

  it.each(['QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V2',
    'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V3'] as const)('downloads every original for %s', async schema => {
    vi.mocked(download).mockClear();
    const packet={request:{schema,
      review_request_id:requestId, native_invocation_id:invocationId, project_cut_sha256:'d'.repeat(64),
      subject_sha256:'e'.repeat(64), result_material_sha256:'f'.repeat(64), input_material_sha256:'1'.repeat(64), created_at_ms:1,
      files_sha256:{}, economic_authority:false as const, submission_enabled:false as const},
      subject:{}, project_cut:{}, source_manifest:{}, statistical_evidence:{}, projection:{},
      source_files:{schema:'QUANTOS_REVIEW_SOURCE_FILES_V1' as const, files:{}}};
    vi.mocked(fetchRegisteredReviewRequest).mockResolvedValue({source:'BACKEND_COMPUTED_SNAPSHOT',
      read_only:true, api_version:'0.1.0', authoritative:false, evidence_status:'TECHNICAL_VALIDATION_ONLY',
      data_provenance:'UNSIGNED_ORIGINAL_PROJECT_REVIEW_REQUEST', material_status:'VALID',
      snapshot_status:'REAL', snapshot_id:requestId, snapshot_ids:[requestId], review_request:packet});
    const clicked: HTMLAnchorElement[]=[];
    const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(function(this: HTMLAnchorElement) {clicked.push(this);});
    try {
      render(<MemoryRouter><RegisteredReviewResults report={report}/></MemoryRouter>);
      fireEvent.click(screen.getByRole('button', {name:'下载完整评审材料'}));
      await waitFor(() => expect(clicked).toHaveLength(1));
      expect(clicked[0].getAttribute('href')).toBe('/api/v1/console/registered-review-archive?review_request_id='+requestId);
      expect(clicked[0].download).toBe(`project-review-${requestId}.tar`);
      expect(download).not.toHaveBeenCalled();
    } finally {click.mockRestore();}
  });
});
