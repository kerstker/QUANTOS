# 投资人报告配图：干跑流水线多策略净值曲线（vol-target 可部署口径）
import sqlite3, json, sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.execution.backtest_authority import quarantine_legacy_backtest

quarantine_legacy_backtest("tools.investor_report_charts")

sys.path.insert(0, str(Path(sys.executable).parent.parent.parent))
from daimon_runtime import setup_plot
import matplotlib.pyplot as plt

setup_plot()

db = sqlite3.connect("data/cache/dry_run.db")

def pts(eq):
    out = []
    for p in eq:
        if isinstance(p, dict):
            out.append(p.get("equity", p.get("value")))
        elif isinstance(p, (list, tuple)):
            out.append(p[1])
        else:
            out.append(p)
    return [v for v in out if isinstance(v, (int, float))]

STRATS = [
    ("top_contra", "top_contra（顶部持仓反向）VT 夏普 1.30"),
    ("crowd_contra", "crowd_contra（拥挤度反向）VT 夏普 1.10"),
    ("carry", "carry（资金费套利）VT 夏普 0.75"),
]

fig, ax = plt.subplots(figsize=(10, 5.5))
for sid, label in STRATS:
    row = db.execute(
        "select detail from dry_run where strategy_id=? order by timestamp desc limit 1", (sid,)
    ).fetchone()
    det = json.loads(row[0])
    eq = pts(det.get("equity_curve_vt") or [])
    if not eq:
        continue
    norm = [v / eq[0] for v in eq]
    ax.plot(range(len(norm)), norm, label=label, linewidth=1.8)

ax.set_title("QUANTOS 端到端干跑流水线：策略净值曲线（vol-target 口径，含成本/整手/风控门）")
ax.set_xlabel("调仓期（周）")
ax.set_ylabel("净值（期初 = 1.0）")
ax.legend(loc="upper left")
ax.grid(alpha=0.3)
fig.savefig("docs/investor/dry_run_equity_curves.png", dpi=200, bbox_inches="tight")
print("saved docs/investor/dry_run_equity_curves.png")
