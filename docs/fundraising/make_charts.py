# -*- coding: utf-8 -*-
"""Generate dark institutional chart assets for the QuantOS fundraising deck.

All data comes from real system artifacts — no fabricated numbers.
Run: .venv-ppt/Scripts/python docs/fundraising/make_charts.py
"""
import json
import datetime
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

BG = "#0B0F17"
PANEL = "#10161F"
GRID = "#233042"
TEXT = "#C7D0DC"
MUTED = "#7C8A9C"
AMBER = "#D9A441"
BLUE = "#4A90D9"
GREEN = "#3FB97F"
RED = "#D96A6A"
PURPLE = "#9B7EDE"

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": PANEL, "savefig.facecolor": BG,
    "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "text.color": TEXT,
    "xtick.color": MUTED, "ytick.color": MUTED, "grid.color": GRID,
    "font.size": 11, "axes.grid": True, "grid.linewidth": 0.5, "grid.alpha": 0.6,
    "font.family": ["Microsoft YaHei", "DejaVu Sans"],
})

OUT = "docs/fundraising/assets"
import os
os.makedirs(OUT, exist_ok=True)

# ---- real data: 729-day continuous realistic-execution paper (artifact of record) ----
d = json.load(open("data/cache/binance_usdt_continuous_paper.json", encoding="utf-8"))
sims = d["simulations"]
start = datetime.date(2024, 7, 25)

SERIES = [
    ("carry_oi_majors", "carry_oi_majors（资金费截面）", BLUE),
    ("crowd_contra", "crowd_contra（拥挤反向）", AMBER),
    ("top_contra", "top_contra（已隔离·诚实披露）", MUTED),
    ("equal_weight_combo", "等权组合", GREEN),
]

fig, ax = plt.subplots(figsize=(11.8, 3.0), dpi=200)
for key, label, color in SERIES:
    eq = sims[key]["equity_curve"]
    dates = [start + datetime.timedelta(days=i) for i in range(len(eq))]
    vals = [(p["equity"] if isinstance(p, dict) else p) / 1e6 for p in eq]
    lw = 2.0 if key != "top_contra" else 1.2
    alpha = 1.0 if key != "top_contra" else 0.65
    ax.plot(dates, vals, label=label, color=color, linewidth=lw, alpha=alpha)
ax.axhline(1.0, color=MUTED, linewidth=0.7, linestyle="--", alpha=0.5)
ax.set_ylabel("净值（初始 = 1.00）")
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
legend = ax.legend(loc="upper left", frameon=False, fontsize=9)
for t in legend.get_texts():
    t.set_color(TEXT)
ax.set_title("历史模拟净值 · Binance USDT-M · 729 天连续运行（PIT + 4bps taker + 价差 + Almgren–Chriss 冲击）",
             color=TEXT, fontsize=11, loc="left", pad=12)
fig.tight_layout()
fig.savefig(f"{OUT}/equity_729d.png")
plt.close(fig)

# ---- drawdown of the combo (real) ----
eq = sims["equal_weight_combo"]["equity_curve"]
vals = [(p["equity"] if isinstance(p, dict) else p) for p in eq]
peak, dds = vals[0], []
for v in vals:
    peak = max(peak, v)
    dds.append((v / peak - 1) * 100)
dates = [start + datetime.timedelta(days=i) for i in range(len(vals))]
fig, ax = plt.subplots(figsize=(10.2, 2.5), dpi=200)
ax.fill_between(dates, dds, 0, color=RED, alpha=0.55, linewidth=0)
ax.plot(dates, dds, color=RED, linewidth=0.8)
ax.set_ylabel("回撤 %")
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
ax.set_title("等权组合回撤（历史模拟）", color=TEXT, fontsize=10, loc="left", pad=8)
fig.tight_layout()
fig.savefig(f"{OUT}/combo_drawdown.png")
plt.close(fig)

# ---- funding-use donut (suggested allocation, placeholders) ----
ALLOC = [
    ("小资金实盘验证与风险准备", 25, AMBER),
    ("数据采购与基础设施", 20, BLUE),
    ("核心 Alpha 研究", 20, GREEN),
    ("执行与实盘退化监控", 12, PURPLE),
    ("风控/合规/治理", 10, RED),
    ("核心团队最低配置", 13, MUTED),
]
fig, ax = plt.subplots(figsize=(8.8, 4.4), dpi=200)
wedges, _ = ax.pie([a[1] for a in ALLOC], colors=[a[2] for a in ALLOC],
                   startangle=90, counterclock=False,
                   wedgeprops=dict(width=0.42, edgecolor=BG, linewidth=2))
ax.text(0, 0.06, "融资用途", ha="center", color=TEXT, fontsize=13)
ax.text(0, -0.12, "建议方案 · 金额【待确认】", ha="center", color=MUTED, fontsize=8.5)
ax.legend(wedges, [f"{a[0]}  {a[1]}%" for a in ALLOC], loc="center left",
          bbox_to_anchor=(1.02, 0.5), frameon=False, fontsize=9)
fig.tight_layout()
fig.savefig(f"{OUT}/funding_alloc.png")
plt.close(fig)

print("charts done:", os.listdir(OUT))
