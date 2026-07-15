# 贡献指南(QUANTOS)

> 本项目是**安全关键的 AI 量化研究平台**,就绪等级 **L2_DRY_RUN_ONLY**(只研究 + 干跑模拟)。
> 所有改动都必须守住下面的安全红线,并通过单一门禁。叙述用中文,代码标识符用英文。

## 1. 安全红线(不可让 —— 机器强制)

- `LIVE_TRADING_ENABLED=false`、`REAL_BROKER_ENABLED=false`、`ALLOW_ORDER_SUBMISSION=false`、
  `ALLOW_AI_DIRECT_TRADING=false`、`REQUIRE_HUMAN_APPROVAL=true`。实盘必须在没有人工多步审批时**不可能发生**。
- **纯 Python 标准库**:不引入 numpy/scipy/torch/pandas/ccxt 等三方依赖。
- **确定性**:不用随机数、不读墙钟时间;前端新代码不用 `Date.now` / `Math.random`。
- **失败关闭**:异常一律保守拒绝,绝不"出错就放行"。
- **网络只在门禁外**:网络访问只允许出现在 `tools/`(门禁不跑);`services/` 整棵树**零网络 import**。
- **不提交任何密钥/凭证**;不引入券商/CTP/FIX SDK;不绕过或削弱风控门;不使用未来数据(无前视)。
- **容器化**:非人类 AGENT actor 永不可达 `APPROVED`;一切评估走未改动的 `ResearchAgentLoop`。

违反以上任意一条,治理扫描器(governance scanner)与 `tests/governance/` 会让门禁直接红掉。

## 2. 开发流程

1. **先写测试(tests-first)**:新行为先有失败测试,再实现。
2. **最小安全改动**:一次只做一件事;绝不隐藏或删除失败的测试。
3. **本地跑门禁**(绿了才提交):

   ```bash
   python scripts/validate.py          # 治理扫描 + 全部 pytest(contracts/governance/research/trading)
   ```

   隔离单跑某个测试时,从仓库根设 `PYTHONPATH=.`(Windows Git Bash:`PYTHONPATH=/h/code/QUANTOS`)。
4. **前端**(改了 `apps/research-console` 才需要):

   ```bash
   cd apps/research-console && npm ci && npm run typecheck && npm run build && npm test
   ```
5. **契约两侧同改**:改 console API 的返回结构,必须同时改 TS 的 `*Payload` 类型与
   `tests/research/test_console_api_contract.py`,在同一个提交里。

## 3. 分支与提交

- 主干:`main`(受保护)。`master` 与 `main` 保持同步(`git branch -f main master`)。
- 开新分支干活:`feat/xxx`、`fix/xxx`、`docs/xxx`、`chore/xxx`。
- 提交信息:**中文**,采用 `type(scope): 摘要` 前缀(`feat` / `fix` / `docs` / `test` / `refactor` / `chore`)。
- 每条提交结尾保留协作署名行:

  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

## 4. 本地 git 钩子(可选但推荐)

```bash
make hooks            # 或:sh scripts/install-hooks.sh
```

装好后:`pre-commit` 跑治理扫描 + 密钥防呆;`pre-push` 跑完整 `validate.py`。
确属误报时可 `git commit --no-verify`,但需人工复核后再用。

## 5. Pull Request

- PR 必须通过 CI 的 `validate`(后端门禁 + 前端)两个检查,并至少 1 人评审通过才可合并到 `main`。
- 按 PR 模板逐项自查(安全红线、tests-first、门禁绿、无密钥、契约两侧同改)。
- 大改动(新子系统/破坏性变更)先开 issue 或设计文档(`docs/engineering/specs/`)对齐再动手。

## 6. 常用命令速查

```bash
make gate            # 完整门禁
make governance      # 只跑治理扫描
make frontend        # 前端 typecheck + build + test
make api             # 起只读 console API(:8400)
make hooks           # 安装本地 git 钩子
```
