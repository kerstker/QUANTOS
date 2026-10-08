"""兼容转发 —— 实现已下沉到 :mod:`quantos.analytics.correlation`。

2026-09-20 审计 P1-9:``pairwise_correlation`` / ``max_abs_corr_to_set`` 是与
alpha 无关的纯统计原语,长期放在 ``services/alpha/`` 只因为历史原因。风险层
(``services/risk/research_risk/domain/gate.py``)需要 ``cluster_alphas``,于是
出现了 risk → alpha 的方向性倒置依赖。

本模块只做**转出**(re-export),公开名称与签名保持不变,既有导入路径
(``from services.alpha.correlation import max_abs_corr_to_set``)继续可用。
新代码应当直接导入 :mod:`quantos.analytics.correlation`。
"""

from quantos.analytics.correlation import (  # noqa: F401
    max_abs_corr_to_set,
    pairwise_correlation,
)

__all__ = ["max_abs_corr_to_set", "pairwise_correlation"]
