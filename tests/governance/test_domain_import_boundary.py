"""治理:机器钉死**今天已成立**的域间 import 不变量(Phase 6 item 2,P2-4 的 clean-additive 半区)。

镜像 test_live_execution_boundary.py 的 AST 风格,移动零代码。**只断言当前图上已成立的 leaf/单向不变量**
(不断言"域间完全无环"——research god-package 今天确有环:research<->{execution,risk,intelligence,
portfolio,alpha},那需要拆子包=大重构,按红线留后续,见 docs Phase 6 defer)。这些锁提供未来解耦的回归网:
谁哪天新加一条违规边,报错点名具体文件。

1) quantos/contracts/ 是真 leaf:不 import services/live/tools;
2) services/data/ 是地基 leaf:不 import 别的 services 域;
3) live/ 不 import tools/;
4) services/ 不 import live/(单向,已被 live 边界测试覆盖,这里 defense-in-depth 复述)。
"""
import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _all_py_files(root):
    out = []
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


def _import_modules(path):
    """完整 dotted 模块名(不只顶层根)——检测 services.<域> 跨域边需要第二段。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                mods.add(a.name)
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.add(n.module)
    return mods


def _rel(path):
    return os.path.relpath(path, _ROOT)


def test_contracts_is_a_leaf():
    # quantos/contracts/ 不得 import services/live/tools(领域契约是真 leaf,谁都能依赖它、它不依赖谁)
    offenders = []
    for path in _all_py_files(os.path.join(_ROOT, "quantos", "contracts")):
        bad = {m for m in _import_modules(path) if m.split(".")[0] in {"services", "live", "tools"}}
        if bad:
            offenders.append(f"{_rel(path)}: {sorted(bad)}")
    assert not offenders, "quantos/contracts must not import services/live/tools:\n" + "\n".join(offenders)


def test_services_data_does_not_import_other_service_domains():
    # services/data/ 是地基层(PIT store/universe/quality),不得 import 别的 services 域
    offenders = []
    for path in _all_py_files(os.path.join(_ROOT, "services", "data")):
        bad = {m for m in _import_modules(path)
               if m.split(".")[:2].__len__() == 2 and m.split(".")[0] == "services"
               and m.split(".")[1] != "data"}
        if bad:
            offenders.append(f"{_rel(path)}: {sorted(bad)}")
    assert not offenders, "services/data must not import other service domains:\n" + "\n".join(offenders)


def test_live_does_not_import_tools():
    # live/ 是执行栈,tools/ 是驱动器层(可 import 两边);live/ 反向 import tools/ 会倒挂依赖
    offenders = []
    for path in _all_py_files(os.path.join(_ROOT, "live")):
        bad = {m for m in _import_modules(path) if m.split(".")[0] == "tools"}
        if bad:
            offenders.append(f"{_rel(path)}: {sorted(bad)}")
    assert not offenders, "live/ must not import tools/:\n" + "\n".join(offenders)


def test_services_does_not_import_live():
    # 单向边界(live/ 依赖 services/,反向禁止)——已被 test_live_execution_boundary 覆盖,此处纵深复述
    offenders = []
    for path in _all_py_files(os.path.join(_ROOT, "services")):
        bad = {m for m in _import_modules(path) if m.split(".")[0] == "live"}
        if bad:
            offenders.append(f"{_rel(path)}: {sorted(bad)}")
    assert not offenders, "services/ must not import live/:\n" + "\n".join(offenders)


def test_boundary_check_has_teeth_not_vacuous():
    # 证伪空洞:同一检测谓词对一条合成违规边必须报出,对合法域内边必须放过
    def data_cross_domain(mods):
        return {m for m in mods
                if len(m.split(".")) >= 2 and m.split(".")[0] == "services" and m.split(".")[1] != "data"}
    assert data_cross_domain({"services.research.foo"}) == {"services.research.foo"}   # 违规被抓
    assert data_cross_domain({"services.data.pit_store", "os", "json"}) == set()        # 合法放过
