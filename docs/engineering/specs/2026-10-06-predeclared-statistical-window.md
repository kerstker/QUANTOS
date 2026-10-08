# 预登记统计窗口与前向原件投影消费者修复

状态：相关回归已完成的开发增量；未部署。父提交
`df3b5a8031cb758de7f13059959dade47db5f1d2`；独立分支
`codex/predeclared-statistical-window-20261006`。

## 问题与行为

[评价核心](../../../quantos/contracts/research_evaluation_plan.py)要求训练、验证、
锁定 OOS 区间均位于总样本内。[原统计消费者](../../../services/validation/project_research_statistics.py)
先要求统计政策的窗口等于总样本，再要求其结束于 OOS 开始之前。
因此，有效声明了锁定 OOS 的核心也不能通过统计窗口绑定。

统计政策本来就限定 `TRAIN_VALIDATION_ONLY`。本增量将有 OOS 的统计窗口
严格绑定到核心已经声明的训练／验证窗口：只有一个窗口时用该窗口；两个
窗口时要求在原固定频率下紧邻，再取其完整连续区间。缺少窗口、区间间存在
未声明空档、改动起止或频率、包含 OOS 都拒绝。没有 OOS 时保留原总样本
精确绑定。政策 SHA、登记时间、来源、数据集和基准核对保持；不改核心原件。

扩大回归另外发现既有前向消费者按四项解包
[原件投影接口](../../../services/validation/project_research_evidence_authority.py)，
而该接口已有五项返回值。本增量只修正该消费者的解包项数。完整前向原件
仍返回 `INSUFFICIENT / FORWARD_NOT_BACKTEST`；原生 hash 保留，统计曲线为空。
迟到登记／invocation／outcome 事实仍需经过原 cohort 时间拒绝检查。

## 真实验证记录

独立输出为 `gold-predeclared-statistical-window-20261006`；使用已核对的
Python 3.12.3 ARM64 环境和原哈希锁，状态与临时目录均在该输出下。
精确 DGX 环境基线为 `/tmp/quantos-dgx-development-sbuj5ogw/baseline.json`，
仅环境核对，未运行 `--test`。

- `regression-red`：三个有效训练／验证窗口及实际原账本→统计评审共
  **4 FAIL / 15.87 秒**，driver 16.085194373 秒，均为原 `PREDECLARED_SAMPLE`
  拒绝；3133 Python 前后保持。红例及原输入不删。
- `registration-green`：五组相关回归 **114 PASS / 4 FAIL / 213.21 秒**，
  driver 213.438867441 秒，3133 Python 保持。四项失败为上述既有前向
  解包不匹配；原失败日志、JUnit 和源码快照保留，不能把这批写作通过。
- 初版修复的四组治理边界 **26 PASS / 2.86 秒**，driver 3.036477921 秒；
  原真实统计→测试签名→独立评审消费者单例 **1 PASS / 51.74 秒**，
  driver 51.954043942 秒。两批各自 3133 Python 保持；测试密钥只在隔离
  测试使用，未安装或替代用户公钥与真实核验。
- 最终解包修正后的 `registration-final`：五组 **118 PASS / 217.43 秒**，
  driver 217.663443055 秒，3133 Python 前后相同。原 4 个有效窗口失败和
  4 个既有前向接口失败均经过实际生产路径验证；未删测试或减断言。

生产模块原完整 Ruff 为 19 E702、14 E701；最终修复保留相同 33 项，原统计
回归文件父／当前各 39 项，逐诊断原语句核对无新增。新窗口回归完整 Ruff
通过；最终全 E9/F821 通过。原服务图 `--check`（20 域／52 边／零环）、
卫生、diff 和三个新文档链接检查通过。提交前须执行实际原钩子；不将这些
局部检查表述为完整发布验收。1531 个未改运行模块与 Main 完整字节核对一致，
两份待替换文件也与父提交原像一致；Main 选择整合与实际验证另行记录。

## 范围与剩余交付

实际原账本整合回归使用明确标记的合成训练／验证价格。OOS 只有窗口声明，
没有伪造其行情、收益或独立保管记录。统计 PASS 只评价该训练／验证样本，
核心 OOS 状态仍是 `UNKNOWN`；报告仍保留 `NO_INDEPENDENT_OOS_PROOF`。
经济、晋级和下单权限保持关闭。

这是通用技术原生评价核心的消费者修复。Gold 前向评价设计的独立 OOS
适配器、真实预先封存与访问记录、独立来源／结果／时间核验仍需完成。
当前 30 天前向采集的声明不回填 OOS，也不修改已锁定边界。

父 df3 自身完整 ARM64→canonical ALL→保留仍在独立冻结源码上运行；
本增量不能借用其完整验收结论。最终版本仍需自身完整发布门。
四组固定交付范围、五秒链和实际 24 小时要求保持；部署、现场全 writer
同 cut 现金恢复及获准 Demo→现金反馈→再研究仍待相应实际授权和证据。

## 冻结与主工作区整合

独立提交 `73efd7c78ad851c5395b3a234d4c7d03ffe46677` 仅上述四路径，冻结源码
3133 Python 与最终回归快照完全一致。原钩子为 664，已显式用验证解释器 PATH
执行 `sh scripts/git-hooks/pre-commit`，790 文件／零违规通过后提交；无 chmod
或 `--no-verify`。首次提交因新检出作者配置缺失返回 128；后续仅单次命令
沿用既有 Codex/codex@localhost，不修改全局配置。

备份后只选择四路径整合到 Main。排除 node_modules 后 Python 7999→8000，
含既有 Node 依赖成员时为 8000→8001；只有两份 Python 改动和一份新增测试。
Main 实际窗口／原账本／前向／迟到事实 **21 PASS / 17.75 秒**，driver
17.984838578 秒，8000 非 Node Python 前后保持。HEAD `913325cc17db40dd4ffe3698d1f0702747b69f8c`
和 index SHA `95b9d9f1d4ed1c45a2d5657324dd8e09243611ea802cf3a4a2bc06be86c340b5`
保持，不暂存或提交 Main。独立 73efd 源码完整发布尚未启动。
