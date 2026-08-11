# 远端仓库搭建 + 分支保护手册(GitHub)

> 这些步骤需要**你的 GitHub 账号/凭证**,我(AI)无法替你执行——我能改的都在仓库里改好了。
> 按顺序照做即可。命令在仓库根目录运行(`H:\code\QUANTOS`)。

---

## 0. 前置

- 已装 `git`;推荐装 GitHub CLI `gh`(`gh auth login` 登录一次)。
- 仓库当前是**私有研究项目**:务必建成 **Private**(私有)仓库。
- CI 完全离线、不需要任何 secret;`key/` 已 gitignore,不会被推上去。

## 1. 提交我刚加的机构级文件

```bash
git status                 # 确认要提交的新文件
python scripts/validate.py # 门禁绿了再提交
```

(我会在交接前把这些文件提交好;若还没提交,用一条中文提交信息提交即可。)

## 2. 建私有远端并推送

**方式 A — 用 gh(推荐,一条命令建库 + 推送):**

```bash
gh repo create QUANTOS --private --source=. --remote=origin --push
```

**方式 B — 网页建空库后手动推:**

1. 在 GitHub 网页新建一个 **Private** 空仓库(不要勾 README/.gitignore)。
2. 关联并推送:

```bash
git remote add origin https://github.com/<你的用户名>/QUANTOS.git
git push -u origin main
git push origin master      # 可选:把 master 也推上去
```

> 注意:`main` 才是受保护主干。保持 `main == master`:`git branch -f main master` 后再 `git push origin main`。

## 3. 核对 CODEOWNERS

`.github/CODEOWNERS` 当前使用 `@kerstker`。仓库转移所有者或团队后，必须在同一变更中更新全部 owner，
否则安全关键路径不会自动请求正确评审。

## 4. 开启分支保护(让红线变成"硬规则")

**方式 A — 网页:** 仓库 → Settings → Branches → Add branch ruleset / protection rule,目标 `main`:

- ✅ Require a pull request before merging(至少 1 个 Approval)
- ✅ Require status checks to pass before merging → 勾选这三个检查:
  - `backend gate (governance scanner + all test suites)`
  - `backend coverage regression floor`
  - `frontend (typecheck + build + test)`
- ✅ Require branches to be up to date before merging
- ✅ Require conversation resolution before merging
- (建议)✅ Do not allow bypassing the above settings / 禁止直接 push 到 `main`

**方式 B — gh 命令:**

```bash
gh api -X PUT repos/<你的用户名>/QUANTOS/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f "required_status_checks[strict]=true" \
  -f "required_status_checks[contexts][]=backend gate (governance scanner + all test suites)" \
  -f "required_status_checks[contexts][]=backend coverage regression floor" \
  -f "required_status_checks[contexts][]=frontend (typecheck + build + test)" \
  -f "enforce_admins=true" \
  -f "required_pull_request_reviews[required_approving_review_count]=1" \
  -f "restrictions=" 
```

> 状态检查的名字必须和 CI 里 job 的 `name:` 完全一致(见 `.github/workflows/validate.yml`)。
> 第一次 PR 跑过一次 CI 后,这些检查名才会出现在可勾选列表里。

## 5. 装本地 git 钩子(可选但推荐)

```bash
make hooks        # 或 sh scripts/install-hooks.sh
```

提交时自动跑治理扫描 + 密钥防呆;推送前自动跑完整门禁。

## 6. 日常流程(以后)

```bash
git switch -c feat/xxx        # 开分支
# …改代码,先写测试…
python scripts/validate.py    # 门禁绿
git commit -m "feat(scope): …"
git push -u origin feat/xxx
gh pr create                  # 开 PR,等 CI + 评审,合并到 main
git branch -f main origin/main # 同步本地;必要时 git branch -f main master
```

---

### 我能做 / 不能做(边界说明)

- ✅ 我已把 CI、Dependabot、贡献/安全/负责人/模板、Makefile、本地钩子、本手册都放进仓库。
- ❌ 我**不能**:创建远端仓库、推送(需要你的凭证)、开启分支保护、翻任何实盘开关或提升就绪等级——
  这些按设计都是**人来做**的。
