# 2026-10-01 发布依赖安全刷新

2026-10-01 冻结复核：本增量15文件固定为5f39dade7ce8966c82f7b58ef44023b4f6aee691，实际hook通过、Git干净。原3项源码绑定失败在该冻结副本全部通过/225.13秒；固定版本扫描76项0跳过/0已知漏洞。原联合失败记录不删。新双ARM64→完整canonical流程已启动，扫描器/基础镜像/builder预检通过、冷构建A在途；尚未完整验收或部署。独立源码中的下文“复查待办”保留为冻结时快照，本续接只更新主工作区记录，未修改正在验收的冻结源。

## 问题与范围

修复版3db6bb446809d155382dfef8049cbd59e503a819的双ARM64流程在最新漏洞扫描终结INCOMPLETE/arm64-driver_FAILED，未开始canonical ALL。其运行镜像存在4项已知漏洞：PyJWT 2.14.0的CVE-2026-101918，以及urllib3 2.7.0的CVE-2026-97687/97688/97689。6695ce404dc5a0ffec376434bcc61c6c2af21271的后继也终结PRIOR_ACCEPTANCE_NOT_PASSED，不能继续报告WAITING。

本增量基于6695ce40，保留精确策略批准的独立操作员交接。仅更新PyJWT至2.15.0、urllib3至2.8.0；来源为[PyJWT维护者公告](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-42vr-xj54-vc7v)与[urllib3 2.8.0发布说明](https://github.com/urllib3/urllib3/releases/tag/2.8.0)。同步身份评估器、原证据生成和原fixture的精确版本，不放宽准入或漏洞门。

## 实现与验证

恶意深嵌套JWT、真实身份验证器的已签名深嵌套输入、超长分块长度/尾行、Deflate结尾附加字节及HTTPS代理策略隔离共9个回归，在旧依赖上复现失败。输入均离线测试材料，无网络、账户或真实凭据。新依赖下三组安全/身份测试31项通过。TLS测试替身已同时适配旧/new库的参数与返回约定，最终旧库失败是代理SSL上下文错配，不是替身导入/调用错误。

两平台CI锁均76个制品；运行镜像锁44个，构建锁9个。在线重解析四锁通过；逐制品比较仅PyJWT与urllib3改变，构建锁制品完全不变，其余变化只是来源摘要。独立Python 3.12.3环境经哈希锁安装，发行包比较也只有上述两版本变化。

原requirements审计真实扫描74个发行包，0已知漏洞，原证据验证器通过。直接审计URL锁时pip-audit跳过76项，因此该次返回0不计扫描通过；另由已验证锁的精确版本生成清单执行固定版本审计，详见保留结果。没有使用忽略列表或降低严重度门。

联合测试初次236项有4失败：TLS测试替身签名已修正；另外3项完整源码绑定检查正确拒绝未冻结的脏工作树。所有原失败记录保留，冻结源码后必须重新运行这3项才能关闭，不能跳过或放宽绑定。实际提交hook、冻结源码和完整ARM64/canonical结果单独留存；这份实施记录本身不宣称完整验收通过。

## 开发环境与交付限制

本机输出及验证记录：`/home/kerstker/quantos-dev-output/system-security-delivery-20261001-u5b0_090`。
对应解释器：`/home/kerstker/quantos-dev-output/system-security-delivery-20261001-u5b0_090/venv/bin/python`。
原2.14.0环境及常驻服务保留；此增量不部署、不安装受保护批准配置、不下单。首次24小时黄金策略批准、未知账户事件诊断、持续Demo运行/资金对账与反馈仍未完成。

原漏洞拒绝材料：`/home/kerstker/quantos-dev-output/system-closed-loop-acceptance-20261001-1o0v9_3g/arm64/rejected-audit-bf00e7c73f1038613809dd2ee747607dc6422c8e98e7e32bacdd6faf96b57fd1.json`。旧历史收益与经济准入边界保持。
