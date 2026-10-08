# 闭环功能候选版本

## 整合候选续验（a232ff35d）

原e29首个全量pytest阶段已终态：9538通过、3失败、19skip，分别为SQLite拥有者、旧依赖图、未刷新供给链。后续工具/前端仍由原进程运行，旧记录不改。
周期模块只读连接已迁回FactoryStore，未扩展允许名单；与周期/队列/记忆联合21项通过、正确依赖图复核及真实pre-commit通过。
最新源码a232ff35d2e9e710a4d2fd04f3a291d9a83b82e6另冻于factory-integrated-release-20260929-mvo06rvk/source，包含记忆、失败展示及上述修复。
实际验收16263（父PID2851668）先通过依赖图/存储边界检查，再进入全新ARM64捕获；不复用e29的构建证明。
源码交付72817与前端交付87347已绑定真实父进程PID/启动时钟和原helper摘要，只有新版完整验收成功才打包；服务材料生成器只作准备，未应用。
旧e29的验收及等待器仍按其各自终态退出，不把新版状态覆盖到旧目录；目前无完整验收通过或现场升级结论。

最新验收观察：e29cbb6在service-import-graph:current检测到旧依赖图，后续派生证据未能刷新；全量测试也出现失败，原进程仍在运行，不能交付。新增模块的图需作为源码候选内容预先冻结，已在包含后续记忆/采集展示的独立目录生成正确版本，依赖图9项检查及实际pre-commit通过。[修复说明](2026-09-29-factory-import-graph-refresh.md)。未修改e29在途源码/证据，未重启或伪装原验收结果。

将采集输入装配、一次请求到研究计划、有限周期新数据批次、核验更新、执行反馈投影及前端逐轮流程固定为独立候选。基于已验收产物 ebd39892468c0d5eba3c625557630d4e24535470，仅收录本次功能53文件，不包含尚未完成的反馈维护升级实现或旧目录生成证据。原工作区及其运行中的真实研究保持不变。

- 候选源码：e29cbb6c9b8c53a42dd018fe6926e268238ae084。
- 独立目录：`quantos-dev-output/factory-functional-release-20260929-qfdc9s97/source`。
- 联合后端190通过、1跳过；前端14通过；类型检查、正式E9/F821、真实pre-commit通过，提交后工作树干净。
- 依赖由已验收目录独立复制，package与lock逐字节一致。
- 原ARM64采集→canonical ALL→证据留存流程已启动，固定绑定本次提交。未取得完整验收结果，不能借用旧候选的32/32。

运行脚本和阶段日志位于候选父目录。该流程不安装或切换服务。真实周期研究仍在独立 gold-periodic-real-20260929-44ph40wj 运行，首批NO_SIGNAL，第二批等待真实一小时冷却；该真实研究基于原工作区身份，不能冒充固定候选的运行证据。

独立经济准入、真实黄金Demo以及持续工厂交付仍未完成。

## 实际交付目标核对

只读核对发现用户当前浏览器15911入口属于用户级 quantos-next-web.service，而非系统级3100控制台。其CWD为gold-frontend-20260922/release-11；对应quantos-next-console.service使用portfolio-final-acceptance-20260918/accepted-source-2626199a6，quantos-next-runner.service使用demo-functional-acceptance-20260914/accepted-source-3b1ed3941。三个服务均active，目录名不单独充当代码身份验证。

实际GET 15911生命周期接口确认缺少gold_results、gold_inputs、collection_requests、collection_programs，scheduler_enabled=true但当前campaign为0。默认3100栈的调度未启用且没有workspace字段。故只升级默认system服务不能交付到用户当前入口；必须针对next组三服务核对源码、同源路由、原状态目录/研究空间映射及模型/调度配置，再按实际发布边界切换。未读取凭证、服务环境或配置秘密，未停止或重启任何现场服务。

候选父目录research-delivery-scope.json保存该次元数据及待交付范围，非原子快照、无apply入口。自动源码打包已经绑定实际验收进程PID和启动时钟，只有该进程结束且原验收成功，才会核对既有helper摘要并提交允许的证据、生成离线源码安装包；该流程不会安装或切换上述服务。

升级前像续查：已保存三份用户单元及现有web drop-in的文件摘要，同时记录管理器中ExecStart/ExecStartPre的不可逆摘要；不输出命令参数、不读取EnvironmentFiles。材料为research-unit-preimages.json，只证明这次观察的配置身份，不能当成未来切换时的确认。下一切换材料需绑定这组前像及验收后的新产物，保留黄金15844独立只读接口和原15840/15842路由。尚未生成或执行切换命令。

## 同版前端交付衔接

新增候选目录内 finish-frontend-delivery.py，绑定源码打包进程的PID/启动时钟并等待其真实退出；仅当源码包验证通过才在其独立verification-clone中构建前端。该步骤核对源码锚、原证据清单及依赖锁，使用原前端源码摘要、构建合同、runtime合同和完整树摘要校验器，再由原网关真实后端合同检查确认同源且mock关闭。生成的frontend-delivery目录与源码包共用验收产物提交；不修改已接受源码、不安装、不重启服务。

驱动已启动等待，尚未生成前端交付通过结论。此前开发目录浏览器截图与本次待生成的固定提交前端产物分别记录，不能相互替代。冷构建只读诊断确认容器内ninja/cc/cc1plus进程活动，当前处于原生科学依赖编译，未因日志暂时不更新而重新启动。


ARM64阶段已实际退出0，双冷构建、原报告校验及发布完成，原canonical ALL已启动（进程2770297）。尚无ALL最终结论，仍未部署。

## 现有研究状态兼容性实测

对实际 next-console 的研究空间配置进行了只读核对。当前可写根为
`quantos-dev-output/research-runtime-244dd6fbe/state`，没有 factory.db；五个历史空间
natural-20260911、natural-previous、original-factory、qwen-comparison、sleeves-comparison
均存在研究库，仍应保持只读展示。不能将历史自然窗口计划迁移为当前空间自动运行任务。

候选目录 check-existing-research.py 使用 SQLite mode=ro 连接逐库备份，只在任务目录副本中调用
新版 FactoryStore 初始化。五库所有原表的行数/内容摘要及研究 snapshot 均保持一致，
integrity_check 通过。新版 lifecycle_payload 对全部六个真实空间只读投影无错误，
四个新增合同字段存在；历史模型数量依次为7、7、8、1、1，当前空间为0。
材料位于 `factory-functional-release-20260929-qfdc9s97/existing-research-prjhjb6x/summary.json`。

这验证现有工厂库升级兼容与只读结果展示，不是研究 Runner 切换或现场端到端验收。
跨空间读取非原子，不读取凭证、不启动后台、不修改原数据库；没有把历史研究转为经济资格。
正式切换仍须保持原工作空间配置、当前状态根、模型端点与黄金15844路由；待绑定最终验收产物。

同版服务材料生成器prepare-next-service-delivery.py已准备于候选父目录，只有源码/前端最终交付均通过且提交匹配才可生成三份next服务drop-in与原配置留存。新配置绑定统一源码/解释器、保留原状态根/研究空间及15844路由，并在启动前核对原发布清单与明确的模型、调度和提交关闭配置。三份配置渲染及Python预检代码语法已检查；尚未对最终产物运行生成器，未安装或切换，现场恢复和执行切换流程仍需完成。
