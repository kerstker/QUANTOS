"""Planned subsystem paths remain code-free until a real implementation lands."""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 显式声明为 PLANNED(未建)的脚手架路径(repo-relative)。存在于磁盘时必须保持 code-free。
_PLANNED_PATHS = [
    "services/gateway", "services/learning", "platform",
    "schemas/avro", "schemas/protobuf",
    "services/alpha/alpha_discovery_engine", "services/alpha/alpha_validation",
    "services/data/data_quality_service", "services/data/feature_store_service",
    "services/data/market_data_service", "services/data/tick_service",
    "services/research/model_registry", "services/research/experiment_tracker",
    "services/research/dataset_builder", "services/research/research_agent_loop",
    "services/portfolio/portfolio_research",
]


def _py_files(root):
    out = []
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


def test_planned_scaffolds_stay_code_free():
    # 存在于磁盘 -> 必须零 .py(仍是 code-free 计划脚手架,不是意外提交的死代码);缺失(新检出/CI)-> 放过
    offenders = []
    for rel in _PLANNED_PATHS:
        abspath = os.path.join(_ROOT, rel)
        if os.path.isdir(abspath):
            pys = _py_files(abspath)
            if pys:
                offenders.append(f"{rel}: {[os.path.relpath(p, _ROOT) for p in pys]}")
    assert not offenders, ("PLANNED scaffold path gained code — build the real module under "
                           "services/<domain>/*.py (flat) instead, or move the path out of "
                           "PLANNED_SCAFFOLDS.md if it is now real:\n" + "\n".join(offenders))


def test_guard_is_not_vacuous():
    # teeth:同一 walker 对一个已知真包必须找到 .py(证明"零 .py"断言非空洞)
    assert _py_files(os.path.join(_ROOT, "services", "alpha")), \
        "sanity: services/alpha should contain .py files"
