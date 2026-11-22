import { useCallback, useEffect, useRef, useState } from 'react';
export function useResource<T>(key: string, load: (signal: AbortSignal) => Promise<T>, poll = 0) {
  const loader = useRef(load);
  loader.current = load;
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{
    key: string;
    data: T | null;
    error: string | null;
    loading: boolean;
    updated: number | null;
  }>({ key, data: null, error: null, loading: true, updated: null });
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const schedule = (delay: number) => {
      if (!stopped && poll > 0)
        timer = setTimeout(() => void run(true), delay);
    };
    const run = async (background = false) => {
      if (background && typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        schedule(poll);
        return;
      }
      controller = new AbortController();
      if (!background) {
        setState(old => ({ ...old, key, data: old.key === key ? old.data : null, loading: true, error: null }));
      }
      try {
        const data = await Promise.race([
          loader.current(controller.signal),
          new Promise<never>((_, reject) => {
            deadline = setTimeout(() => {
              controller?.abort();
              reject(new Error('读取超时，请重试。'));
            }, 15000);
          }),
        ]);
        failures = 0;
        if (!stopped)
          setState({ key, data, error: null, loading: false, updated: Date.now() });
      } catch (error) {
        failures = Math.min(failures + 1, 4);
        if (!stopped)
          setState(old => ({ ...old, key, loading: false, error: error instanceof Error ? error.message : '读取失败' }));
      }
      if (deadline) clearTimeout(deadline);
      const nextDelay = failures > 0 ? Math.min(poll * 2 ** failures, 120000) : poll;
      schedule(nextDelay);
    };
    const onVisibility = () => {
      if (!stopped && poll > 0 && document.visibilityState === 'visible') {
        if (timer) clearTimeout(timer);
        void run(true);
      }
    };
    if (poll > 0 && typeof document !== 'undefined')
      document.addEventListener('visibilitychange', onVisibility);
    void run(false);
    return () => {
      stopped = true;
      if (poll > 0 && typeof document !== 'undefined')
        document.removeEventListener('visibilitychange', onVisibility);
      controller?.abort();
      if (timer) clearTimeout(timer);
      if (deadline) clearTimeout(deadline);
    };
  }, [key, revision, poll]);
  return { ...state, data: state.key === key ? state.data : null, refresh: useCallback(() => setRevision(n => n + 1), []) };
}
export const KIND_LABELS: Record<string, string> = { gold_bar_recover:'黄金三策略结果恢复', gold_bar_receipt:'黄金三策略回执核验', gold_bar_research:'黄金三策略实验', gold_factory_review:'黄金工厂独立核验更新', gold_collect_input:'黄金公开采集与数据装配', gold_prepare_input:'黄金研究数据装配', gold_replay:'黄金订单流信号回放', profitability_forward:'冻结预测前向成本账户',profitability_input:'冻结输入版本研究',profitability_study:'盈利能力验证', ai_factory:'AI 量化研究工厂', btc_factory: '工厂规则回放', btc_mechanism: '机制信息实验', btc_forward: '前向观察', perpetual_alpha: '永续 Alpha 研究', perpetual_replay: '永续技术回放', orderblock_research: '订单块研究', data_refresh: '数据更新', ashare_technical_search: 'A股技术搜索', ashare_forward_observation: 'A股前向观察' };
export const statusLabel = (status: string) => ({ DEGRADED:'运行降级',RETRY_REQUIRED:'检查点待恢复',DAILY_CHECKPOINT_EXISTS:'已有当日检查点',VERIFIED_DAILY_CHECKPOINT:'当日检查点已复核',VERIFIED_RESEARCH_CHECKPOINT:'检查点已验证',EXISTING_IMMUTABLE_CHECKPOINT:'已有检查点',VERIFIED_EXISTING_CHECKPOINT:'已有检查点已复核',healthy:'健康',down:'失败',degraded:'降级',unknown:'待核实', NO_CAMPAIGNS:'等待研究计划',WAITING_RUNNER:'等待研究执行器',SUBMITTED:'已提交研究',WAITING_OBSERVATION_CAPACITY:'等待观察容量',WAITING_POLICY:'等待调度条件',AVAILABLE:'功能可用',PARTIAL:'部分完成',BLOCKED:'条件未满足',RESEARCH_ONLY:'仅研究',RESERVED:'轮次已预留',EXHAUSTED:'预算耗尽',STOPPED:'已停止',COMPLETED:'已完成', CANCELLED:'已取消', PAUSED:'已暂停', RUNNING:'实验运行中', VALIDATING_INPUT:'验证输入数据', OUTER_VALIDATION:'独立外层验证', done: '已完成', running: '运行中', queued: '排队中', error: '失败', cancelled: '已取消', PASSED: '通过', FAILED: '失败', READY: '模型就绪', WAITING_WINDOW: '等待决策窗口', WAITING_TRAINING: '积累训练样本', ONLINE_ENABLED: '在线工厂已启用', FROZEN: '模型已冻结', NO_FREEZE: '尚未冻结', OBSERVING: '观察中', LABELS_UPDATED: '标签已更新', REAL: '真实快照', TECHNICAL: '技术结果', DIAGNOSTIC: '诊断快照', SYNTHETIC: '占位数据', UNAVAILABLE: '不可用', STALE: '已过期', UNKNOWN: '状态未知' }[status] ?? status);
Object.assign(KIND_LABELS, {
  registered_research: '原生登记研究', registered_research_recover: '原生登记研究恢复',
  registered_review_request: '准备原项目独立评审材料', registered_review: '消费原项目独立评审回复',
  registered_review_recover: '恢复原项目评审结果',
});
export function fmt(value: unknown, digits = 2): string {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean')
    return '—'; const n = Number(value); return Number.isFinite(n) ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n) : '—';
}
export function stamp(value: number | null | undefined, ms = false) { return value ? new Date(ms ? value : value * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—'; }
export function download(name: string, value: unknown) { const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' })); const link = document.createElement('a'); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
