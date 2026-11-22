/**
 * Pure deterministic model for QUANTOS AI Agent Automation & Live Graduation.
 *
 * Mirrors `services/intelligence/graduation_orchestrator.py` and
 * `live/governance/mandate_envelope.py` fail-closed invariants.
 * Never fabricates trading authority, profitability, or signed mandates.
 */

export type TargetVenue = 'testnet' | 'mainnet';
export type TargetExchange = 'binance' | 'bybit' | 'mexc';
export type LedgerVersion = 'LINEAR_PERP_LEDGER_V2' | 'INVALIDATED_PERP_LEDGER_V1';
export type StageStatus = 'PASS' | 'FAIL' | 'BLOCKED';
export type GraduationVerdict =
  | 'REJECTED'
  | 'TESTNET_MANDATE_READY'
  | 'BLOCKED_EXTERNAL_VERIFIER_REQUIRED'
  | 'MAINNET_HUMAN_SIGN_READY';

export interface SystemRiskCeiling {
  allowedInstruments: readonly string[];
  maxSingleOrderNotional: number;
  maxGrossNotional: number;
  maxNetNotional: number;
  maxValidityHours: number;
}

export const DEFAULT_SYSTEM_CEILING: SystemRiskCeiling = {
  allowedInstruments: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BTCUSDC'],
  maxSingleOrderNotional: 1000,
  maxGrossNotional: 5000,
  maxNetNotional: 3000,
  maxValidityHours: 24,
};

export interface GraduationInputs {
  strategyId: string;
  strategyVersionHash: string;
  targetVenue: TargetVenue;
  exchange: TargetExchange;
  allowedInstruments: string[];

  // Stage 1: PIT Data & Quality
  pitAsOfVerified: boolean;
  leakageAuditPassed: boolean;
  panelFreshnessDays: number;
  maxAllowedFreshnessDays: number;

  // Stage 2: V2 Perpetual Ledger & Statistical Validation
  ledgerVersion: LedgerVersion;
  includesFeesFundingSlippage: boolean;
  oosNetSharpe: number;
  deflatedSharpeProb: number;
  cpcvPassed: boolean;
  minOosSharpe: number;
  minDeflatedSharpeProb: number;

  // Stage 3: 9-Engine Research Risk & Drawdown
  researchRiskGatesPassed: number;
  researchRiskGatesTotal: number;
  maxBacktestDrawdown: number;
  maxAllowedDrawdown: number;

  // Stage 4: Testnet Soak & TCA Reconciliation
  soakHoursCompleted: number;
  minSoakHours: number;
  observedFills: number;
  minObservedFills: number;
  realizedSlippageBps: number;
  maxSlippageBps: number;
  ledgerReconciliationDriftUsd: number;
  maxReconciliationDriftUsd: number;

  // Stage 5: Independent Authority & Human Signer
  humanReviewerId: string;
  externalVerifierIntegrated: boolean;
  externalSignatureVerified: boolean;

  // Stage 6: Runtime Guardrails
  preTradeRiskGateActive: boolean;
  killSwitchReady: boolean;
  drawdownControllerActive: boolean;
  exchangeFiltersConfigured: boolean;
}

