# 机构工作台改版 — 设计原则与组件清单

状态:**设计基准文档(供页面级改版同事参照)** · 日期:2026-07-16
适用:`apps/research-console/`(React 18 + Vite 6 + TS strict + Tailwind)

本文档是 QUANTOS 研究控制台向「机构工作台(institutional workbench)」改版
的统一设计语言与组件契约。页面级实现由并行 worker 完成;改任何页面必须先
读本文件,保证 30 页视觉/交互/诚实性一致。

---

## 1. 设计原则

### P1 诚实优先(Honesty as a first-class visual state)
- 每个数据面必须显式呈现 provenance:`REAL_*` / `SYNTHETIC_DETERMINISTIC` /
  `UNCALIBRATED_PRINCIPLED` / `PARTIAL` 等标记是一等 UI 元素(provenance
  badge),不是脚注。
- 合成/缺失数据**永不伪装成真数据**;缺失子系统显示「PLANNED / 未建设」
  占位态,链接到 SystemCompleteness。
- 失败 fail-closed:快照标记校验不过 → 可见错误 + mock 回退,不渲染
  `undefined`。

### P2 密度与层级(机构终端密度,非营销页)
- 目标密度对标机构终端:表格/数字为主,图表为佐证,留白克制。
- 字号阶梯:页面标题 18 / 区块标题 13 大写追踪 / 正文 13 / 表格与指标
  12–13 等宽数字(`tabular-nums`)。
- 一页一任务:每页回答一个工作流问题(见 §3 页面契约),拒绝仪表盘式堆砌。

### P3 工作流导航(沿用 7 组 IA)
总览 → 数据 → 因子研究 → 验证 → 组合与风险 → 执行与跟踪 → 治理与系统。
新页仍走 `src/app/routes.tsx` 注册表(一行进导航 + 自动冒烟)。改版不改 IA,
只升级页内信息层级与组件。

### P4 状态显式化
- 所有异步面四态齐备:loading / error / empty / ready;empty 与 error 必须
  有明确文案与下一步(如「先运行 research run」)。
- 关键安全态(kill-switch ARM、gate 拒绝、mandate 待批)使用**持久可见**的
  状态条,不依赖 toast。

### P5 可操作但边界清晰
- 只读面(console 8400)与受控变更面(research_runner 8420 POST)在视觉上
  明确区分:变更按钮带「将触发白名单研究任务」确认文案,绝不出现任何下单
  /凭证入口。
- live 运维信息(live_ops 8440)以只读徽章呈现,标注「运维只读,下单走
  mandate 流水线」。

### P6 一致性与可测性
- 组件先走共享组件库(§4),页面不发明一次性样式。
- 契约不变:`*Payload` 类型 ↔ `tests/research/test_console_api_contract.py`
  同 commit 修改;改形状必须两侧同步。
- 每页保持冒烟测试通过;关键交互补组件级测试。

---

## 2. 视觉基调(Tailwind 令牌约定)

> **2026-07 更正:以代码为准。** 初稿的 zinc/emerald/rose 色板从未落地;实际
> 实现的权威主题是 `apps/research-console/src/index.css` 的机构终端主题
> (CSS 变量是唯一真源,Tailwind theme 只是其映射)。任何页面改版必须沿用
> 下表,不得回退到初稿色板。

| 语义 | 约定(已实现,`src/index.css`) |
|---|---|
| 背景层级 | 近黑画布 `--bg #0a0c10` / 顶栏侧栏 `--surface #0e1117` / 面板 `--panel #12151d`(顶 `--panel-2 #151924`)/ 内嵌井 `--inset #0c0f14` |
| 边框 | 发丝线 `--line rgba(148,163,184,.10)`,强线 `--line-strong .20`;圆角 `rounded-panel`/`rounded-tile` |
| 数字 | `.tnum`/`.ticker-num`(mono + tabular-nums);涨跌色 `--pos #2ebd85` / `--neg #f6465d`(交易所语义) |
| 主 accent | 机构琥珀 `--accent #f0b90b`(高光 `--accent-2 #f8d12e`),品牌渐变 `--grad` 琥珀坡 |
| 辅助语义 | info `--info #4b9bff`,warn 复用琥珀 `#f0b90b`,special `--special #8b9cff`,cyan `--cyan #45d3d8` |
| provenance | REAL=pos 绿徽章;SYNTHETIC=amber;UNCALIBRATED/PARTIAL=info;BLOCKED/UNAVAILABLE=neg 红(由 `ProvenanceRibbon` 统一渲染) |
| 危险/安全态 | kill-switch TRIPPED = `--neg` 红底持久状态条(`StatusBanner`),不依赖 toast |
| 终端质感 | `.terminal-panel`(发丝边框 + 淡网格底纹)用于 hero 面板;`.density-compact` 高密度表格/阶梯 |
| 交互 | hover `bg-elevated/40`;focus 可见 ring;禁用 40% 透明度;`prefers-reduced-motion` 全尊重 |
| 间距 | 页面 `PageShell`(max-w 1720,gap-5);面板 p-5(紧凑密度 `density-compact` 下 12px);表格行高 32–36 |

