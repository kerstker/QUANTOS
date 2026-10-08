# 当前现场预镜像与服务操作材料准备

承接[当前版本交付材料](2026-10-06-current-release-materials.md)和
[既定闭环范围](../plans/2026-09-28-ai-factory-closed-loop.md)。本批仅准备
三项研究服务的可审阅切换材料；没有安装、reload、stop、start 或状态写入。

## 本轮实际现场核对

2026-10-06 21:37，通过原 systemctl 用户管理器只读 show，确认
quantos-next-runner.service、quantos-next-console.service、quantos-next-web.service
均 active/running，实际工作目录与执行解释器仍绑定已安装
ffac7d9134a121d66b1492f21b08a216c0e8470c。它不是本次应用验收的 584 版本。
三项 NeedDaemonReload 均 no。Runner 实际 TimeoutStopUSec=infinity、
SendSIGKILL=no、KillMode=mixed；排空保护保持有效。

三份 fragment 和五份 drop-in 共八份原件，常规单链接、原路径、所有者与
无组／全局写检查通过；读取前后文件身份／时刻稳定，原字节及摘要保留至
外部 gold-current-service-materials-20261006/field-preimages。实际 fragment /
drop-in 路径和属性、公共配置、工作区摘要记录在 research-unit-preimages.json。
没有读取 EnvironmentFile 文件内容或实际凭据。

现有状态目录、工作区文件、本地 Qwen endpoint/model 与渲染器相符；console /
runner 五项原安全环境为 false/false/false/false/true。只对照单位文件内的
明确公共字段，不变更配置。状态目录存在且无链接；工作区常规单链接、
无全局写，摘要绑定，原组写属性不改变。

## 已有操作工具修复冻结

旧干净操作工具 cd08608e5ea5b30aa24da42a2562e4a15065481d 仍缺先前现场
已修复的权限与 Git 配置。独立克隆并复制 Main 已有三处修复：组合安装
临时 022/finally 恢复、前端公开父目录 0755、安装校验的独立 Git 环境仅
信任精确 release 路径。原 operator builder 和 c624 原安装模板保持。

原工具配既有回归复现 4 FAIL/0.31 秒；三修复后四模块 19 PASS/0.37 秒，
driver 0.565190 秒。通过当前 584 应用的原证据／前端消费者导入工具，再
19 PASS/0.24 秒，driver 0.414562 秒。原失败／通过日志保留；所有服务管理、
root 安装及安装校验均为测试替身，没有接触实际 root 安装或服务状态。

原 pre-commit 用明确 Python 3.12 PATH 通过，治理零违规。本地辅助器冻结
8b4bbbab5ddd03c7846cf91aae6570d3737f044d，codex/reviewed-research-operator-20261006，
七路径（三个辅助器、三个有实际差异的测试、一记录）；第四测试与原字节
相同、不产生提交差异。三份安装工具与上一批 35eeafbb 的修复工具完全相同。
这只是操作装配工具提交，不能替代 584 自身完整验收或现场安装。

## 操作材料准入与后续行为

prepare-after-materials.py 完整 Ruff 通过，当前在途打包结果拒绝检查通过，
没有创建 next-service-delivery、operator-delivery 或 operation。实际 session
73566 于 21:44 启动，只绑定上一批材料任务 PID 2655353/start_ticks=37452899；
自身 PID/start_ticks 另行记录。上游失败或缺完成收据则停止，不自动重跑。

只有上游 MATCHING_SOURCE_FRONTEND_AND_INSTALL_BYTES_PREPARED、原 source
anchor=584、对应 artifact 的完整证据与前端合同核验通过后，才重新读取
实际八份单位文件的固定路径／摘要及工作区摘要，确认无待 reload。新路径、
删改、未知 drop-in、工作区变化均拒绝。再用冻结原渲染器生成仅三份 release
drop-in 和绑定计划；既有八份原件以明确外部绝对路径引用，可逐项核对。
调用原 operator.prepare 后只执行默认无 --apply 的操作工具，要求
REVIEWED_NOT_APPLIED；没有启动安装、切换或创建运行 operation。

计划保留原状态、工作区、网络地址和 EnvironmentFiles，复用原排空保护与
前进恢复流程。实际应用须用户审阅并明确授权这一版安装及三服务切换，
随后普通用户完整安装核验、旧 runner PID/cgroup/锁排空及健康检查仍需
实际证明。准备任务不是部署许可或现场恢复验收。

2026-10-06 21:45:40 原完整验收与两级材料任务实查仍在途；584 冷构建 A
仍下载固定构建依赖，未形成完整门、匹配材料或操作包终态。Windows 原
CheckOnly 已有用户成功输出，公开 JSON 尚待交回；真实 trust、独立来源／
结果／时间、预锁 OOS、现场现金同 cut 恢复及获准 Demo 五秒链／实际 24
小时仍未完成。完整目标保持 ACTIVE，不改变交易或经济准入。

21:48 上游完整门以冷构建 3600 秒超时失败，原匹配／服务任务随后退出 1，
未生成 operator 或 operation。原失败保持。经过实际下载诊断、签名与 solver
复核后，[公开路径修复记录](2026-10-06-cold-build-download-route.md)续接原
584 同版验收 52333、匹配材料 22844 与服务材料 47517；冻结工具不改，
八份实际配置再次核对。新任务仍待完整成功，不能借用上一轮状态称准备完成。