export const DEFAULT_GRADUATION_INPUTS: GraduationInputs = {
  strategyId: 'btc_hourly_v2_candidate',
  strategyVersionHash: 'v2-perp-pending-commit',
  targetVenue: 'testnet',
  exchange: 'binance',
  allowedInstruments: ['BTCUSDT'],

  pitAsOfVerified: true,
  leakageAuditPassed: true,
  panelFreshnessDays: 0.25,
  maxAllowedFreshnessDays: 1.0,

  ledgerVersion: 'LINEAR_PERP_LEDGER_V2',
  includesFeesFundingSlippage: true,
  oosNetSharpe: 1.15,
  deflatedSharpeProb: 0.88,
  cpcvPassed: true,
  minOosSharpe: 0.75,
  minDeflatedSharpeProb: 0.8,

  researchRiskGatesPassed: 9,
  researchRiskGatesTotal: 9,
  maxBacktestDrawdown: 0.09,
  maxAllowedDrawdown: 0.15,

  soakHoursCompleted: 0,
  minSoakHours: 72,
  observedFills: 0,
  minObservedFills: 20,
  realizedSlippageBps: 0,
  maxSlippageBps: 12,
  ledgerReconciliationDriftUsd: 0,
  maxReconciliationDriftUsd: 0.05,

  humanReviewerId: 'operator-lead',
  externalVerifierIntegrated: false,
  externalSignatureVerified: false,

  preTradeRiskGateActive: true,
  killSwitchReady: true,
  drawdownControllerActive: true,
  exchangeFiltersConfigured: true,
};

export interface StageEvaluation {
  stageId: string;
  order: number;
  title: string;
  summary: string;
  status: StageStatus;
  reasons: string[];
}

export interface GraduationAssessment {
  verdict: GraduationVerdict;
  verdictLabel: string;
  verdictDetail: string;
  passedCount: number;
  totalCount: number;
  stages: StageEvaluation[];
  aiDirectAuthority: false;
  requiresHumanSignature: true;
}

export interface MandateDraftInput {
  instruments: string[];
  maxSingleOrderNotional: number;
  maxGrossNotional: number;
  maxNetNotional: number;
  validityHours: number;
  nonce: string;
}

export interface UnsignedMandateProposal {
  schema_version: 1;
  status: 'UNSIGNED_AWAITING_HUMAN_SIGNATURE';
  ai_direct_authority: false;
  requires_human_signature: true;
  graduation_verdict: GraduationVerdict;
  strategy_id: string;
  strategy_version_hash: string;
  venue: TargetVenue;
  exchange: TargetExchange;
  allowed_instruments: string[];
  max_single_order_notional: number;
  max_gross_notional: number;
  max_net_notional: number;
  issued_at_ms: number;
  expires_at_ms: number;
  nonce: string;
}

export interface MandateDraftResult {
  valid: boolean;
  errors: string[];
  proposal: UnsignedMandateProposal | null;
  canonicalJson: string;
}

export interface DgxRunbookStep {
  step: string;
  title: string;
  goal: string;
  command: string;
  safetyNote: string;
}

const isFiniteNumber = (v: unknown): v is number =>
  typeof v === 'number' && Number.isFinite(v);

