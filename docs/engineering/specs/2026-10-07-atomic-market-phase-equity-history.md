# 原子市场阶段权益记录

状态：修复已冻结、专项和原件冷消费已验证并整合至开发工作树；未完整发布验收或部署。
承接[止损历史观察记录](2026-10-07-gold-loss-history-observation.md)，
范围保持原 V2 账本、五个百分点初始权益止损和[F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)。

## 缺陷与修复

原模拟账户在处理整个市场帧后才记录一次 MARKET 权益。同一帧先结算资金费、
再更新价格时，资金费造成的越线可能被后续价格回升覆盖，Gold 控制器无法读到该损失。
有效反例沿用原数量和风险约束：初始权益100、实际成交0.1、合成手续费率0.45、
资金费率0.1，入场后95.5，资金费阶段94.5，同帧价格100→115后96。
高费率是明确的合成边界输入，不是实际账户费率或策略盈利证据。

`LinearPerpetualLedger.process_events` 增加可选阶段观察器，在每个完整 funding/mark
事务和账户级强平检查提交后调用。跨标的同阶段仍整体处理，不逐标的触发误判。
观察器不能回滚已发生现金流；记录失败传播异常，由原模拟会话拒绝发布部分结果。
无效观察器在任何事件变更之前拒绝，失败原子组不产生观察记录。

模拟账户保留 FUNDING/MARK 阶段权益，原 INITIAL/MARKET/FILL 记录继续存在。
这是账本历史记录，不新增策略调用或交易权限；资金费回执未可用期间原暂停规则保持。
实际控制器在允许的回调中读取越线并沿原订单引擎减仓，反例终态为 STOPPED_FLAT，
费用9.675、价格损益1.5、资金费-1、期末90.825、对账残差0。

`ATOMIC_MARKET_PHASE_EQUITY_V1` 同时写入输入身份和原生结果，改变权益历史的版本
不能复用旧结果哈希。父源码同输入结果和检查点实际留存于 legacy_reference.json；
两个旧哈希断言先实际失败，再改为验证新确定性向量、现金不变及父版检查点拒绝。
旧结果没有被重标或升级。控制台载荷结构保持，原件材料消费已核对。

## 证据与未通过项

独立分支 codex/gold-atomic-phase-loss-history-20261007，冻结提交
`3c7c9a2084ef28a0aada0a48d21ba7f23dd22485`，父版
`227dd0663c8e471ac44186ae625f9796db0c652b`。
3143份 Python 内容摘要 b450cf1560f857884829d45a669a69ec0571d9a26cc44c9687bfeb6aca97e34f。
实际 hook、全 E9/F821 和原依赖图检查通过，工作树干净；图结构未变。

输出目录为 gold-project-closure-20261003-3pr5mrly/gold-atomic-phase-loss-history-20261007：

- red_valid：父版4 FAIL，包含实际成交、实际资金费后未触发的反例。早期错误夹具
  red/red_corrected/first_green 原样保留，不用其失败证明本缺陷。
- expanded：423项选择的部分输出有两个旧哈希失败，未生成终态/JUnit；原进程和
  句柄已不存在，记 INCOMPLETE，不称等待或通过。两项随后单独复现2 FAIL。
- phase_and_restore：209 PASS，29.40秒；批次、回执时序、账本、会话恢复和风险守卫。
- frozen_consumers：25 PASS，66.39秒；含原来源导出、完整分块请求、统计签名消费者
  和冷注册库，测试签名仅为隔离输入。
- main-integrated：172 PASS、1 SKIP、1 FAIL、2 ERROR，81.10秒。三项拒绝均为
  RESEARCH_EVIDENCE_FILE_CUSTODY；Main现有源码含1326个不满足原非组写要求的文件。
  保留原权限和拒绝，不放宽读者。相同三项在受控冻结副本 frozen_http_contract
  为3 PASS，59.17秒；这不把 Main 失败改写为通过。

整合前逐路径核对 Main 与父像，写前保存四个既有文件，新测试使用0644。
仅五个 Python 路径变化；Main其余 Python、HEAD和index经重建前像摘要核对保持。
整合后8010份 Python，摘要9e57a2de96de5d92504bc4643649db9797032d198e757cfb426982aca812b4e0。
Main不提交或暂存现有用户改动。

本轮没有执行新版完整 ALL 门。旧bf0完整门已终结为30 PASS/2 TIMEOUT，
两级匹配材料分别拒绝 FULL_PIPELINE_NOT_PASS/MATCHING_MATERIALS_NOT_COMPLETE。
后台诊断进程已不存在且未生成 backend-process/exit；辅助器仍引用不存在的
pipeline-exit.json及旧结果字段 name，后续启动前须按真实产物合同修正，不能重启旧门。
原长期公共采集PID也已不存在，日志有WebSocket代理协议异常，未生成完整结果；
具体终止根因未证明，不重新启动或回填原窗口。

完整九项风险、独立数学复算、风险输入来源、锁定OOS、候选准入和现场闭环仍未完成。
所有真实经济与提交权限保持原边界。按用户要求，本轮后先完成[全系统交付分析](../plans/2026-10-07-system-delivery-gap-audit.md)。
