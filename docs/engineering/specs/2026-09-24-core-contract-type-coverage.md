# R5核心合同类型检查扩展

等待操作员启动真实备份/隔离演练期间，推进不依赖受保护账户数据的R5工作。
canonical已有python-quality:mypy阶段从14个文件扩展到19个，新增成本、订单
状态、风险决策、强身份及确定性哈希合同。沿用既有阶段，不增加32阶段计数。

五模块启用与analytics一致的逐模块严格选项，拒绝未注解函数、未参数化泛型及
隐式Any返回等问题；不全局放宽类型规则。风险决策序列化方法补成dict[str, object]，
订单状态异常构造方法标注None返回。计算、状态转移、风险校验和序列化内容不变。

新增回归使用真实canonical命令在临时包中注入无类型风险合同函数：原门禁漏报，
修复后拒绝；补全类型后通过。合同、成本、哈希和类型门禁联合76项通过。
实际Ruff/mypy两阶段通过，mypy覆盖19文件。证据在
/home/kerstker/quantos-dev-output/maintenance-rehearsal-20260924的
contracts-types-before.log、contracts-types-after.log、contracts-types.log/json。

这是原开发工作树增量，不属于此前固定15c2803ac管理员包，也不继承8f9b00cda的
5087项通过或a99完整发布结论。原管理员命令和包摘要不变，现场仍未归档。
R5更广类型覆盖和其余路线验收不因本增量自动完成。
