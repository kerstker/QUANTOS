# 一次配置：采集完成后自动创建黄金研究计划

生命周期页面新增“先采集新数据，完成后自动启动计划”。所选快照作为参数模板，新数据使用独立编号，目标、轮数、冷却时间和可选整轮反馈等待配置保持原请求。原 `/factory-campaign` 接受 collect_create，在同一FactoryStore登记有摘要的采集请求，再提交原JobQueue的 gold_collect_campaign；研究端仍只有编号参数的白名单任务。

worker先核对模板摘要，执行原同窗公开采集/装配，再检查捕获保存的模板与原请求一致，使用新快照的实际编号/摘要调用原create_campaign。后续由原campaign_tick、轮次预算及候选排除机制推进，没有新增实验账本或替代调度器。

原请求在campaign_collections表中持久保存，重复配置复用同一任务身份，改变已占用编号的请求拒绝。采集占用的计划编号不能被普通create抢用。页面展示采集请求以及是否已创建计划，失败原因继续在原研究任务中查看。只持有请求不等于正在运行；重启终结的任务不会被暗中重开采集。

取消仍使用原Runner软取消语义，采集可能继续完成本地落盘；调度器新增交接核对：原采集任务必须done、身份与任务类型一致、产出的输入编号/摘要匹配，才能启动研究。取消或错误的任务不释放后续研究预算；尚未新增自动重试或取消时强杀网络线程。

## 验证

原合法API请求先返回422，新增接线后同一HTTP请求经原队列捕获/装配、创建原campaign、原调度器执行AI候选（隔离模型）与成本实验，最终控制台读取原报告。重复提交不重复采集，改变配置拒绝；捕获失败保留请求、不创建计划；软取消后不启动研究。后端API/调度/认证/边界/控制台合同联合161通过、1跳过，增加取消交接后相关15项通过。前端6项、类型检查、ESLint及E9/F821通过。

仍需已保存的模板；采集为单次有限窗口，多轮研究固定使用新快照，不等同于每轮自动更新行情或全天运行。未部署，没有放宽独立核验或Demo/实盘授权。

## 一次真实HTTP请求贯通

验证根 `/home/kerstker/quantos-dev-output/gold-one-request-20260929-0e1l_1zd`。一次真实TestClient HTTP请求提交collect_create，60秒公开同窗采集后自动建立原campaign，验证驱动仅调用原campaign_tick等待推进，没有另行创建计划。随后真实本地qwen3.8-27b完成一次有限候选选择及成本实验。

- 采集交接任务：run_1052ce795edbf2f3e44ba9c9fcb74eb4，done。
- 自动研究任务：run_723b1aa26dc4ae483acfc42db07a4ce0，done。
- 新快照摘要：0bb7bf56a92581ebd07cab27120fec339ef73c88af676d791dbfea31ec8aa7c8。
- 原试验：f1773f16d00bdaf90b2c9c8184ba77c6611f87de0ae4edf4c3656d845b082b2d。
- 原计划COMPLETED，结果NO_SIGNAL，模拟权益1000保持不变、对账残差0；控制台报告AVAILABLE。
- 独立核验仍INDEPENDENT_AUTHORITY_UNCONFIGURED，economic_eligible=false，submission_enabled=false。

request-receipt.json和validation-summary.json留存实际请求/终态，所有正式任务使用当前墙钟。该驱动没有启动驻留调度服务；这证明一次真实请求的功能交接，不是全天服务运行验收、策略有效性或Demo执行。治理扫描703文件0违规，文档卫生通过。
