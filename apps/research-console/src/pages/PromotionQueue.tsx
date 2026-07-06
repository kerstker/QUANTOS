import React, { useCallback, useEffect, useState } from 'react';
import {
  approvePromotionRequest, executePromotionRequest, fetchPromotionCandidates,
  fetchPromotionRequest, fetchPromotionRequests, getGovToken, governanceEnabled,
  openPromotionRequest, rejectPromotionRequest, reviewPromotionRequest, setGovToken,
  type PromotionCandidatesPayload, type PromotionRequestDetail,
} from '@/lib/api/promotion-api';
import { getOperator, setOperator } from '@/lib/api/research-runner-api';
import { bi } from '@/lib/i18n/bilingual';
import {
  Chip, EmptyState, KpiStrip, PageHeader, PageShell, Panel, StatTile, StatusChip,
} from '@/ui/primitives';
import { stamp } from '@/ui/format';

/**
 * PromotionQueue — 研究晋级(→APPROVED)的多方批准操作台(战线①,2026-07-06)。
 * 三个不同真人走 发起→复核→批准→执行 四步,每步显示谁做的;后端 = promotion_api(端口 8460,
 * 默认不启动,须读过威胁模型说明后自行拉起)。前端的"同人置灰"只是提示——真拒绝在状态机
 * (ApprovalViolation → 409)。诚实边界:X-Operator 是自报问责标签、非身份认证。
 */

const OPEN_STATES = new Set(['REQUESTED', 'REVIEWED', 'APPROVED']);

function fmtSharpe(v: number | null): string {
  return v == null ? '—' : v.toFixed(2);
}

