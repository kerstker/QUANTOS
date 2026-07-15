"""治理:机器强制 PIT 裸读边界(路线图 Rank #8)。

`SqlitePITStore.fetch_bars(instrument, start, end)` 是 RAW event_time 窗口读——它**不**过滤
`knowledge_time`,单独使用**不防前视**(persistent_store.py 自注释:"NOT lookahead-safe on its
own")。PIT 安全读是 `as_of()` / `latest_as_of()`(带 `knowledge_time<=as_of`)。整个数据层要防的
头号自欺就是这条裸读悄悄溜进回测/研究路径。此前只有一行注释挡着;本测试把注释级防护升级为
**AST 机器强制**,复刻 `test_crypto_data_boundary.py` 的模式:

1) **services/ 整棵树(强保证)**:确定性研究核心里,除了少数合法 infra 用法(缓存包装器 /
   日历完备性审计 / catalog 元数据枚举 / dataset 版本哈希),任何模块调用 `.fetch_bars(` 即违规。
   核心信号路径(`panel_builder`)已只走 `as_of`——本测试钉死这条「services/ 无裸读(白名单外)」
   不退化,新的信号/回测代码一旦裸读即红。
2) **tools/ 研究驱动器(棘轮)**:门外驱动器存量裸读(建 event_time 网格 + 全样本 ADV 容量估计,
   轻度前视)按调用点计数钉死——**只减不增**。新增裸读即红;存量迁移随 Rank #11(ADV 同源化)
   收敛到 PIT 安全读后,把 `_TOOLS_PINNED` 相应下调。

诚实边界:tools/ 的「建网格」用法(只取 event_time、不读 bar 值)本身是 PIT 中性的;真正的轻度
前视是「全样本 ADV」容量估计。第一步先钉住不增长,不在本增量里迁移存量(那会改行为,属 Rank #11)。
"""
import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SERVICES = os.path.join(_ROOT, "services")
_TOOLS = os.path.join(_ROOT, "tools")

# services/ 里合法调用 .fetch_bars( 的 infra 模块(非信号/回测路径,不引入前视):
#   caching_store  —— CachingPITStore 一次性载入全 bar,再在 as_of() 内按 knowledge_time 过滤(安全包装器)
#   calendar_store —— reconcile_calendar 完备性审计(自标注 "not a PIT read")
#   console_api    —— data-catalog 端点枚举数据集元数据(content_hash / 新鲜度),非回测
#   session        —— _derive_dataset_version 对全窗口算 content_hash(版本指纹,非信号读)
_SERVICES_WHITELIST = {
    "services/data/caching_store.py",
    "services/data/calendar_store.py",
    "services/console/data_console.py",
    "services/data/research_adapter.py",
}

# tools/ 门外研究驱动器的存量裸读调用点总数(AST 计数)。只减不增:新增即红;迁移后下调此数。
_TOOLS_PINNED = 35


def _fetch_bars_call_sites(path: str) -> list:
    """该文件内 `<expr>.fetch_bars(...)` 调用的行号列表(CALL 站点,非方法定义)。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "fetch_bars"]


def _tree_call_sites(root: str) -> dict:
    """{相对 posix 路径: 裸读调用点数},只列有调用的文件。"""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            sites = _fetch_bars_call_sites(p)
            if sites:
                out[os.path.relpath(p, _ROOT).replace("\\", "/")] = len(sites)
    return out


def test_services_tree_bare_reads_are_whitelisted_infra_only():
    """services/ 里任何调用 .fetch_bars( 的文件都必须是已知合法 infra(白名单),否则=前视脚印。"""
    callers = set(_tree_call_sites(_SERVICES))
    offenders = sorted(callers - _SERVICES_WHITELIST)
    assert not offenders, (
        "services/ 出现未白名单的裸读 fetch_bars —— 确定性研究核心不得静默重引入前视。"
        "用 as_of()/latest_as_of() 做 PIT 读,或(确属 infra)把文件加进 _SERVICES_WHITELIST:\n"
        + "\n".join(offenders))


def test_services_whitelist_has_no_stale_entries():
    """白名单不得腐烂:每个白名单文件必须仍真的调用 .fetch_bars(,否则应移除(防止白名单越攒越松)。"""
    callers = set(_tree_call_sites(_SERVICES))
    stale = sorted(_SERVICES_WHITELIST - callers)
    assert not stale, f"这些白名单条目已不再裸读,请从 _SERVICES_WHITELIST 移除:{stale}"


def test_tools_bare_read_count_is_ratcheted():
    """tools/ 存量裸读按调用点计数钉死;新增即红,迁移后请把 _TOOLS_PINNED 下调(只减不增)。"""
    total = sum(_tree_call_sites(_TOOLS).values())
    assert total == _TOOLS_PINNED, (
        f"tools/ 裸读 fetch_bars 调用点 = {total},钉死值 = {_TOOLS_PINNED}。"
        "新增裸读请改用 PIT 安全读(as_of);若是迁移移除了裸读,请把 _TOOLS_PINNED 下调到新值。")


# --- 正/负控制:证明 AST 扫描器真的抓裸读、且不误伤 PIT 安全读(否则守卫形同虚设) ---

def _sites_in_source(src: str) -> list:
    tree = ast.parse(src)
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "fetch_bars"]


def test_scanner_catches_a_planted_bare_read():
    planted = "def backtest(store):\n    return store.fetch_bars('BTC', 0, 999)\n"
    assert _sites_in_source(planted) == [2]


def test_scanner_ignores_pit_safe_reads():
    safe = ("def backtest(store, t):\n"
            "    grid = store.as_of('BTC', as_of_time=t)\n"
            "    latest = store.latest_as_of('BTC', as_of_time=t)\n"
            "    return grid, latest\n")
    assert _sites_in_source(safe) == []
