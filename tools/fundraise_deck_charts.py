# 融资路演 PPT 配图：Binance USDT-M 永续四策略历史模拟净值曲线（730 天，可复现证据）
import json, sys, datetime
from pathlib import Path

sys.path.insert(0, str(Path(sys.executable).parent.parent.parent))
from daimon_runtime import setup_plot
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

setup_plot()

BG = "#10151C"; GRID = "#2A3340"; TXT = "#C7CDD6"
COLORS = {
    "carry_oi_majors": "#C9A227",
    "crowd_contra": "#5B8DB8",
    "top_contra": "#8A8F85",
    "equal_weight_combo": "#E8E4D8",
}
LABELS = {
    "carry_oi_majors": "carry_oi_majors",
    "crowd_contra": "crowd_contra",
    "top_contra": "top_contra",
    "equal_weight_combo": "等权组合",
}

d = json.load(open("data/cache/binance_usdt_continuous_paper.json", encoding="utf-8"))

fig, ax = plt.subplots(figsize=(9.6, 4.4))
fig.patch.set_facecolor(BG); ax.set_facecolor(BG)

for sid, color in COLORS.items():
    eq = d["simulations"][sid]["equity_curve"]
    xs = [datetime.datetime.fromtimestamp(p["day"] / 1000, tz=datetime.timezone.utc) for p in eq]
    ys = [p["equity"] / eq[0]["equity"] for p in eq]
    lw = 2.2 if sid == "equal_weight_combo" else 1.5
    ax.plot(xs, ys, color=color, linewidth=lw, label=LABELS[sid])

# 标注附件口径的 180 天窗口（2026-01-25 ~ 2026-07-23）
w0 = datetime.datetime(2026, 1, 25, tzinfo=datetime.timezone.utc)
w1 = datetime.datetime(2026, 7, 23, tzinfo=datetime.timezone.utc)
ax.axvspan(w0, w1, color="#C9A227", alpha=0.07)
ax.text(w0, ax.get_ylim()[1], "", fontsize=8)

ax.tick_params(colors=TXT, labelsize=9)
for s in ax.spines.values():
    s.set_color(GRID)
ax.grid(color=GRID, alpha=0.5, linewidth=0.6)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%y-%m"))
leg = ax.legend(loc="upper left", fontsize=9, frameon=False, labelcolor=TXT)
ax.set_ylabel("净值（期初 = 1.0）", color=TXT, fontsize=10)
ax.set_title("Binance USDT-M 永续 · 历史模拟净值（730 天：2024-07-24 ~ 2026-07-23，阴影为 180 天窗口）",
             color="#E8E4D8", fontsize=11, pad=10)

fig.savefig("docs/investor/quantos_fundraise/media/equity_730d.png", dpi=200, bbox_inches="tight",
            facecolor=fig.get_facecolor())
print("saved")
