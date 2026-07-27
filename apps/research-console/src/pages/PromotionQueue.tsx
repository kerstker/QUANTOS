import { useCallback, useEffect, useState } from 'react';
import {
  approvePromotionRequest, executePromotionRequest, fetchPromotionCandidates,
  fetchPromotionRequest, fetchPromotionRequests, getGovernanceAccessToken, governanceEnabled,
  openPromotionRequest, rejectPromotionRequest, reviewPromotionRequest,
  type PromotionCandidatesPayload, type PromotionRequestDetail,
} from '@/lib/api/promotion-api';
import {
  beginGovernancePkceAuthorization,
  clearGovernancePkceSession,
  completeGovernancePkceAuthorization,
  governancePkceEnabled,
} from '@/lib/api/governance-pkce';
import { bi } from '@/lib/i18n/bilingual';
import {
  Button, Chip, DataTable, EmptyState, KpiStrip, PageHeader, PageShell, Panel, StatTile, StatusChip,
} from '@/ui/primitives';
import { fixed, stamp } from '@/ui/format';
import { useConfirm } from '@/ui/dialog';
import { useToast } from '@/ui/toast';

/**
 * PromotionQueue — 研究晋级(→APPROVED)的多方批准操作台(战线①,2026-07-06)。
 * 三个不同外部身份走 发起→复核→批准→执行 四步,每步显示不可变 issuer|subject;
 * 角色与同人复用由后端强身份依赖和状态机 fail-closed 拒绝。
 */

const OPEN_STATES = new Set(['REQUESTED', 'REVIEWED', 'APPROVED']);

