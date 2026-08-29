"""育种谱系接线(P3-4,块B):cs_genome 育种子代经 alpha_factory→alpha_pipeline→make_child_asset 记
parent_ids/mutation_op/perf_delta。诚实边界:gp/evolutionary 路径不构造 AlphaAsset、无挂载点,本子项不接。"""
import math

import services.research.alpha_expression as ax
from services.research.alpha_factory import run_alpha_factory as _run_alpha_factory


def run_alpha_factory(*args, **kwargs):
    kwargs["diagnostic_only"] = True
    return _run_alpha_factory(*args, **kwargs)

FIELDS = ["close", "funding"]
WINDOWS = [3, 5, 7, 14]


def _panel():
    T, N = 80, 8
    base = [0.001 * (i - 3.5) for i in range(N)]
    close = [[100.0 + 0.5 * math.sin(0.3 * t + i) for i in range(N)] for t in range(T)]
    funding = [base[:] for _ in range(T)]
    return ax.FieldPanel([f"C{i}" for i in range(N)], list(range(T)),
                         {"close": close, "funding": funding})


def _run():
    return run_alpha_factory(_panel(), FIELDS, WINDOWS, generations=3, population_size=14, elite_k=6,
                             dataset_version="dv", timestamp=1, ephemeral=True)


def test_factory_records_bred_lineage():
    reg = _run()["pipeline"]["registry"]
    assets = reg.all_latest()
    bred = [a for a in assets if a.lineage.parent_ids]
    assert bred, "no bred child recorded lineage (breeding path not wired)"
    assert all(a.lineage.mutation_op in ("mutate", "crossover") for a in bred)
    # perf_delta 尽力而为:in-scope 父代能算则 float,否则 None(fail-safe 契约)
    assert all(a.lineage.perf_delta is None or isinstance(a.lineage.perf_delta, float) for a in bred)
    # 谱系可查询:某 bred 子代的 in-best 父代 -> children_of 含它
    for a in bred:
        for pid in a.lineage.parent_ids:
            if reg.get(pid) is not None:
                assert a.alpha_id in {c.alpha_id for c in reg.children_of(pid)}


def test_factory_lineage_deterministic():
    def _lin():
        reg = _run()["pipeline"]["registry"]
        return sorted((a.alpha_id, a.lineage.parent_ids, a.lineage.mutation_op) for a in reg.all_latest())
    assert _lin() == _lin()      # cs_genome 哈希种子,育种谱系确定性未破
