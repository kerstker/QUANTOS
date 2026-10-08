# BTC 机制研究复现说明

主报告：[金融研究工作论文](../../reports/2026-09-06-btc-mechanism-academic-report.md)。

- [来源卡](sources.json)：10 条原始研究/数据来源、阅读范围与访问限制。
- [机制卡](hypotheses.json)：7 个研究材料条目，非已批准策略。
- [冻结计划](../../engineering/plans/2026-09-06-btc-mechanism-study.md)：模型、时钟、训练与比较规格。
- [唯一实验驱动](../../../tools/btc_mechanism_study.py)：离线预测，无订单和账户账本。
- [测试](../../../tests/tools/test_btc_mechanism_study.py)：因果时序和数学边界。

## 原始研究输出

DGX 输出目录：`/home/kerstker/quantos-dev-output/btc-mechanism-study-20260906`。

`results/1/` 与 `results/2/` 各有 `predictions.csv`、`result.json`、`input-snapshot.json`；`results/trials.db` 保存全部状态。根目录 `verify.py` 独立重估预测及统计，`independent-verification.json` 保存结果；`oi-feasibility.json` 与两个 metrics ZIP 保存数据可行性证据。原始失败测试及成功验证日志保留。

研究包：`/home/kerstker/quantos-dev-output/btc-mechanism-study-20260906/QUANTOS-BTC-mechanism-reproducibility.tar.gz`。包含报告、来源/机制卡、冻结计划、实验脚本与测试快照、独立验证器、两次输出、日志、原始公共 trade ZIP/元数据及 funding capture、OI样本和 SHA256 清单。没有账户凭证或私人数据。无需重新抓取行情即可核对本批数值；若另行下载，使用每个 ZIP 的 `.zip.json` 内官方 URL 和 CHECKSUM，内容漂移则拒绝当成原批次。

## 环境与代码身份

依赖项目代码，材料包不是另一个量化引擎。需要 QUANTOS 基础提交及研究提交 `5704fc48c7f71152fe7e38fb71e6791cb26bd816`；本机已存在。包内 `source-increment.bundle` 仅携带相对 `9766434b370f8deac576937a4abbc2d3941bd677` 的增量，不能替代整个仓库。可在已有基础提交的仓库导入，再创建独立工作树：

```bash
git fetch /absolute/path/btc-mechanism-repro/source-increment.bundle refs/heads/codex/btc-mechanism-study-20260906
git worktree add --detach /absolute/path/quantos-btc-study 5704fc48c7f71152fe7e38fb71e6791cb26bd816
```

使用研究提交的 `requirements-ci-linux-aarch64.lock` 和 Python 3.12.3 / aarch64；本机验证环境：
`/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python`。不要求修改常驻服务依赖。跨平台浮点数只应按明确容差核对，不承诺跨 BLAS/CPU 字节一致。

## 运行

在对应项目根目录执行，`--output` 使用新的研究目录，禁止覆盖原试验：

```bash
PYTHONPATH=. OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python \
  tools/btc_mechanism_study.py \
  --input /absolute/path/btc-mechanism-repro/input \
  --output /absolute/path/btc-study-new/results --attempt 1
```

同一目录重放使用 `--attempt 2`。每次新计算追加四条试验，不是新候选，不可把重跑当独立样本。结果身份含 attempt、commit，故整个 JSON 摘要可以不同；相同源码/输入的 `predictions.csv` 应逐字节相同，`fits` 和 `results` 应相同。

输入目录最小形状：`klines/BTCUSDC-1m-*.zip`、对应 `.zip.json`、`funding-capture.json`。数据解析复用 QUANTOS 官方归档及捕获校验器；不读取钱包、交易所私有 API 或生产状态。

## 独立核验

材料包解压后，先核对包内文件，再对已包含的两次输出运行验证器：

```bash
cd /absolute/path/btc-mechanism-repro
sha256sum -c SHA256SUMS
OPENBLAS_NUM_THREADS=1 \
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python verify.py
```

验证器使用自身所在目录的 `results/{1,2}`，不会运行交易；输出/更新根目录 `independent-verification.json`。核验 SHA 清单应在修改或新生成文件之前运行。

已验证：四模型两次预测字节一致、2,920 个独立预测重建、12 组统计、8 个已完成试验；误差容差1e-12。验证器重估使用导出的特征面板，不独立认证交易所原数据真实性。特征未来追加与未成熟标签的性质由项目测试覆盖。完整复现应同时检查原始文件 SHA 和项目测试，不能只看最终汇总。

## 结果解释

`PASSED` 表示计算成功，不表示候选通过。所有结果 `authoritative=false`、`account_pnl_computed=false`。本轮没有策略净收益、Paper、实盘或全系统金融正确性结论。FLOW 的0.943%是相对价格基线的MSE改善，不是账户收益率。