不引入新依赖;图表沿用 `src/ui/charts.tsx` 的 SVG 自绘风格(CandleChart/
EquityCurveChart 等,零图表库)。

---

## 3. 页面契约(每页必须满足)

1. **标题区**:页面问题一句话 + provenance badge + 数据 as-of 时间。
2. **KPI 行**(如适用):≤6 个 `MetricCard`,等宽数字,带口径注(如
   「夏普(周→年, VT 口径)」)。
3. **主证据区**:表格或图表,来源端点可追溯(hover/footnote 标端点路径)。
4. **状态区**:空/错/加载四态;失败给下一步指引。
5. **诚实脚注**:本页的口径限制(如「gross 无成本,RELATIVE 比较」「历史
   回测非前向」),与后端 honest-boundary 文案一致。

---

## 4. 共享组件清单(新建/收敛目标)

> **2026-07 更正:组件家 = `apps/research-console/src/ui/`(现实),** 初稿写的
> `src/components/workbench/` 从未存在。已实现组件与初稿命名的对应关系
> 一并列在下表「落地」列——优先复用已有,缺的已按本清单补齐。

页面级改版应优先复用/补齐以下共享组件(`src/ui/`):

| 组件 | 职责 | 落地 |
|---|---|---|
| `ProvenanceBadge` | 渲染 REAL/SYNTHETIC/UNCALIBRATED/PARTIAL/PLANNED 徽章 + tooltip | 已实现为 `primitives.tsx::ProvenanceRibbon`(更强:含连接态 + 数据源行) |
| `MetricCard` | 标签 + 等宽大数字 + 口径注 + 可选 delta 着色 | 已实现为 `primitives.tsx::StatTile` |
| `MetricRow` | KPI 行容器(≤6,响应式换行) | 已实现为 `primitives.tsx::KpiStrip`(+ `charts.tsx::MetricGrid`) |
| `DataTable` | 密度可调的通用表:排序、sticky 头、等宽数字列、行 hover、空态 | `primitives.tsx::DataTable`(本轮补 `stickyHeader` + `density="compact"` 变体) |
| `StatusBanner` | 持久状态条(kill-switch / gate / mandate / 快照校验失败) | `workbench.tsx::StatusBanner`(喂 `/safety-primitives`,常驻非 toast) |
| `Panel` | 面板容器:区块标题(大写追踪)+ 操作区 + 内容 | 已实现为 `primitives.tsx::Panel` |
| `EquityCurveChart` | SVG 净值曲线(多序列:RAW/VT/基准),复用干跑页风格 | `workbench.tsx::EquityCurveChart`(抽取 DryRunPipeline/LiveDesk 三处手写实现) |
| `StateView` | loading/error/empty/ready 四态包装器,统一文案规范 | `workbench.tsx::StateView` |
| `AsOfStamp` | 数据 as-of 时间 + 新鲜度着色(stale 警告) | `workbench.tsx::AsOfStamp` |
| `EndpointFootnote` | 数据来源端点路径脚注,便于审计追溯 | `workbench.tsx::EndpointFootnote` |
| `ConfirmActionButton` | 受控变更按钮(POST /run):确认文案 + 结果回执链接到 run | `workbench.tsx::ConfirmActionButton`(包 `dialog.tsx::useConfirm`) |
| `MandateGateChip` | mandate/attestation 状态只读徽章(待批/已批/拒绝) | `workbench.tsx::MandateGateChip` |
| `TickerTape` / `CandleChart` / `DepthChart` / `OrderBookLadder` / `MetricGrid` | 交易所级行情微结构视觉 | 已存在于 `charts.tsx`(本轮接进 AppShell tape 槽与 MEXC 页面,不再是孤儿组件) |

页面专属组件留在页面目录,不进共享库,直到出现第二个消费方。

---

## 5. 改版批次建议(供排期参照)

1. **批次 A(骨架)**:共享组件库落地 + 2 个样板页(建议 Overview 总览 +
   DryRunPipeline 执行)打样,跑通 P1–P6。
2. **批次 B(数据与研究组)**:数据目录、IC 面板、alpha-zoo、验证页套样板。
3. **批次 C(组合/执行/治理组)**:组合、风险、生命周期监控、safety、
   SystemCompleteness;接入 live_ops 只读徽章与 mandate 状态。
4. **批次 D(抛光)**:密度/对齐/键盘可达性走查,fe 测试地板保持
   30/30 冒烟 + 新增组件测试。

每批次完成标准:`npm run typecheck && npm test` 全绿 + 契约测试不红 +
四态手工走查。

---

## 6. 非目标

- 不新增路由组、不改 7 组 IA、不删页(删页走 SystemCompleteness 诚实流程)。
- 不引入 UI 框架依赖(MUI/AntD 等)、不引图表库。
- 不做任何下单/凭证/提现入口 —— 前端永不出现直接下单能力,live 操作
  一律指向 mandate 流水线与人工批准文档。