export default function PromotionQueue() {
  const enabled = governanceEnabled();
  const pkceEnabled = governancePkceEnabled();
  const [authenticated, setAuthenticated] = useState(
    () => getGovernanceAccessToken().length > 0,
  );
  const [candidates, setCandidates] = useState<PromotionCandidatesPayload | null>(null);
  const [requests, setRequests] = useState<PromotionRequestDetail[]>([]);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const confirm = useConfirm();
  const toast = useToast();

  const reload = useCallback(async () => {
    if (!enabled || !getGovernanceAccessToken()) return;
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

  useEffect(() => {
    void completeGovernancePkceAuthorization()
      .then(result => {
        if (result.status !== 'AUTHENTICATED') return;
        setAuthenticated(true);
        toast.success(bi('OIDC 登录完成', 'OIDC sign-in complete'));
        void reload();
      })
      .catch(error => {
        setAuthenticated(false);
        setLoadError(error instanceof Error ? error.message : String(error));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload]);

  async function onSignIn() {
    setBusy(true);
    setLoadError(null);
    try {
      const authorizationUrl = await beginGovernancePkceAuthorization();
      window.location.assign(authorizationUrl);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  }

  function onSignOut() {
    clearGovernancePkceSession();
    setAuthenticated(false);
    setCandidates(null);
    setRequests([]);
    toast.info(bi('OIDC 会话已清除', 'OIDC session cleared'));
  }

  async function act(label: string, fn: () => Promise<unknown>) {
    try {
      await fn();
      toast.success(bi(`完成:${label}`, `done: ${label}`));
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  }

  async function onExecute(requestId: string) {
    const confirmed = await confirm({
      title: bi('执行晋级', 'Execute promotion'),
      message: bi('这会一次性烧毁本次多方批准的 grant——无论证据门通过与否,grant 都不可复用;若被拒绝需重新走三方批准。',
        'This burns the multi-party grant one-way — whether the evidence guard passes or not, the grant cannot be reused; a rejection requires restarting the 3-party flow.'),
      severity: 'warn', confirmLabel: bi('执行(烧毁 grant)', 'Execute (burn grant)'),
    });
    if (!confirmed) return;
    try {
      const out = await executePromotionRequest(requestId);
      if (out.ok) {
        toast.success(bi(`已晋级:${out.alpha_id} → ${out.dst}`, `promoted: ${out.alpha_id} → ${out.dst}`));
      } else {
        toast.error(bi(`晋级被证据门拒绝(${out.reasons.join('; ')})—— grant 已一次性烧毁,需重新走三方`,
          `guard rejected (${out.reasons.join('; ')}) — the grant is burned one-way; restart the 3-party flow`));
      }
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  }

  async function onReject(requestId: string) {
    const confirmed = await confirm({
      title: bi('否决申请', 'Reject request'),
      message: bi('否决后该申请关闭,需重新发起才能再次尝试晋级。', 'Rejecting closes this request — promoting again requires opening a fresh one.'),
      severity: 'warn', confirmLabel: bi('否决', 'Reject'),
    });
    if (!confirmed) return;
    await act(bi('否决', 'reject'), () => rejectPromotionRequest(requestId));
  }

  const open = requests.filter(r => OPEN_STATES.has(r.state));
  const closed = requests.filter(r => !OPEN_STATES.has(r.state));
  const ready = enabled && pkceEnabled && authenticated;

  return (
    <PageShell>
      <PageHeader title="Promotion Queue" titleZh="晋级审批"
        subtitle={bi('多方人工批准驱动 alpha →APPROVED:发起 / 复核 / 批准 / 执行,三个不同真人,每步哈希链落库',
          'Multi-party human approval driving alpha →APPROVED: request / review / approve / execute, three distinct humans, every step hash-chained and persisted')} />

      {/* 强身份边界常驻说明。 */}
      <div className="mb-3 rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-2xs text-muted">
        {bi('身份边界:后端只接受非对称签名的 OIDC access token、MFA、mTLS 证书绑定与动作角色;操作者来自不可变 issuer|subject,前端不能自报。浏览器已使用 Authorization Code + PKCE S256,但外部 IdP 与可信 TLS 部署证据仍未提供,因此本能力仍非 M4。APPROVED 只表示研究生命周期状态,不解锁真钱。',
          'Identity boundary: the backend requires an asymmetrically signed OIDC access token, MFA, mTLS certificate binding, and action roles. The browser uses Authorization Code + PKCE S256; external IdP and trusted TLS deployment evidence remain open. APPROVED never unlocks live trading.')}
      </div>

      <KpiStrip cols={4}>
        <StatTile label={bi('候选 alpha', 'Candidates')} value={String(candidates?.candidates.length ?? 0)} accent />
        <StatTile label={bi('进行中', 'In flight')} value={String(open.length)} tone="info" />
        <StatTile label={bi('已关闭', 'Closed')} value={String(closed.length)} tone="muted" />
        <StatTile label={bi('身份', 'Identity')} value={ready
          ? bi('OIDC 会话', 'OIDC session')
          : (enabled && pkceEnabled ? bi('需要登录', 'sign-in required') : bi('PKCE 未配置', 'PKCE offline'))}
          tone={ready ? 'pos' : 'neg'} />
      </KpiStrip>

      <Panel eyebrow={bi('身份', 'Identity')} title={bi('OIDC + PKCE 会话', 'OIDC + PKCE Session')}
        right={<Chip label={enabled && pkceEnabled
          ? bi('PKCE S256 已配置', 'PKCE S256 configured')
          : bi('身份服务离线', 'identity service offline')}
          tone={enabled && pkceEnabled ? 'pos' : 'muted'} />}>
        {(!enabled || !pkceEnabled) && (
          <div className="mb-2 rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
            {bi('身份服务未配置。', 'Identity service is not configured.')}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 text-2xs text-faint">
          {!authenticated ? (
            <Button onClick={() => void onSignIn()} disabled={!enabled || !pkceEnabled || busy} variant="pos"
              className="font-semibold">
              {bi('OIDC 登录', 'OIDC sign in')}
            </Button>
          ) : (
            <Button onClick={onSignOut} variant="muted">
              {bi('退出', 'Sign out')}
            </Button>
          )}
          <Button onClick={() => void reload()} disabled={!ready || busy} variant="muted">
            {busy ? bi('加载中…', 'loading…') : bi('刷新', 'Refresh')}
          </Button>
        </div>
        {loadError && <div className="mt-2 text-2xs text-neg">{loadError}</div>}
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
                  backtest_sharpe {fixed(c.evidence.backtest_sharpe)} · paper_sharpe {fixed(c.evidence.paper_sharpe)}
                  {' · '}trials {c.evidence.eval_trial_count ?? '—'}
                </div>
                {c.precheck_hints.length > 0 && (
                  <div className="mt-1 text-2xs text-warn" title={bi('预检提示:只提示不放行,真拒绝在证据门', 'precheck hints only — the guard decides')}>
                    ⚠ {c.precheck_hints.join(' · ')}
                  </div>
                )}
                <Button onClick={() => void act(bi(`发起 ${c.alpha_id} 晋级申请`, `request ${c.alpha_id}`), () => openPromotionRequest(c.alpha_id))}
                  disabled={!ready} variant="accent" className="mt-2">
                  {bi('发起晋级申请 → APPROVED', 'Request promotion → APPROVED')}
                </Button>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {/* ② 进行中:步骤时间线 + 下一步操作 */}
      <Panel eyebrow={bi('第 2–4 步 · 复核/批准/执行', 'Steps 2–4 · Review / Approve / Execute')}
        title={bi('进行中的申请', 'In-flight Requests')}
        right={<Chip label={bi('三个两两不同的外部身份', 'three distinct external identities')} tone="info" />}>
        {open.length === 0 ? (
          <EmptyState>{bi('没有进行中的申请。', 'No in-flight requests.')}</EmptyState>
        ) : (
          <DataTable rows={open} getKey={r => r.request_id}
            searchable={r => `${r.request_id} ${r.subject_id} ${r.state}`}
            searchPlaceholder={bi('按 request id / alpha 搜索…', 'search by request id / alpha…')}
            columns={[
              { key: 'id', header: bi('申请', 'Request'), render: r => (
                  <div>
                    <div className="font-medium text-ink">{r.subject_id}</div>
                    <div className="font-mono text-2xs text-faint">{r.request_id}</div>
                  </div>
                ) },
              { key: 'state', header: bi('状态', 'State'), render: r => <StatusChip value={r.state} /> },
              { key: 'steps', header: bi('步骤时间线', 'Step timeline'), render: r => (
                  <ol className="flex flex-wrap gap-3">
                    {r.steps.map((s, i) => (
                      <li key={i} className="text-2xs text-muted">
                        <span className="font-mono">{s.state}</span>
                        {' · '}<span className="text-ink">{s.actor_id}</span>
                        {' · '}<span className="text-faint">{stamp(s.timestamp)}</span>
                        {s.note && <span className="text-faint"> · “{s.note}”</span>}
                      </li>
                    ))}
                  </ol>
                ) },
              { key: 'actions', header: bi('操作', 'Actions'), render: r => (
                  <div className="flex flex-wrap items-center gap-2">
                    {r.state === 'REQUESTED' && (
                      <>
                        <Button onClick={() => void act(bi('复核', 'review'), () => reviewPromotionRequest(r.request_id))}
                          disabled={!ready} variant="accent">
                          {bi('复核通过', 'Review OK')}
                        </Button>
                        <Button onClick={() => void onReject(r.request_id)}
                          disabled={!ready} variant="neg">
                          {bi('否决', 'Reject')}
                        </Button>
                      </>
                    )}
                    {r.state === 'REVIEWED' && (
                      <>
                        <Button onClick={() => void act(bi('批准', 'approve'), () => approvePromotionRequest(r.request_id))}
                          disabled={!ready} variant="accent">
                          {bi('批准(第三人)', 'Approve (3rd human)')}
                        </Button>
                        <Button onClick={() => void onReject(r.request_id)}
                          disabled={!ready} variant="neg">
                          {bi('否决', 'Reject')}
                        </Button>
                      </>
                    )}
                    {r.state === 'APPROVED' && (
                      <Button onClick={() => void onExecute(r.request_id)}
                        disabled={!ready} variant="pos">
                        {bi('执行晋级(烧毁 grant,一次性)', 'Execute (burns the grant, one-way)')}
                      </Button>
                    )}
                  </div>
                ) },
            ]} />
        )}
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