export function evaluateGraduation(inputs: GraduationInputs): GraduationAssessment {
  const stages: StageEvaluation[] = [];

  // Stage 1: PIT Data & Quality
  const s1: string[] = [];
  if (!inputs.pitAsOfVerified) s1.push('尚未验证决策时点 PIT as_of(t) 因果可用性');
  if (!inputs.leakageAuditPassed) s1.push('前瞻泄漏审计未通过');
  if (
    !isFiniteNumber(inputs.panelFreshnessDays) ||
    !isFiniteNumber(inputs.maxAllowedFreshnessDays) ||
    inputs.panelFreshnessDays < 0 ||
    inputs.maxAllowedFreshnessDays <= 0 ||
    inputs.panelFreshnessDays > inputs.maxAllowedFreshnessDays
  ) {
    s1.push('数据新鲜度超限或为非法数值');
  }
  if (!inputs.allowedInstruments.length) s1.push('未配置允许交易标的');
  stages.push({
    stageId: 'stage_1_pit_data',
    order: 1,
    title: 'PIT 因果数据与面板质量门',
    summary: '验证决策时点可用性、零前瞻泄漏与行情新鲜度',
    status: s1.length === 0 ? 'PASS' : 'FAIL',
    reasons: s1,
  });

  // Stage 2: V2 Perpetual Ledger & Statistical Validation
  const s2: string[] = [];
  if (inputs.ledgerVersion !== 'LINEAR_PERP_LEDGER_V2') {
    s2.push('已拦截历史 INVALIDATED_PERP_LEDGER_V1 口径，必须使用 V2 线性永续账本');
  }
  if (!inputs.includesFeesFundingSlippage) {
    s2.push('回测未计入手续费、资金费率与真实滑点成本');
  }
  if (!isFiniteNumber(inputs.oosNetSharpe) || inputs.oosNetSharpe < inputs.minOosSharpe) {
    s2.push(`锁定样本外 OOS 净夏普低于阈值 (${inputs.minOosSharpe})`);
  }
  if (
    !isFiniteNumber(inputs.deflatedSharpeProb) ||
    inputs.deflatedSharpeProb < inputs.minDeflatedSharpeProb
  ) {
    s2.push(`多重检验校正 DSR 概率低于阈值 (${inputs.minDeflatedSharpeProb})`);
  }
  if (!inputs.cpcvPassed) s2.push('CPCV 组合净化交叉验证未通过');
  stages.push({
    stageId: 'stage_2_v2_validation',
    order: 2,
    title: 'V2 永续账本与统计验证门',
    summary: '校验全成本模型、锁定 OOS、DSR 与 CPCV 防过拟合指标',
    status: s2.length === 0 ? 'PASS' : 'FAIL',
    reasons: s2,
  });

  // Stage 3: 9-Engine Research Risk & Drawdown
  const s3: string[] = [];
  if (
    !Number.isInteger(inputs.researchRiskGatesPassed) ||
    !Number.isInteger(inputs.researchRiskGatesTotal) ||
    inputs.researchRiskGatesTotal <= 0 ||
    inputs.researchRiskGatesPassed < inputs.researchRiskGatesTotal
  ) {
    s3.push(
      `9 引擎研究风险门未全部通过 (${inputs.researchRiskGatesPassed}/${inputs.researchRiskGatesTotal})`
    );
  }
  if (
    !isFiniteNumber(inputs.maxBacktestDrawdown) ||
    !isFiniteNumber(inputs.maxAllowedDrawdown) ||
    inputs.maxBacktestDrawdown < 0 ||
    inputs.maxAllowedDrawdown <= 0 ||
    inputs.maxBacktestDrawdown > inputs.maxAllowedDrawdown
  ) {
    s3.push(`回测最大回撤超出容忍上限 (${(inputs.maxAllowedDrawdown * 100).toFixed(1)}%)`);
  }
  stages.push({
    stageId: 'stage_3_research_risk',
    order: 3,
    title: '9 引擎研究风控与压力测试门',
    summary: '市场风险、集中度、相关性、回撤、压力情景全量通过',
    status: s3.length === 0 ? 'PASS' : 'FAIL',
    reasons: s3,
  });

  // Stage 4: Testnet Soak & TCA Reconciliation
  const s4: string[] = [];
  if (inputs.targetVenue === 'mainnet') {
    if (!isFiniteNumber(inputs.soakHoursCompleted) || inputs.soakHoursCompleted < inputs.minSoakHours) {
      s4.push(`测试网实盘浸泡时长不足 (${inputs.soakHoursCompleted}h / ${inputs.minSoakHours}h)`);
    }
    if (!Number.isInteger(inputs.observedFills) || inputs.observedFills < inputs.minObservedFills) {
      s4.push(`测试网真实回执成交笔数不足 (${inputs.observedFills} / ${inputs.minObservedFills})`);
    }
    if (
      !isFiniteNumber(inputs.realizedSlippageBps) ||
      inputs.realizedSlippageBps < 0 ||
      inputs.realizedSlippageBps > inputs.maxSlippageBps
    ) {
      s4.push(`TCA 实际滑点超出预算 (${inputs.realizedSlippageBps} bps > ${inputs.maxSlippageBps} bps)`);
    }
    if (
      !isFiniteNumber(inputs.ledgerReconciliationDriftUsd) ||
      inputs.ledgerReconciliationDriftUsd < 0 ||
      inputs.ledgerReconciliationDriftUsd > inputs.maxReconciliationDriftUsd
    ) {
      s4.push('唯一账本现金与持仓对账存在未解释偏差');
    }
  }
  stages.push({
    stageId: 'stage_4_soak_tca',
    order: 4,
    title: 'Testnet 浸泡与 TCA 账本对账门',
    summary:
      inputs.targetVenue === 'testnet'
        ? '当前目标为 Testnet 联调，将在本阶段积累真实回执与对账证据'
        : '进入 Mainnet 前必须完成持续浸泡、滑点校验与零偏差对账',
    status: s4.length === 0 ? 'PASS' : 'FAIL',
    reasons: s4,
  });

  // Stage 5: Human Reviewer & External Cryptographic Verifier
  const s5: string[] = [];
  let s5Status: StageStatus = 'PASS';
  if (!inputs.humanReviewerId.trim()) {
    s5.push('未指定具名人工复核责任人 (human_reviewer_id)');
    s5Status = 'FAIL';
  }
  if (inputs.targetVenue === 'mainnet') {
    if (!inputs.externalVerifierIntegrated) {
      s5.push('尚未接入独立外部密码学签名验证器 (Ed25519 / 硬件密钥)');
      s5Status = 'BLOCKED';
    } else if (!inputs.externalSignatureVerified) {
      s5.push('缺少针对当前策略哈希与 Mandate 载荷的有效人工签名');
      s5Status = 'BLOCKED';
    }
  }
  stages.push({
    stageId: 'stage_5_human_authority',
    order: 5,
    title: '人工治理与外部密码学签名门',
    summary: 'AI Agent 仅可提交提案，实盘必须由独立外部人类私钥签署有界 Mandate',
    status: s5Status,
    reasons: s5,
  });

  // Stage 6: Runtime Execution Guardrails
  const s6: string[] = [];
  if (!inputs.preTradeRiskGateActive) s6.push('交易前硬风控门 (PreTradeRiskGate) 未启用');
  if (!inputs.killSwitchReady) s6.push('全局熔断开关 (KillSwitch) 未就绪');
  if (!inputs.drawdownControllerActive) s6.push('动态回撤降杠杆控制器未启用');
  if (!inputs.exchangeFiltersConfigured) s6.push('交易所步长/最小名义额过滤器未配置');
  stages.push({
    stageId: 'stage_6_runtime_guardrails',
    order: 6,
    title: '确定性执行与实时风控护栏',
    summary: '交易前风控、Kill-Switch、动态回撤控制、限价保护与自动减仓就绪',
    status: s6.length === 0 ? 'PASS' : 'FAIL',
    reasons: s6,
  });

  const anyFail = stages.some((st) => st.status === 'FAIL');
  const anyBlocked = stages.some((st) => st.status === 'BLOCKED');

  let verdict: GraduationVerdict;
  let verdictLabel: string;
  let verdictDetail: string;
  if (anyFail) {
    verdict = 'REJECTED';
    verdictLabel = '未通过准入 · 禁止起草授权单';
    verdictDetail = '存在未通过的硬性研究或风控关卡，请先修复失败项再进入下一阶段。';
  } else if (anyBlocked) {
    verdict = 'BLOCKED_EXTERNAL_VERIFIER_REQUIRED';
    verdictLabel = '主网结构性封锁 · 需接入外部人工签名器';
    verdictDetail =
      '前置研究与风控条件满足，但 Mainnet 实盘必须接入独立外部密码学签名验证器并由人类签署 Mandate 才可解封。可导出待签名提案用于离线审核。';
  } else if (inputs.targetVenue === 'testnet') {
    verdict = 'TESTNET_MANDATE_READY';
    verdictLabel = 'Testnet 联调就绪 · 可生成人工授权提案';
    verdictDetail =
      '策略已满足进入 Binance / Bybit Testnet 模拟盘浸泡的前置要求，请人工核对限额后导出并离线签署。';
  } else {
    verdict = 'MAINNET_HUMAN_SIGN_READY';
    verdictLabel = '主网人工签名就绪 · 仍需离线密钥签署';
    verdictDetail = '全部 6 道关卡已满足，可导出带唯一 Nonce 与 SHA-256 绑定的主网 Mandate 提案。';
  }

  return {
    verdict,
    verdictLabel,
    verdictDetail,
    passedCount: stages.filter((st) => st.status === 'PASS').length,
    totalCount: stages.length,
    stages,
    aiDirectAuthority: false,
    requiresHumanSignature: true,
  };
}

