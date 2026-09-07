export interface Point { time: number; value: number }
const DAY = 86400000;
const mean = (a: number[]) => a.reduce((s, n) => s + n, 0) / a.length;
const deviation = (a: number[]) => a.length < 2 ? null : Math.sqrt(a.reduce((s, n) => s + (n - mean(a)) ** 2, 0) / (a.length - 1));

export function analyzeEquity(input: Point[]) {
  const valid = input.every(p => Number.isFinite(p.time) && Math.abs(p.time) <= 8640000000000000 && Number.isFinite(p.value));
  const points = valid ? input : [];
  const ordered = points.every((p, i) => i === 0 || p.time > points[i - 1].time);
  const positive = points.length > 1 && ordered && points.every(p => p.value > 0);
  let peak = points[0]?.value ?? 0;
  const drawdown = points.map(p => { peak = Math.max(peak, p.value); return { time: p.time, value: peak > 0 ? Number(((p.value / peak - 1) * 100).toFixed(12)) : 0 }; });
  const returns = (positive ? points.slice(1) : []).flatMap((p, i) => points[i].value > 0 && p.time > points[i].time ? [{ time: p.time, value: (p.value / points[i].value - 1) * 100 }] : []);
  const regular = positive && points.slice(1).every((p, i) => p.time - points[i].time === DAY);
  const std = deviation(returns.map(p => p.value));
  const rollingVol = regular ? returns.flatMap((p, i) => i < 19 ? [] : [{ time: p.time, value: (deviation(returns.slice(i - 19, i + 1).map(x => x.value)) ?? 0) * Math.sqrt(365) }]) : [];
  const monthly: Record<string, number> = {};
  for (const p of returns) { const month = new Date(p.time).toISOString().slice(0, 7); monthly[month] = (monthly[month] ?? 1) * (1 + p.value / 100); }
  return { points, drawdown: positive ? drawdown : [], returns, rollingVol,
    monthly: Object.entries(monthly).map(([label, v]) => ({ label, value: (v - 1) * 100 })),
    totalReturn: positive ? (points[points.length - 1].value / points[0].value - 1) * 100 : null,
    maxDrawdown: positive ? -Math.min(...drawdown.map(p => p.value)) : null,
    annualVol: regular && std !== null ? std * Math.sqrt(365) : null,
    sharpe: regular && std !== null && std > 0 ? mean(returns.map(p => p.value)) / std * Math.sqrt(365) : null,
    regularDaily: regular };
}

export function histogram(values: number[], buckets = 12) {
  const data = values.filter(Number.isFinite);
  if (!data.length) return [];
  const min = Math.min(...data), max = Math.max(...data);
  const width = max === min ? 1 : (max - min) / buckets;
  const bins = Array.from({ length: max === min ? 1 : buckets }, (_, i) => ({ low: min + i * width, high: min + (i + 1) * width, count: 0 }));
  data.forEach(v => bins[Math.min(bins.length - 1, Math.floor((v - min) / width))].count++);
  return bins;
}

export function correlation(a: Point[], b: Point[]) {
  if ([a,b].some(points => new Set(points.map(p => p.time)).size !== points.length || points.some(p => !Number.isFinite(p.time)))) return null;
  const other = new Map(b.map(p => [p.time, p.value]));
  const pairs = a.filter(p => Number.isFinite(p.value) && Number.isFinite(other.get(p.time))).map(p => [p.value, other.get(p.time)!]);
  if (pairs.length < 3) return null;
  const x = mean(pairs.map(p => p[0])), y = mean(pairs.map(p => p[1]));
  const covariance = pairs.reduce((s, p) => s + (p[0] - x) * (p[1] - y), 0);
  const denom = Math.sqrt(pairs.reduce((s, p) => s + (p[0] - x) ** 2, 0) * pairs.reduce((s, p) => s + (p[1] - y) ** 2, 0));
  return denom > 0 ? covariance / denom : null;
}
