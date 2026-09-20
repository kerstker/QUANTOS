"""兼容转发 —— 实现已下沉到 :mod:`quantos.analytics.cluster`。

2026-09-20 审计 P1-9:``cluster_alphas`` 是 complete-linkage 凝聚聚类的纯实现,
只依赖标准库与 ``quantos.contracts``,与 alpha 业务无关。它被风险层
(``services/risk/research_risk/domain/gate.py``)与研究工作流
(``services/research/factor_decomp.py``)使用,留在 alpha 包里就迫使这两层
反向依赖 alpha。

本模块只做**转出**(re-export),``cluster_alphas`` 的名称与签名保持不变。
新代码应当直接导入 :mod:`quantos.analytics.cluster`。
"""

from quantos.analytics.cluster import cluster_alphas  # noqa: F401

__all__ = ["cluster_alphas"]
