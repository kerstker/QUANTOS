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
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    let deadline: ReturnType<typeof setTimeout>;
    const run = async () => {
      controller = new AbortController();
      setState(old => ({ ...old, key, data: old.key === key ? old.data : null, loading: true, error: null }));
      try {
        const data = await Promise.race([loader.current(controller.signal), new Promise<never>((_, reject) => { deadline = setTimeout(() => { controller.abort(); reject(new Error('读取超时，请重试。')); }, 15000); })]);
        if (!stopped)
          setState({ key, data, error: null, loading: false, updated: Date.now() });
      }
      catch (error) {
        if (!stopped)
          setState(old => ({ ...old, loading: false, error: error instanceof Error ? error.message : '读取失败' }));
      }
      clearTimeout(deadline);
      if (!stopped && poll)
        timer = setTimeout(run, poll);
    };
    void run();
    return () => { stopped = true; controller?.abort(); clearTimeout(timer); clearTimeout(deadline); };
  }, [key, revision, poll]);
  return { ...state, data: state.key === key ? state.data : null, refresh: useCallback(() => setRevision(n => n + 1), []) };
}
export const KIND_LABELS: Record<string, string> = { ai_factory:'AI 量化研究工厂', btc_factory: '工厂规则回放', btc_mechanism: '机制信息实验', btc_forward: '前向观察', perpetual_alpha: '永续 Alpha 研究', perpetual_replay: '永续技术回放', orderblock_research: '订单块研究', data_refresh: '数据更新', ashare_technical_search: 'A股技术搜索', ashare_forward_observation: 'A股前向观察' };
export const statusLabel = (status: string) => ({ NO_CAMPAIGNS:'等待研究计划',WAITING_RUNNER:'等待研究执行器',SUBMITTED:'已提交研究',WAITING_OBSERVATION_CAPACITY:'等待观察容量',WAITING_POLICY:'等待调度条件',AVAILABLE:'功能可用',PARTIAL:'部分完成',BLOCKED:'条件未满足',RESEARCH_ONLY:'仅研究',RESERVED:'轮次已预留',EXHAUSTED:'预算耗尽',STOPPED:'已停止',COMPLETED:'已完成', CANCELLED:'已取消', PAUSED:'已暂停', RUNNING:'实验运行中', VALIDATING_INPUT:'验证输入数据', OUTER_VALIDATION:'独立外层验证', done: '已完成', running: '运行中', queued: '排队中', error: '失败', cancelled: '已取消', PASSED: '通过', FAILED: '失败', READY: '模型就绪', WAITING_WINDOW: '等待决策窗口', WAITING_TRAINING: '积累训练样本', ONLINE_ENABLED: '在线工厂已启用', FROZEN: '模型已冻结', NO_FREEZE: '尚未冻结', OBSERVING: '观察中', LABELS_UPDATED: '标签已更新', REAL: '真实快照', TECHNICAL: '技术结果', DIAGNOSTIC: '诊断快照', SYNTHETIC: '占位数据', UNAVAILABLE: '不可用', STALE: '已过期', UNKNOWN: '状态未知' }[status] ?? status);
export function fmt(value: unknown, digits = 2): string {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean')
    return '—'; const n = Number(value); return Number.isFinite(n) ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n) : '—';
}
export function stamp(value: number | null | undefined, ms = false) { return value ? new Date(ms ? value : value * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—'; }
export function download(name: string, value: unknown) { const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' })); const link = document.createElement('a'); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
