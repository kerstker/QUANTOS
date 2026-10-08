# 黄金精确传输与预留查询接线

原 binance_demo_exact 的请求编码、请求摘要、一次性发送和原ID查询接口新增
显式批次 schema，默认保持旧BTC范围。原协调器从已保存的授权信封读取协议，
传给请求生成、发送结果解析和恢复查询；不根据订单自报symbol自动扩权。
固定Demo端点、原账户凭证接口、读取凭证后的final_check、单次网络尝试保持。

新增六项回归：五项传输接口在实现前失败；查询协调器集成先返回
QUERY_RECOVERY_REQUIRED，接线后返回ORDER_SNAPSHOTS_OBSERVED。
验证18位小数字节无float变化、POST摘要绑定固定域名/端点/请求、HMAC字节、
凭证读取后再次检查、检查拒绝时无HTTP、超时只有一次尝试，以及恢复只用
原origClientOrderId。真实原SQLite派发/OMS/恢复存储与哈希证明文件参与查询
测试，网络、凭证和源目录custody使用隔离fixture。查询FOUND只追加线观察，
OMS订单、成交和恢复记录均保持空，未把查询结果伪造为成交。

五组联合56 passed：gold_demo_exact_transport、demo_exact_transport、
demo_prepared_execution、gold_demo_dispatch、demo_position_context。
新增测试完整Ruff、两处实现E9/F821通过；治理扫描698文件零违规。

未访问真实凭证或HTTP、未部署、未运行canonical全量门。
黄金完整风险提案与issuer策略尚未接线，当前端到端发单仍不可达；
本增量验证传输/查询接口，不是独立批准、真实交易或完整OMS现金闭环。
