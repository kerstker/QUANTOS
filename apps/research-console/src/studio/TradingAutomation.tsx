import { useEffect, useMemo, useState } from 'react';
import { Link } from '@/lib/router';
import type { RunnerHealth } from '@/lib/api/research-runner-api';
import { fetchBinanceDemo } from '@/lib/api/binance-demo-api';
import { Header, Metric, Panel } from './components';
import { download, useResource } from './data';
import {
  DEFAULT_GRADUATION_INPUTS,
  DEFAULT_SYSTEM_CEILING,
  buildDgxRunbook,
  computeCanonicalSha256,
  evaluateGraduation,
  validateAndBuildMandateProposal,
  type GraduationInputs,
  type LedgerVersion,
  type MandateDraftInput,
  type TargetExchange,
  type TargetVenue,
} from './trading-automation-model';
import './trading-automation.css';

interface RunsSummary {
  rows: Array<{ run_id?: string; status?: string }>;
  available: boolean;
}

function generateNonce(): string {
  const bytes = new Uint8Array(12);
  if (typeof globalThis.crypto !== 'undefined' && globalThis.crypto.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i++) bytes[i] = (Date.now() + i * 37) & 0xff;
  }
  const hex = Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
  return `quantos-${hex}`;
}

export function TradingAutomation({
  runs,
  health,
}: {
  runs: RunsSummary;
  health: RunnerHealth | null;
}) {
  const demo = useResource('studio:automation-demo', fetchBinanceDemo, 30000);
  const [inputs, setInputs] = useState<GraduationInputs>(DEFAULT_GRADUATION_INPUTS);
  const [draft, setDraft] = useState<MandateDraftInput>({
    instruments: ['BTCUSDT'],
    maxSingleOrderNotional: 500,
    maxGrossNotional: 2000,
    maxNetNotional: 1000,
    validityHours: 12,
    nonce: 'quantos-mandate-20260920-01',
  });
  const [issuedAtMs] = useState<number>(() => Date.now());
  const [sha256, setSha256] = useState<string>('');
  const [copiedStep, setCopiedStep] = useState<string>('');
  const [exportNotice, setExportNotice] = useState<string>('');

  const assessment = useMemo(() => evaluateGraduation(inputs), [inputs]);
  const draftResult = useMemo(
    () =>
      validateAndBuildMandateProposal(
        inputs,
        assessment,
        draft,
        DEFAULT_SYSTEM_CEILING,
        issuedAtMs
      ),
    [inputs, assessment, draft, issuedAtMs]
  );
  const runbook = useMemo(
    () => buildDgxRunbook(inputs.exchange, inputs.targetVenue, inputs.strategyId),
    [inputs.exchange, inputs.targetVenue, inputs.strategyId]
  );

  useEffect(() => {
    let cancelled = false;
    if (!draftResult.valid || !draftResult.canonicalJson) {
      setSha256('');
      return;
    }
    void computeCanonicalSha256(draftResult.canonicalJson)
      .then((digest) => {
        if (!cancelled) setSha256(digest);
      })
      .catch(() => {
        if (!cancelled) setSha256('digest-unavailable');
      });
    return () => {
      cancelled = true;
    };
  }, [draftResult.valid, draftResult.canonicalJson]);

  const toggleInstrument = (symbol: string) => {
    setDraft((prev) => {
      const exists = prev.instruments.includes(symbol);
      const next = exists
        ? prev.instruments.filter((item) => item !== symbol)
        : [...prev.instruments, symbol];
      return { ...prev, instruments: next };
    });
    setInputs((prev) => {
      const exists = prev.allowedInstruments.includes(symbol);
      const next = exists
        ? prev.allowedInstruments.filter((item) => item !== symbol)
        : [...prev.allowedInstruments, symbol];
      return { ...prev, allowedInstruments: next };
    });
  };

  const handleExportProposal = () => {
    if (!draftResult.valid || !draftResult.proposal) return;
    const envelope = {
      ...draftResult.proposal,
      proposal_id: `mandate-proposal-${sha256.slice(0, 20) || 'pending'}`,
      payload_sha256: sha256,
    };
    download(`quantos-unsigned-mandate-${inputs.targetVenue}.json`, envelope);
    setExportNotice(
      `已导出待人工签名提案 (${inputs.exchange.toUpperCase()} ${inputs.targetVenue.toUpperCase()})，请使用独立离线密钥签署。`
    );
  };

  const handleCopyCommand = async (step: string, cmd: string) => {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopiedStep(step);
      setTimeout(() => setCopiedStep((cur) => (cur === step ? '' : cur)), 2000);
    } catch {
      setCopiedStep(step);
    }
  };

  const bannerTone =
    assessment.verdict === 'TESTNET_MANDATE_READY' ||
    assessment.verdict === 'MAINNET_HUMAN_SIGN_READY'
      ? 'good'
      : assessment.verdict === 'BLOCKED_EXTERNAL_VERIFIER_REQUIRED'
        ? 'blocked'
        : 'bad';

  const demoConnected = demo.data?.public?.status === 'READY';

  return (
    <>
      <Header
        eyebrow="AUTOMATION / AI AGENT TO REAL TRADING"
        title="AI 自动化交易中枢"
        action={
          <Link className="s-button primary" to="/studio/factory">
            进入 AI 研究工厂
          </Link>
        }
      >
        机构级三层自动量化闭环：AI Agent 自主发现与验证策略 → 人工审查并离线签署有界 Mandate →
        确定性风控与执行引擎自动运行。严守「AI 绝不直接下实盘单」安全边界。
      </Header>

      <div className="s-metrics">
        <Metric
          label="运行边界"
          value="L2_DRY_RUN"
          note="默认禁止无授权实盘下单"
        />
        <Metric
          label="研究执行引擎"
          value={health ? '已连接' : '离线评估模式'}
          note={health ? 'services.research_runner :8420' : '启动本地研究服务后实时同步'}
        />
        <Metric
          label="已记录研究实验"
          value={runs.available ? runs.rows.length : '—'}
          note={runs.available ? '来自本地研究注册表' : '等待只读控制台 :8400'}
        />
        <Metric
          label="6 阶段准入进度"
          value={`${assessment.passedCount} / ${assessment.totalCount}`}
          note={demoConnected ? 'Testnet 公共接口已联通' : 'Testnet 待联调采集'}
        />
      </div>

      <section className={`a-banner ${bannerTone}`} aria-live="polite">
        <div>
          <h2>{assessment.verdictLabel}</h2>
          <p>{assessment.verdictDetail}</p>
        </div>
        <div className="a-banner-meta">
          <span className={`a-pill ${bannerTone === 'good' ? 'PASS' : bannerTone === 'blocked' ? 'BLOCKED' : 'FAIL'}`}>
            {assessment.verdict}
          </span>
          <small>AI 直接交易权限：已永久关闭 · 需人工签署：强制</small>
        </div>
      </section>

      <section className="a-architecture" aria-label="三层自动化交易架构">
        <article className="a-layer">
          <div className="a-layer-head">
            <span className="a-layer-step">LAYER 01 · RESEARCH</span>
            <span className="a-pill PASS">AI AGENT 驱动</span>
          </div>
          <h3>AI Agent 自主研究闭环</h3>
          <p>24/7 自动生成 Alpha 假说，执行 UCB / MCTS / 遗传进化搜索与防过拟合统计检验。</p>
          <ul className="a-layer-list">
            <li>PIT 因果面板检查，杜绝未来函数泄漏</li>
            <li>V2 Decimal 线性永续账本（含手续费/资金费/滑点）</li>
            <li>DSR 多重检验校正 + CPCV + 9 引擎研究风控</li>
          </ul>
          <div className="a-layer-actions">
            <Link className="s-button" to="/studio/factory">
              AI 研究工厂
            </Link>
            <Link className="s-button" to="/studio/lifecycle">
              Alpha 生命周期
            </Link>
          </div>
        </article>

        <article className="a-layer">
          <div className="a-layer-head">
            <span className="a-layer-step">LAYER 02 · GOVERNANCE</span>
            <span className="a-pill BLOCKED">人工签名隔离</span>
          </div>
          <h3>人工治理与有界 Mandate 签署</h3>
          <p>AI 仅能起草收紧型授权提案，由人类核验 OOS 与浸泡证据后用独立密钥离线签发。</p>
          <ul className="a-layer-list">
            <li>SHA-256 绑定策略版本哈希、白名单标的与资金上限</li>
            <li>单次有效 Nonce 防止授权重放，严格时效自动过期</li>
            <li>Mainnet 强依赖外部密码学签名器 (Ed25519 / 硬件)</li>
          </ul>
          <div className="a-layer-actions">
            <Link className="s-button" to="/studio/assessment">
              研究验收看板
            </Link>
            <a className="s-button" href="#mandate-builder">
              起草授权提案 ↓
            </a>
          </div>
        </article>

        <article className="a-layer">
          <div className="a-layer-head">
            <span className="a-layer-step">LAYER 03 · EXECUTION</span>
            <span className="a-pill PASS">硬风控状态机</span>
          </div>
          <h3>确定性 24/7 自动执行引擎</h3>
          <p>在已签 Mandate 边界内全自动执行信号、拆单、限价保护、回执恢复与动态降杠杆。</p>
          <ul className="a-layer-list">
            <li>PreTradeRiskGate 硬风控 + 全局 Kill-Switch 熔断</li>
            <li>DrawdownController 动态降杠杆 + 紧急只减仓平仓</li>
            <li>唯一账本实时对账 + TCA 滑点归因监控</li>
          </ul>
          <div className="a-layer-actions">
            <Link className="s-button" to="/studio/demo">
              DEMO 控制中心
            </Link>
            <Link className="s-button" to="/studio/operations">
              运行监控
            </Link>
          </div>
        </article>
      </section>

      <div className="a-split">
        <Panel
          title="6 阶段实盘毕业准入评估 (Fail-Closed)"
          note="对应后端 services/intelligence/graduation_orchestrator.py，任意一项缺失即阻断"
        >
          <div className="a-controls">
            <label>
              目标执行环境
              <select
                aria-label="目标执行环境"
                value={inputs.targetVenue}
                onChange={(e) =>
                  setInputs((prev) => ({ ...prev, targetVenue: e.target.value as TargetVenue }))
                }
              >
                <option value="testnet">Testnet 模拟盘浸泡 (推荐优先)</option>
                <option value="mainnet">Mainnet 真实资金 (结构性受锁)</option>
              </select>
            </label>
            <label>
              目标交易所
              <select
                aria-label="目标交易所"
                value={inputs.exchange}
                onChange={(e) =>
                  setInputs((prev) => ({ ...prev, exchange: e.target.value as TargetExchange }))
                }
              >
                <option value="binance">Binance Futures (USDⓈ-M)</option>
                <option value="bybit">Bybit Futures</option>
                <option value="mexc">MEXC Spot</option>
              </select>
            </label>
            <label>
              账本核算口径
              <select
                aria-label="账本核算口径"
                value={inputs.ledgerVersion}
                onChange={(e) =>
                  setInputs((prev) => ({
                    ...prev,
                    ledgerVersion: e.target.value as LedgerVersion,
                  }))
                }
              >
                <option value="LINEAR_PERP_LEDGER_V2">LINEAR_PERP_LEDGER_V2 (当前标准)</option>
                <option value="INVALIDATED_PERP_LEDGER_V1">
                  INVALIDATED_PERP_LEDGER_V1 (历史失效·强制拦截)
                </option>
              </select>
            </label>
            <label>
              人工复核责任人
              <input
                aria-label="人工复核责任人"
                value={inputs.humanReviewerId}
                onChange={(e) =>
                  setInputs((prev) => ({ ...prev, humanReviewerId: e.target.value }))
                }
                placeholder="例如 operator-lead"
              />
            </label>
            <label>
              OOS 锁定样本外净夏普
              <input
                aria-label="OOS 锁定样本外净夏普"
                type="number"
                step="0.05"
                value={inputs.oosNetSharpe}
                onChange={(e) =>
                  setInputs((prev) => ({ ...prev, oosNetSharpe: Number(e.target.value) }))
                }
              />
            </label>
            <label>
              回测最大回撤 (比例 0-1)
              <input
                aria-label="回测最大回撤"
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={inputs.maxBacktestDrawdown}
                onChange={(e) =>
                  setInputs((prev) => ({
                    ...prev,
                    maxBacktestDrawdown: Number(e.target.value),
                  }))
                }
              />
            </label>
          </div>

          {inputs.targetVenue === 'mainnet' && (
            <div className="a-controls">
              <label>
                Testnet 已完成浸泡小时数 (要求 ≥ 72h)
                <input
                  aria-label="Testnet 浸泡小时数"
                  type="number"
                  min="0"
                  value={inputs.soakHoursCompleted}
                  onChange={(e) =>
                    setInputs((prev) => ({
                      ...prev,
                      soakHoursCompleted: Number(e.target.value),
                    }))
                  }
                />
              </label>
              <label>
                Testnet 真实回执成交笔数 (要求 ≥ 20)
                <input
                  aria-label="Testnet 成交笔数"
                  type="number"
                  min="0"
                  value={inputs.observedFills}
                  onChange={(e) =>
                    setInputs((prev) => ({ ...prev, observedFills: Number(e.target.value) }))
                  }
                />
              </label>
            </div>
          )}

          <div className="a-checks">
            <label className="a-check">
              <input
                type="checkbox"
                checked={inputs.pitAsOfVerified}
                onChange={(e) =>
                  setInputs((prev) => ({ ...prev, pitAsOfVerified: e.target.checked }))
                }
              />
              PIT as_of(t) 因果校验通过
            </label>
            <label className="a-check">
              <input
                type="checkbox"
                checked={inputs.cpcvPassed}
                onChange={(e) =>
                  setInputs((prev) => ({ ...prev, cpcvPassed: e.target.checked }))
                }
              />
              CPCV 与全成本建模通过
            </label>
            <label className="a-check">
              <input
                type="checkbox"
                checked={inputs.preTradeRiskGateActive && inputs.killSwitchReady}
                onChange={(e) =>
                  setInputs((prev) => ({
                    ...prev,
                    preTradeRiskGateActive: e.target.checked,
                    killSwitchReady: e.target.checked,
                  }))
                }
              />
              PreTradeRiskGate & Kill-Switch 就绪
            </label>
            <label className="a-check">
              <input
                type="checkbox"
                checked={inputs.externalVerifierIntegrated}
                disabled
              />
              独立外部密码学签名器已集成 (主网硬锁)
            </label>
          </div>

          <ol className="a-stages" aria-label="6阶段准入检查明细">
            {assessment.stages.map((st) => (
              <li key={st.stageId} className="a-stage">
                <div className="a-stage-top">
                  <div className="a-stage-title">
                    <span className="a-stage-num">0{st.order}</span>
                    <span>{st.title}</span>
                  </div>
                  <span className={`a-pill ${st.status}`}>{st.status}</span>
                </div>
                <p className="a-stage-summary">{st.summary}</p>
                {st.reasons.length > 0 && (
                  <ul className="a-stage-reasons">
                    {st.reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        </Panel>

        <div id="mandate-builder">
          <Panel
            title="有界 Mandate 人工授权提案起草"
            note="仅可收紧系统上限（单笔 ≤ $1,000 · 总敞口 ≤ $5,000 · 净敞口 ≤ $3,000 · 有效期 ≤ 24h）"
          >
            <div className="a-form">
              <label>
                策略标识 (strategy_id)
                <input
                  aria-label="策略标识"
                  value={inputs.strategyId}
                  onChange={(e) =>
                    setInputs((prev) => ({ ...prev, strategyId: e.target.value }))
                  }
                />
              </label>

              <label>
                策略不可变版本哈希 (strategy_version_hash)
                <input
                  aria-label="策略版本哈希"
                  value={inputs.strategyVersionHash}
                  onChange={(e) =>
                    setInputs((prev) => ({ ...prev, strategyVersionHash: e.target.value }))
                  }
                />
              </label>

              <div>
                <small className="a-field-caption">
                  允许交易合约白名单
                </small>
                <div className="a-instruments">
                  {DEFAULT_SYSTEM_CEILING.allowedInstruments.map((sym) => {
                    const active = draft.instruments.includes(sym);
                    return (
                      <button
                        type="button"
                        key={sym}
                        aria-pressed={active}
                        className={`a-inst-btn ${active ? 'selected' : ''}`}
                        onClick={() => toggleInstrument(sym)}
                      >
                        {sym}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="a-form-grid">
                <label>
                  单笔最大名义额 (USD)
                  <input
                    aria-label="单笔最大名义额"
                    type="number"
                    min="1"
                    value={draft.maxSingleOrderNotional}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        maxSingleOrderNotional: Number(e.target.value),
                      }))
                    }
                  />
                </label>
                <label>
                  账户总敞口上限 Gross (USD)
                  <input
                    aria-label="账户总敞口上限"
                    type="number"
                    min="1"
                    value={draft.maxGrossNotional}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        maxGrossNotional: Number(e.target.value),
                      }))
                    }
                  />
                </label>
                <label>
                  账户净敞口上限 Net (USD)
                  <input
                    aria-label="账户净敞口上限"
                    type="number"
                    min="1"
                    value={draft.maxNetNotional}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        maxNetNotional: Number(e.target.value),
                      }))
                    }
                  />
                </label>
                <label>
                  授权有效期 (小时 · 1-24)
                  <input
                    aria-label="授权有效期"
                    type="number"
                    min="1"
                    max="24"
                    value={draft.validityHours}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        validityHours: Number(e.target.value),
                      }))
                    }
                  />
                </label>
              </div>

              <label>
                防重放一次性 Nonce (≥16 字符)
                <div className="a-actions-row">
                  <input
                    className="a-flex-input"
                    aria-label="防重放 Nonce"
                    value={draft.nonce}
                    onChange={(e) => setDraft((prev) => ({ ...prev, nonce: e.target.value }))}
                  />
                  <button
                    type="button"
                    className="s-button"
                    onClick={() => setDraft((prev) => ({ ...prev, nonce: generateNonce() }))}
                  >
                    生成随机 Nonce
                  </button>
                </div>
              </label>

              {draftResult.errors.length > 0 && (
                <ul className="a-errors" role="alert">
                  {draftResult.errors.map((err) => (
                    <li key={err}>{err}</li>
                  ))}
                </ul>
              )}

              <div className="a-digest-box">
                <span>Canonical Payload SHA-256（供离线人类私钥签署核对）</span>
                <code data-testid="mandate-sha256">
                  {draftResult.valid ? sha256 || '计算中…' : '参数校验未通过，摘要已清空'}
                </code>
              </div>

              {draftResult.valid && draftResult.proposal && (
                <pre className="a-code" aria-label="待签名 Mandate 提案 JSON">
                  {JSON.stringify(draftResult.proposal, null, 2)}
                </pre>
              )}

              <div className="a-actions-row">
                <button
                  type="button"
                  className="s-button primary"
                  disabled={!draftResult.valid || !sha256}
                  onClick={handleExportProposal}
                >
                  导出待签名 Mandate 提案 (JSON)
                </button>
              </div>
              {exportNotice && (
                <p role="status" className="a-export-notice">
                  {exportNotice}
                </p>
              )}
            </div>
          </Panel>
        </div>
      </div>

      <Panel
        title="DGX Spark 真实量化推进操作手册 (Runbook)"
        note="按顺序在您的本地 DGX Spark 环境执行；所有真实交易所密钥仅存放在 gitignored 的 key/ 目录，绝不进入前端或仓库"
      >
        <div className="a-runbook">
          {runbook.map((item) => (
            <article key={item.step} className="a-runbook-card">
              <div className="a-runbook-head">
                <h3>
                  STEP {item.step} · {item.title}
                </h3>
                <button
                  type="button"
                  className="s-button"
                  onClick={() => void handleCopyCommand(item.step, item.command)}
                >
                  {copiedStep === item.step ? '已复制' : '复制命令'}
                </button>
              </div>
              <p>{item.goal}</p>
              <code className="a-runbook-cmd">{item.command}</code>
              <span className="a-runbook-note">安全守则：{item.safetyNote}</span>
            </article>
          ))}
        </div>
      </Panel>
    </>
  );
}
