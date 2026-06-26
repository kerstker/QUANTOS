---
name: Bug 报告
about: 报告一个缺陷或异常行为
title: "fix: "
labels: ["bug"]
---

## 现象

<!-- 发生了什么,期望是什么 -->

## 复现步骤

1.
2.
3.

## 门禁/测试输出

<!-- 贴 `python scripts/validate.py` 或相关测试的失败输出 -->

```
（粘贴日志）
```

## 环境

- 提交(`git rev-parse --short HEAD`):
- 操作系统 / Python 版本:

## 是否触及安全不变量?

<!-- 如果这个 bug 可能让实盘开关、容器化、风控门、密钥处理出问题,务必勾选并优先处理 -->

- [ ] 这个 bug 可能影响安全红线(若是,按 SECURITY.md 私下上报,不要公开细节)
