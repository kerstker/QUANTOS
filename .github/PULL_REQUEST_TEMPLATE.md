<!-- 标题用中文,格式 type(scope): 摘要 ,如 feat(research/alpha): … -->

## 这个 PR 做了什么 / 为什么

<!-- 一句话说清意图,以及为什么需要它 -->

## 改动范围

<!-- 列出主要文件/模块;契约类改动请说明 TS 类型与契约测试是否同改 -->

## 自查清单(逐项打勾)

- [ ] **tests-first**:新行为先有失败测试,再实现;未隐藏/删除任何失败测试
- [ ] **门禁绿**:本地 `python scripts/validate.py` 全部通过
- [ ] **前端**(如改动):`npm run typecheck && npm run build && npm test` 通过
- [ ] **安全红线未破**:实盘开关恒关、纯 stdlib、确定性、失败关闭、网络只在 `tools/`、未提交任何密钥、未引入券商/CTP/FIX SDK、未绕过风控门、无前视/未来数据
- [ ] **容器化未破**:非人类 actor 仍不可达 APPROVED;评估仍走未改动的 `ResearchAgentLoop`
- [ ] **契约两侧同改**(如改了 console API 返回结构):TS `*Payload` 类型 + `test_console_api_contract.py` 同提交
- [ ] **最小安全改动**:范围聚焦,无夹带无关重构
- [ ] 提交信息中文,结尾保留 `Co-Authored-By` 署名行

## 备注 / 风险 / 后续待办

<!-- 可选:已知限制、留下的"口子"、需要人工决策的点 -->
