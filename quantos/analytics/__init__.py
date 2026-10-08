"""中立的纯数值分析原语(correlation / clustering)。

2026-09-20 审计 P1-9:``pairwise_correlation`` / ``max_abs_corr_to_set`` /
``cluster_alphas`` 原本住在 ``services/alpha/`` 里,但它们是**与 alpha 无关**的
纯统计函数——只依赖标准库与 ``quantos.contracts``。把它们留在 alpha 包里制造了
一个方向性倒置的依赖:

    services/risk/research_risk/domain/gate.py -> services.alpha.cluster

风险层反过来依赖 alpha 层。结果是 import-linter 的 research→alpha 契约出现一条
经由 execution → risk 的间接违规链。把原语下沉到这个中立包后,alpha 与 risk 都
只依赖中立层,那条链消失。

这里只放**纯函数**,不放业务状态。services/alpha 下的同名模块保留为兼容转发,
以保证既有调用点与测试的导入路径不变。
"""
