# Gold 完整发布检查的五项边界修复

状态：独立增量，父提交 `91ddd2bcfa5e4526876bdf6269c2de91d8cd0785`；
冻结后完整专项检查和主区整合另行记录。范围沿用
[既定 F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)，不新增产品能力。

## 原失败与行为

fe246e4c 的自身完整发布检查后端结束为5 FAIL / 10398 PASS / 19 SKIP，
3540.75秒。原完整流程仍继续其他阶段，不能称为发布通过。五项在本增量
父版重新运行，5 FAIL / 13 PASS、1.35秒（driver1.763322秒），源码保持。

- health 测试遗漏已实现的 `registered_gold_chunked` 和恢复入口；精确
  清单同步，生产白名单、认证及隔离未变。
- console 路由清单遗漏已实现的两条 registered-review GET 接口；仍
  要求原 router 的完整路径集合、GET-only 和组件大小边界。
- 纯 JSON/离线制品读者通过 `quantos.contracts` 的 eager imports
  间接要求 Pydantic。包入口延迟加载单一原域模块，保留原100个可访问
  导出、97项 `__all__` 及静态类型导入；实际 SafetyConfig、异常类和
  PaperOrderSide 仍为直接模块中的同一对象，默认交易门不变。
- 公共采集器精确纯合同例外补入既有 collection-budget 的
  `for_duration`，不允许包级导入、其他模块、星号或额外方法；原 window
  例外保持。预算合同另检查只使用标准库 deepcopy 和纯数据操作。
- alpha trial 接线读者沿实际固定的 family-schema → neutral-registry
  两级精确重导出核验原函数。缺文件、错模块、错 alias、改 config_hash
  绑定或重绑定均拒绝，两个实际源码路径进入证据；原事务/append-only/
  portable snapshot 等检查保持，不将文本路径存在当作经济权威。

## 验证与冻结顺序

新增纯导入、精确预算及二级哈希读取先4 FAIL / 4 PASS；重绑定反例先
1 FAIL / 5 PASS，全部日志留存。五项及新回归首轮25 PASS / 1 FAIL，
唯一失败为测试遗漏既有 `maximum_bytes` 必填参数，已修正测试调用，
生产预算不改。

首轮完整专项1044 PASS / 4 FAIL，63.46秒（driver64.044786秒），3120
Python前后保持。一项为新增纯操作名单漏 `set`；另外三项是原 canonical
源码门对未冻结 `quantos/contracts/__init__.py` 的正确拒绝。先完成实际
hook与干净提交，再在冻结源码上重复原完整集合；不跳过三项或放宽源码门。

重导出的正常引用与重新绑定分别核验：普通别名引用先1 FAIL / 6 PASS，
修正仅检查赋值目标，正常引用允许，替换原函数仍拒绝。实际父版与增量
两个独立进程比较100个可访问导出、97项 `__all__`，类型、模块、函数名
和常量值一致；初始外部比对器未处理原 SafetyConfig 默认实例的错误
另行保留，修正比对器不改生产模型。

证据目录：`gold-project-closure-20261003-3pr5mrly/`
`gold-release-boundary-fixes-20261005`。完整发布检查必须属于新冻结版，
不借父版ARM64或专项检查结果。该增量不修改持久记录/签名/费用/OOS，
不部署、切服务、安装实际trust、读取凭据或发单。
