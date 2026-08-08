export type WorkbenchObjectId =
  | 'market-monitor'
  | 'alpha-control-plane'
  | 'validation-evidence'
  | 'portfolio-risk'
  | 'execution-orders';

export interface WorkbenchDataSourceEntry {
  id: WorkbenchObjectId;
  route: string;
  purpose: string;
  authoritativeEndpoints: readonly string[];
  acceptedProvenance: readonly string[];
  allowSyntheticFallback: false;
  forbiddenDependencies: readonly ['exchange-adapter', 'sqlite', 'browser-reported-identity'];
}

const FORBIDDEN = ['exchange-adapter', 'sqlite', 'browser-reported-identity'] as const;

export const WORKBENCH_DATA_SOURCE_MANIFEST: readonly WorkbenchDataSourceEntry[] = [
  {
    id: 'market-monitor',
    route: '/workbench/market',
    purpose: '市场连续性、调度、水位线、风险摘要与告警',
    authoritativeEndpoints: ['/api/v1/console/mft-execution', '/api/v1/console/overview'],
    acceptedProvenance: ['REAL_MFT_READ_MODEL', 'REAL_HOURLY_ALPHA_AUTHORITIES'],
    allowSyntheticFallback: false,
    forbiddenDependencies: FORBIDDEN,
  },
  {
    id: 'alpha-control-plane',
    route: '/workbench/alpha',
    purpose: '候选、家族、血缘、生命周期与自动研究任务',
    authoritativeEndpoints: [
      '/api/v1/console/hourly-alpha',
      '/api/v1/console/alpha-zoo',
      '/api/v1/console/alpha-lifecycle',
    ],
    acceptedProvenance: ['REAL_HOURLY_ALPHA_AUTHORITIES', 'REAL', 'REPOSITORY_EVIDENCE'],
    allowSyntheticFallback: false,
    forbiddenDependencies: FORBIDDEN,
  },
  {
    id: 'validation-evidence',
    route: '/workbench/validation',
    purpose: 'Trial Ledger、验证 DAG、反证与前向现实差距',
    authoritativeEndpoints: [
      '/api/v1/console/hourly-alpha',
      '/api/v1/console/validation',
      '/api/v1/console/experiments',
    ],
    acceptedProvenance: ['REAL_HOURLY_ALPHA_AUTHORITIES', 'REAL', 'REPOSITORY_EVIDENCE'],
    allowSyntheticFallback: false,
    forbiddenDependencies: FORBIDDEN,
  },
  {
    id: 'portfolio-risk',
    route: '/workbench/portfolio-risk',
    purpose: '组合目标、实际仓位、敞口、归因、漂移与应急状态',
    authoritativeEndpoints: [
      '/api/v1/console/portfolio',
      '/api/v1/console/risk-dashboard',
      '/api/v1/console/mft-execution',
    ],
    acceptedProvenance: ['REAL_MFT_READ_MODEL', 'REAL', 'REPOSITORY_EVIDENCE'],
    allowSyntheticFallback: false,
    forbiddenDependencies: FORBIDDEN,
  },
  {
    id: 'execution-orders',
    route: '/workbench/execution',
    purpose: '意图、OMS、成交、持仓、TCA、对账与 mandate 到期',
    authoritativeEndpoints: [
      '/api/v1/console/mft-execution',
      '/api/v1/console/mft-execution/delta',
    ],
    acceptedProvenance: ['REAL_MFT_READ_MODEL'],
    allowSyntheticFallback: false,
    forbiddenDependencies: FORBIDDEN,
  },
] as const;