export function canonicalizeJson(obj: unknown): string {
  if (obj === null || typeof obj !== 'object') {
    return JSON.stringify(obj);
  }
  if (Array.isArray(obj)) {
    return `[${obj.map((item) => canonicalizeJson(item)).join(',')}]`;
  }
  const entries = Object.entries(obj as Record<string, unknown>).sort(([a], [b]) =>
    a < b ? -1 : a > b ? 1 : 0
  );
  return `{${entries
    .map(([k, v]) => `${JSON.stringify(k)}:${canonicalizeJson(v)}`)
    .join(',')}}`;
}

export async function computeCanonicalSha256(canonicalJson: string): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalJson);
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

export function validateAndBuildMandateProposal(
  inputs: GraduationInputs,
  assessment: GraduationAssessment,
  draft: MandateDraftInput,
  ceiling: SystemRiskCeiling = DEFAULT_SYSTEM_CEILING,
  issuedAtMs: number = Date.now()
): MandateDraftResult {
  const errors: string[] = [];

  if (assessment.verdict === 'REJECTED') {
    errors.push('当前策略未通过 6 阶段准入检查 (REJECTED)，系统禁止生成 Mandate 提案');
  }
  if (!inputs.strategyId.trim()) errors.push('策略 ID 不能为空');
  if (!inputs.strategyVersionHash.trim()) errors.push('策略版本哈希不能为空');

  const allowedSet = new Set(ceiling.allowedInstruments);
  const instruments = Array.from(
    new Set(draft.instruments.map((s) => s.trim().toUpperCase()).filter(Boolean))
  ).sort();
  if (!instruments.length) {
    errors.push('至少选择一个允许交易的合约标的');
  } else if (instruments.some((inst) => !allowedSet.has(inst))) {
    errors.push('所选标的超出系统风控白名单上限');
  }

  const checkNotional = (label: string, val: number, maxCap: number) => {
    if (!isFiniteNumber(val) || val <= 0) {
      errors.push(`${label} 必须是大于 0 的有限数值`);
    } else if (val > maxCap) {
      errors.push(`${label} ($${val}) 超出系统策略上限 ($${maxCap})，人工仅可收紧不可放宽`);
    }
  };
  checkNotional('单笔最大名义额', draft.maxSingleOrderNotional, ceiling.maxSingleOrderNotional);
  checkNotional('账户总敞口上限 (Gross)', draft.maxGrossNotional, ceiling.maxGrossNotional);
  checkNotional('账户净敞口上限 (Net)', draft.maxNetNotional, ceiling.maxNetNotional);

  if (
    isFiniteNumber(draft.maxSingleOrderNotional) &&
    isFiniteNumber(draft.maxGrossNotional) &&
    draft.maxSingleOrderNotional > draft.maxGrossNotional
  ) {
    errors.push('单笔最大名义额不能大于账户总敞口上限');
  }
  if (
    isFiniteNumber(draft.maxNetNotional) &&
    isFiniteNumber(draft.maxGrossNotional) &&
    draft.maxNetNotional > draft.maxGrossNotional
  ) {
    errors.push('账户净敞口上限不能大于账户总敞口上限');
  }

  if (
    !Number.isInteger(draft.validityHours) ||
    draft.validityHours < 1 ||
    draft.validityHours > ceiling.maxValidityHours
  ) {
    errors.push(`授权有效期必须是 1 至 ${ceiling.maxValidityHours} 小时的整数`);
  }
  if (!draft.nonce || draft.nonce.trim().length < 16) {
    errors.push('防重放 Nonce 长度不能少于 16 位字符');
  }

  if (errors.length > 0) {
    return { valid: false, errors, proposal: null, canonicalJson: '' };
  }

  const proposal: UnsignedMandateProposal = {
    schema_version: 1,
    status: 'UNSIGNED_AWAITING_HUMAN_SIGNATURE',
    ai_direct_authority: false,
    requires_human_signature: true,
    graduation_verdict: assessment.verdict,
    strategy_id: inputs.strategyId.trim(),
    strategy_version_hash: inputs.strategyVersionHash.trim(),
    venue: inputs.targetVenue,
    exchange: inputs.exchange,
    allowed_instruments: instruments,
    max_single_order_notional: draft.maxSingleOrderNotional,
    max_gross_notional: draft.maxGrossNotional,
    max_net_notional: draft.maxNetNotional,
    issued_at_ms: issuedAtMs,
    expires_at_ms: issuedAtMs + draft.validityHours * 3_600_000,
    nonce: draft.nonce.trim(),
  };

  return {
    valid: true,
    errors: [],
    proposal,
    canonicalJson: canonicalizeJson(proposal),
  };
}

