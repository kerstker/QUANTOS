# GitHub 手动设置清单(人来做)

> 这份清单只列 **GitHub 网页 / 账号上要你亲手设的**——我(AI)按设计**无法替你做**:
> 我改不了远端仓库设置、开不了分支保护、动不了任何就绪等级或实盘开关。
> 仓库里能改的(CI、CODEOWNERS、模板、Makefile、钩子)我已经放好了。
>
> 远端搭建 + 推送 + 分支保护的**完整命令版手册**见
> [`docs/REMOTE_SETUP.md`](REMOTE_SETUP.md);本文件是**简短可执行清单**,只覆盖
> "纯 GitHub 上手动点"的部分,逐条照做即可。
>
> 当前远端:`https://github.com/kerstker/QUANTOS`(私有研究项目,确认建成 **Private**)。

---

## 0. 先记三条前提

- 本仓库是**安全关键的研究平台**,就绪等级 `L2_DRY_RUN_ONLY`——只做研究 + dry-run,
  **永远不接实盘、不接券商、不放真凭证**。GitHub 上的任何设置都不得削弱这条红线。
- CI 完全离线、不需要任何 secret;`key/` 已 gitignore,不会被推上去。
- 下面每一项**只能你来点**,我做不了。

---

## 1. 仓库 description(建议文案,二选一)

仓库主页 → 右上 **About** 齿轮 → **Description** 填入(英文,放 GitHub 上更通用):

- 简短版:
  `Safety-critical AI quantitative-trading RESEARCH platform — research + dry-run only (L2_DRY_RUN_ONLY). No live trading.`
- 略长版:
  `AI quant-trading research platform. L2_DRY_RUN_ONLY: research + dry-run simulation only — no live trading, no broker connectivity, no real credentials.`

> 务必让 description 里就带出 "research only / no live trading",别让外人误以为这是实盘系统。

## 2. Topics / 标签(建议)

同一个 **About** 面板 → **Topics** 加(全小写、空格用连字符):

```
quant-research  algorithmic-trading  backtesting  alpha-research
risk-management  python  fastapi  react  typescript  dry-run  research-platform
```

> 不要加 `live-trading`、`broker`、`order-execution` 这类会误导用途的标签。

## 3. README 徽章(可选,文案给好)

`README.md` 目前**没有徽章**。要加就把下面这行放在大标题下面(属主当前就是 `kerstker`,
若仓库转移再改这个名字即可):

```markdown
[![validate](https://github.com/kerstker/QUANTOS/actions/workflows/validate.yml/badge.svg)](https://github.com/kerstker/QUANTOS/actions/workflows/validate.yml)
```

- 这是 CI 工作流 `.github/workflows/validate.yml`(`name:` 就叫 `validate`)的状态徽章。
- **不建议**加 license 徽章——仓库当前**没有根目录 `LICENSE` 文件**;真要标 license,先加文件再加徽章。
- 私有库的徽章只有有权限的人能看到图,这正常。

## 4. 分支保护(把红线变成"硬规则",目标分支 `main`)

仓库 → **Settings** → **Branches** → **Add branch protection rule / ruleset**,
分支名填 `main`,勾选:

- ✅ **Require a pull request before merging**,Required approvals 设 **1**
- ✅ **Require status checks to pass before merging** → 勾这**两个**检查(名字必须**一字不差**):
  - `backend gate (governance scanner + all test suites)`
  - `frontend (typecheck + build + test)`
- ✅ **Require branches to be up to date before merging**
- ✅ **Require conversation resolution before merging**
- (建议)✅ **Do not allow bypassing the above settings**(禁止直接 push 到 `main`)

> 这两个检查名取自 CI 里两个 job 的 `name:`(见 `.github/workflows/validate.yml`)。
> **第一次 PR 跑过一次 CI 后**,这两个名字才会出现在可勾选列表里。
> 命令行版(`gh api … branches/main/protection`)见 `docs/REMOTE_SETUP.md` 第 4 节。
> 注:`.github/CODEOWNERS` 已填好属主 `@kerstker`,自动请求评审已可触发;若你把仓库
> 转给别的账号,记得把里面的 `@kerstker` 一并改成新属主(对照 `docs/REMOTE_SETUP.md` 第 3 节)。

## 5. Private 仓库提醒(务必确认)

- 在 **Settings → General → Danger Zone** 确认仓库可见性是 **Private**——
  这是私有研究项目,**不要**误转 Public。
- `key/`、`*.key`、`coinkey*`、`*.db`、`data/cache/` 已在 `.gitignore`;
  转 Public 前请先复核没有任何密钥 / 缓存数据进了历史。
- secret 只用**只读数据 key**(如环境变量 `COINGECKO_API_KEY`,或 gitignore 的 `key/coinkey.txt`),
  绝不硬编码、不提交、不进 Issue/PR 正文。

## 6. Releases / Wiki(可选)

- **Releases**:研究平台**没有发布节奏**,可不开;真要打 tag 就用语义化、并在说明里**重申
  `L2_DRY_RUN_ONLY`、非实盘**,别让 release 看起来像"可上线版本"。
- **Wiki**:建议**关闭**(Settings → General → Features 取消勾选 Wiki)——
  本仓库文档以 `docs/` 为单一事实源(`PROJECT_STATE.md`、`REMOTE_SETUP.md`、blueprint 等),
  开 Wiki 只会制造第二份会漂移的文档。

---

### 边界说明(我能做 / 不能做)

- ✅ 已放进仓库:CI(`.github/workflows/validate.yml`)、Dependabot(`.github/dependabot.yml`)、
  CODEOWNERS、Issue/PR 模板、`CONTRIBUTING.md`、`SECURITY.md`、`Makefile`、本地 git 钩子
  (`scripts/install-hooks.sh`)、以及 `docs/REMOTE_SETUP.md` / 本清单。
- ❌ **做不了**(按设计**人来做**):建/改远端仓库设置、改 description/topics、加徽章到 README、
  开启分支保护、改仓库可见性、翻任何实盘开关或提升就绪等级。