export default function PromotionQueue() {
  const enabled = governanceEnabled();
  const [govToken, setGovTokenState] = useState(getGovToken());
  const [operator, setOperatorState] = useState(getOperator());
  const [candidates, setCandidates] = useState<PromotionCandidatesPayload | null>(null);
  const [requests, setRequests] = useState<PromotionRequestDetail[]>([]);
  const [msg, setMsg] = useState<{ tone: 'pos' | 'neg' | 'info'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!enabled || !getGovToken()) return;
    setBusy(true);
    setLoadError(null);
    try {
      const [cands, list] = await Promise.all([fetchPromotionCandidates(), fetchPromotionRequests()]);
      const details = await Promise.all(list.requests.map(r => fetchPromotionRequest(r.request_id)));
      setCandidates(cands);
      setRequests(details);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [enabled]);

  useEffect(() => { void reload(); }, [reload]);

  async function act(label: string, fn: () => Promise<unknown>) {
    setMsg({ tone: 'info', text: bi(`执行中:${label} …`, `running: ${label} …`) });
    try {
      await fn();
      setMsg({ tone: 'pos', text: bi(`完成:${label}`, `done: ${label}`) });
      await reload();
    } catch (e) {
      setMsg({ tone: 'neg', text: e instanceof Error ? e.message : String(e) });
    }
  }

  async function onExecute(requestId: string) {
    setMsg({ tone: 'info', text: bi('执行晋级 …', 'executing …') });
    try {
      const out = await executePromotionRequest(requestId);
      if (out.ok) {
        setMsg({ tone: 'pos', text: bi(`已晋级:${out.alpha_id} → ${out.dst}`, `promoted: ${out.alpha_id} → ${out.dst}`) });
      } else {
        setMsg({
          tone: 'neg',
          text: bi(`晋级被证据门拒绝(${out.reasons.join('; ')})—— grant 已一次性烧毁,需重新走三方`,
            `guard rejected (${out.reasons.join('; ')}) — the grant is burned one-way; restart the 3-party flow`),
        });
      }
      await reload();
    } catch (e) {
      setMsg({ tone: 'neg', text: e instanceof Error ? e.message : String(e) });
    }
  }

  const open = requests.filter(r => OPEN_STATES.has(r.state));
  const closed = requests.filter(r => !OPEN_STATES.has(r.state));
  const ready = enabled && govToken.trim().length > 0;

  return (
    <PageShell>
      <PageHeader title="Promotion Queue" titleZh="晋级审批"
        subtitle={bi('多方人工批准驱动 alpha →APPROVED:发起 / 复核 / 批准 / 执行,三个不同真人,每步哈希链落库',
          'Multi-party human approval driving alpha →APPROVED: request / review / approve / execute, three distinct humans, every step hash-chained and persisted')} />

      {/* 威胁模型诚实说明(评审 #6)—— 与后端 docstring 一字对齐,常驻不可折叠 */}
      <div className="mb-3 rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-2xs text-muted">
        {bi('诚实边界:X-Operator 是自报问责标签、不是身份认证。「三个不同真人」在单机信任模型下 = 三个两两不同的标签;持 gov token 的任何本机进程(含 AI agent)都能用三个自报标签走完三方批准。它挡的是流程遗漏与无 token 进程,不挡蓄意冒名(真身份认证 = Phase 14)。promotion_api 默认不启动 —— 由用户读过 services/governance/promotion_api/main.py 的威胁模型说明后自行拉起;AI agent 运行环境不应持有该 token。APPROVED 只是研究生命周期状态,不解锁真钱(LIVE 仍被 readiness pin 挡死)。',
          'Honest boundary: X-Operator is a self-asserted accountability tag, NOT authentication. Any local process holding the gov token (including an AI agent) can walk the 3-party flow with three self-asserted labels. The service is OFF by default — start it only after reading the threat-model note in services/governance/promotion_api/main.py. APPROVED never unlocks live trading.')}
      </div>

      <KpiStrip cols={4}>
        <StatTile label={bi('候选 alpha', 'Candidates')} value={String(candidates?.candidates.length ?? 0)} accent />
        <StatTile label={bi('进行中', 'In flight')} value={String(open.length)} tone="info" />
        <StatTile label={bi('已关闭', 'Closed')} value={String(closed.length)} tone="muted" />
        <StatTile label={bi('服务', 'Service')} value={enabled ? (ready ? bi('已配置', 'ready') : bi('缺 token', 'no token')) : bi('未启动', 'offline')}
          tone={ready ? 'pos' : 'neg'} />
      </KpiStrip>

      <Panel eyebrow={bi('凭证', 'Credentials')} title={bi('操作者与 Gov Token', 'Operator & Gov Token')}
        right={<Chip label={enabled ? bi('promotion_api 已配置', 'promotion_api configured') : bi('promotion_api 未启动(默认)', 'promotion_api offline (default)')}
          tone={enabled ? 'pos' : 'muted'} />}>
        {!enabled && (
          <div className="mb-2 rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
            {bi('本服务默认不启动(威胁模型扩张需用户拍板)。启动:python -m uvicorn services.governance.promotion_api.main:app --host 127.0.0.1 --port 8460(单进程单 worker),再以 VITE_GOVERNANCE_URL=http://127.0.0.1:8460 运行前端。',
              'The service is OFF by default. Start: python -m uvicorn services.governance.promotion_api.main:app --host 127.0.0.1 --port 8460 (single worker), then run the console with VITE_GOVERNANCE_URL=http://127.0.0.1:8460.')}
          </div>
        )}
        <label className="mb-2 flex items-center gap-2 text-2xs text-faint">
          <span>{bi('操作者标签', 'Operator tag')}</span>
          <input value={operator} disabled={!enabled}
            onChange={e => { setOperatorState(e.target.value); setOperator(e.target.value); }}
            className="w-40 rounded border border-line bg-transparent px-2 py-1 text-xs text-ink disabled:text-faint"
            placeholder="console-operator" />
          <span className="text-faint/70">{bi('· 自报问责标签,非认证', '· self-asserted, NOT authn')}</span>
        </label>
        <label className="flex items-center gap-2 text-2xs text-faint">
          <span>Gov Token</span>
          <input type="password" value={govToken} disabled={!enabled}
            onChange={e => { setGovTokenState(e.target.value); setGovToken(e.target.value); }}
            className="w-40 rounded border border-line bg-transparent px-2 py-1 text-xs text-ink disabled:text-faint"
            placeholder={bi('粘贴 key/governance_token.txt', 'paste key/governance_token.txt')} />
          <span className="text-faint/70">{bi('· 本机共享密钥,仅存本标签页', '· local shared secret, this tab only')}</span>
          <button onClick={() => void reload()} disabled={!ready || busy}
            className={`ml-2 rounded-md border px-2.5 py-1 text-2xs transition ${ready && !busy
              ? 'border-accent/50 text-accent hover:bg-accent/10' : 'cursor-not-allowed border-line text-faint'}`}>
            {busy ? bi('加载中…', 'loading…') : bi('刷新', 'Refresh')}
          </button>
        </label>
        {loadError && <div className="mt-2 text-2xs text-neg">{loadError}</div>}
        {msg && <div className={`mt-2 text-2xs ${({ pos: 'text-pos', neg: 'text-neg', info: 'text-info' } as const)[msg.tone]}`}>{msg.text}</div>}
      </Panel>

      {/* ① 候选 alpha:证据摘要 + 发起申请 */}
      <Panel eyebrow={bi('第 1 步 · 发起', 'Step 1 · Request')} title={bi('候选 Alpha(证据摘要)', 'Candidate Alphas (evidence)')}
        right={<Chip label={bi('证据只来自真实 artifact,不代填', 'evidence from real artifacts only')} tone="info" />}>
        {!candidates || candidates.candidates.length === 0 ? (
          <EmptyState>{bi('无候选(需要 experiment registry 中存在 PAPER_TRADING/SHADOW/PAUSED 状态的 alpha)。',
            'No candidates (needs alphas in PAPER_TRADING/SHADOW/PAUSED in the experiment registry).')}</EmptyState>
        ) : (
          <div className="flex flex-wrap gap-2">
            {candidates.candidates.map(c => (
              <div key={c.alpha_id} className="min-w-[220px] rounded-lg border border-line px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-ink">{c.alpha_id}</span>
                  <StatusChip value={c.state} />
                </div>
                <div className="mt-1 font-mono text-2xs text-muted">
                  backtest_sharpe {fmtSharpe(c.evidence.backtest_sharpe)} · paper_sharpe {fmtSharpe(c.evidence.paper_sharpe)}
                  {' · '}trials {c.evidence.eval_trial_count ?? '—'}
                </div>
                {c.precheck_hints.length > 0 && (
                  <div className="mt-1 text-2xs text-warn" title={bi('预检提示:只提示不放行,真拒绝在证据门', 'precheck hints only — the guard decides')}>
                    ⚠ {c.precheck_hints.join(' · ')}
                  </div>
                )}
                <button onClick={() => void act(bi(`发起 ${c.alpha_id} 晋级申请`, `request ${c.alpha_id}`), () => openPromotionRequest(c.alpha_id))}
                  disabled={!ready}
                  className={`mt-2 rounded-md border px-2.5 py-1 text-2xs transition ${ready
                    ? 'border-accent/50 text-accent hover:bg-accent/10' : 'cursor-not-allowed border-line text-faint'}`}>
                  {bi('发起晋级申请 → APPROVED', 'Request promotion → APPROVED')}
                </button>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {/* ② 进行中:步骤时间线 + 下一步操作 */}
      <Panel eyebrow={bi('第 2–4 步 · 复核/批准/执行', 'Steps 2–4 · Review / Approve / Execute')}
        title={bi('进行中的申请', 'In-flight Requests')}
        right={<Chip label={bi('三个两两不同的操作者', 'three pairwise-distinct operators')} tone="info" />}>
        {open.length === 0 ? (
          <EmptyState>{bi('没有进行中的申请。', 'No in-flight requests.')}</EmptyState>
        ) : open.map(r => {
          const participated = r.steps.some(s => s.actor_id === operator.trim());
          const approver = r.steps.find(s => s.state === 'APPROVED')?.actor_id;
          const dupHint = bi('复核/批准人必须不同于前序参与人(前端提示;真拒绝在状态机)',
            'reviewer/approver must differ from prior participants (frontend hint; the SM enforces)');
          return (
            <div key={r.request_id} className="mb-3 rounded-lg border border-line px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-2xs text-faint">{r.request_id}</span>
                <span className="font-medium text-ink">{r.subject_id}</span>
                <StatusChip value={r.state} />
              </div>
              <ol className="mt-2 flex flex-wrap gap-3">
                {r.steps.map((s, i) => (
                  <li key={i} className="text-2xs text-muted">
                    <span className="font-mono">{s.state}</span>
                    {' · '}<span className="text-ink">{s.actor_id}</span>
                    {' · '}<span className="text-faint">{stamp(s.timestamp)}</span>
                    {s.note && <span className="text-faint"> · “{s.note}”</span>}
                  </li>
                ))}
              </ol>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {r.state === 'REQUESTED' && (
                  <>
                    <button onClick={() => void act(bi('复核', 'review'), () => reviewPromotionRequest(r.request_id))}
                      disabled={!ready || participated} title={participated ? dupHint : undefined}
                      className={`rounded-md border px-2.5 py-1 text-2xs ${ready && !participated
                        ? 'border-accent/50 text-accent hover:bg-accent/10' : 'cursor-not-allowed border-line text-faint'}`}>
                      {bi('复核通过', 'Review OK')}
                    </button>
                    <button onClick={() => void act(bi('否决', 'reject'), () => rejectPromotionRequest(r.request_id))}
                      disabled={!ready}
                      className={`rounded-md border px-2.5 py-1 text-2xs ${ready
                        ? 'border-neg/50 text-neg hover:bg-neg/10' : 'cursor-not-allowed border-line text-faint'}`}>
                      {bi('否决', 'Reject')}
                    </button>
                  </>
                )}
                {r.state === 'REVIEWED' && (
                  <>
                    <button onClick={() => void act(bi('批准', 'approve'), () => approvePromotionRequest(r.request_id))}
                      disabled={!ready || participated} title={participated ? dupHint : undefined}
                      className={`rounded-md border px-2.5 py-1 text-2xs ${ready && !participated
                        ? 'border-accent/50 text-accent hover:bg-accent/10' : 'cursor-not-allowed border-line text-faint'}`}>
                      {bi('批准(第三人)', 'Approve (3rd human)')}
                    </button>
                    <button onClick={() => void act(bi('否决', 'reject'), () => rejectPromotionRequest(r.request_id))}
                      disabled={!ready}
                      className={`rounded-md border px-2.5 py-1 text-2xs ${ready
                        ? 'border-neg/50 text-neg hover:bg-neg/10' : 'cursor-not-allowed border-line text-faint'}`}>
                      {bi('否决', 'Reject')}
                    </button>
                  </>
                )}
                {r.state === 'APPROVED' && (
                  <button onClick={() => void onExecute(r.request_id)}
                    disabled={!ready || operator.trim() !== approver}
                    title={operator.trim() !== approver
                      ? bi(`只有批准人 ${approver ?? '?'} 能执行自己的批准`, `only the approver ${approver ?? '?'} may execute`) : undefined}
                    className={`rounded-md border px-2.5 py-1 text-2xs ${ready && operator.trim() === approver
                      ? 'border-pos/50 text-pos hover:bg-pos/10' : 'cursor-not-allowed border-line text-faint'}`}>
                    {bi('执行晋级(烧毁 grant,一次性)', 'Execute (burns the grant, one-way)')}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </Panel>

      {/* ③ 历史 */}
      <Panel eyebrow={bi('历史', 'History')} title={bi('已关闭的申请(CONSUMED / REJECTED)', 'Closed Requests (CONSUMED / REJECTED)')}>
        {closed.length === 0 ? (
          <EmptyState>{bi('暂无历史。', 'No history yet.')}</EmptyState>
        ) : closed.map(r => (
          <div key={r.request_id} className="mb-2 flex flex-wrap items-center gap-2 text-2xs">
            <span className="font-mono text-faint">{r.request_id}</span>
            <span className="text-ink">{r.subject_id}</span>
            <StatusChip value={r.state} />
            <span className="text-muted">{r.steps.map(s => `${s.state}:${s.actor_id}`).join(' → ')}</span>
            {r.state === 'CONSUMED' && (
              <span className="text-faint">{bi('· grant 已烧毁(执行结果见审计链;失败须重走三方)', '· grant burned (see audit chain; failures require a fresh 3-party flow)')}</span>
            )}
          </div>
        ))}
      </Panel>
    </PageShell>
  );
}