export function buildDgxRunbook(
  exchange: TargetExchange,
  venue: TargetVenue,
  strategyId: string
): DgxRunbookStep[] {
  const safeId = strategyId.trim() || 'btc_hourly_v2_candidate';
  return [
    {
      step: '01',
      title: '全量合规与数学门禁验收',
      goal: '在 DGX Spark 隔离 Python 3.12 环境中跑通所有回归与治理扫描',
      command:
        'python scripts/validate.py && python apps/research-console/scripts/run_paper_to_live_governance_check.py',
      safetyNote: '任何失败项都必须阻断后续上线流程，严禁跳过校验',
    },
    {
      step: '02',
      title: '启动 AI Agent 自主研究工厂 (L2 隔离态)',
      goal: '运行只读控制台 (8400) 与受控研究执行器 (8420)，由 Agent 持续搜索并执行 V2 永续账本验证',
      command:
        'uvicorn services.console.main:app --host 127.0.0.1 --port 8400 & uvicorn services.research_runner.main:app --host 127.0.0.1 --port 8420',
      safetyNote: 'AI Agent 仅具备 PROPOSE / EVALUATE / REQUEST_HUMAN_APPROVAL 权限，无法接触任何交易所密钥',
    },
    {
      step: '03',
      title: `部署 ${exchange.toUpperCase()} Testnet 72 小时浸泡与账本对账`,
      goal: '将测试网 API 密钥放入 gitignored 的 key/ 目录，验证限价保护、订单恢复、TCA 滑点与唯一账本零偏差',
      command: `python -m tools.live_ops_api --port 8440 --venue testnet --exchange ${exchange} --strategy ${safeId}`,
      safetyNote: '只读运维接口 8440 绝不暴露下单能力；先跑满 72 小时且回执对账残差为 0',
    },
    {
      step: '04',
      title: '离线人类私钥签署 Mandate Envelope',
      goal: '导出本页生成的 UNSIGNED Mandate 提案，用独立硬件/Ed25519 私钥离线签名并校验 Nonce',
      command: `python -c "from live.governance.mandate_envelope import verify_mandate_envelope; print('Plug your hardware Ed25519 ExternalSignatureVerifier before arming ${venue}')"`,
      safetyNote: '实盘前必须集成独立外部签名验证器；小额资金起步并设定 24 小时有效期与单标的硬上限',
    },
  ];
}
