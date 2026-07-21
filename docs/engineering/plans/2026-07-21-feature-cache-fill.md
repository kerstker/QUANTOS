# Rank #15 最小实施计划：PIT 特征缓存

1. 复用现有 FX PIT bundle 和三条已注册特征，不引入网络源或新的因子逻辑。
2. 用批量 SQLite 写入完成可重复物化；写入前后都使用 bitemporal `as_of` 边界验证。
3. 输出可审计摘要，包含来源 manifest、行数、覆盖范围、泄漏结论和内容哈希。
4. 让只读数据目录在缓存存在时读取真实特征摘要；缓存缺失时仍 fail-closed 到既有 synthetic fallback。
5. 将证据检查接入 `scripts/validate.py`，补齐持久层、驱动器、治理边界和 Console 读取测试。

本计划不改变 L2 交易安全边界，也不把缓存诊断升级为自动晋级或交易门禁。
