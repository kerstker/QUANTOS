# -*- coding: utf-8 -*-
"""QuantOS 融资路演 PPT 生成器 — 18 页深色机构风,全部真实数据,诚实标注。
Run: .venv-ppt/Scripts/python docs/fundraising/build_deck.py
"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
import os

# ---------- design system ----------
BG = RGBColor(0x0B, 0x0F, 0x17)
PANEL = RGBColor(0x10, 0x16, 0x1F)
PANEL2 = RGBColor(0x14, 0x1B, 0x26)
GRID = RGBColor(0x23, 0x30, 0x42)
TEXT = RGBColor(0xC7, 0xD0, 0xDC)
WHITE = RGBColor(0xF2, 0xF5, 0xF9)
MUTED = RGBColor(0x7C, 0x8A, 0x9C)
AMBER = RGBColor(0xD9, 0xA4, 0x41)
BLUE = RGBColor(0x4A, 0x90, 0xD9)
GREEN = RGBColor(0x3F, 0xB9, 0x7F)
RED = RGBColor(0xD9, 0x6A, 0x6A)
PURPLE = RGBColor(0x9B, 0x7E, 0xDE)
FONT = "Microsoft YaHei"

SW, SH = Inches(13.333), Inches(7.5)
ASSETS = "docs/fundraising/assets"

prs = Presentation()
prs.slide_width, prs.slide_height = SW, SH
BLANK = prs.slide_layouts[6]


def slide():
    s = prs.slides.add_slide(BLANK)
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = BG
    return s


def box(s, x, y, w, h, fill=None, line=None, line_w=0.75, shadow=False, round_=False):
    shp = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if round_ else MSO_SHAPE.RECTANGLE,
                             Inches(x), Inches(y), Inches(w), Inches(h))
    if round_:
        try:
            shp.adjustments[0] = 0.055
        except Exception:
            pass
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(line_w)
    shp.shadow.inherit = False
    return shp


def txt(s, x, y, w, h, runs, size=12, color=TEXT, bold=False, align=PP_ALIGN.LEFT,
        anchor=MSO_ANCHOR.TOP, leading=1.16, space_after=4):
    """runs: str 或 [(text, {size,color,bold}), ...] 或 [[runs_para], ...]"""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    if isinstance(runs, str):
        runs = [[(runs, {})]]
    elif runs and isinstance(runs[0], tuple):
        runs = [runs]
    for i, para in enumerate(runs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = leading
        p.space_after = Pt(space_after)
        for text, st in para:
            r = p.add_run()
            r.text = text
            r.font.name = st.get("font", FONT)
            r.font.size = Pt(st.get("size", size))
            r.font.bold = st.get("bold", bold)
            r.font.color.rgb = st.get("color", color)
            r.font._rPr.set(qn("a:ea"), st.get("font", FONT))
    return tb


def kicker(s, text, x=0.62, y=0.42):
    txt(s, x, y, 9, 0.3, text.upper(), size=10.5, color=AMBER, bold=True)


def headline(s, text, sub=None, y=0.72):
    txt(s, 0.62, y, 12.1, 1.0, text, size=27, color=WHITE, bold=True)
    box(s, 0.62, y + 0.62, 0.55, 0.028, fill=AMBER)
    if sub:
        txt(s, 0.62, y + 0.78, 12.1, 0.4, sub, size=12.5, color=MUTED)


def footer(s, page):
    box(s, 0.62, 7.08, 12.09, 0.012, fill=GRID)
    txt(s, 0.62, 7.14, 6, 0.3, "Confidential · 内部研发与投资人验证用途 · 历史模拟结果不代表未来表现",
        size=8, color=MUTED)
    txt(s, 12.0, 7.14, 0.7, 0.3, f"{page:02d}", size=9, color=MUTED, align=PP_ALIGN.RIGHT)


def chip(s, x, y, w, text, color, h=0.34, size=10, fill=PANEL2):
    box(s, x, y, w, h, fill=fill, line=color, line_w=1.0, round_=True)
    txt(s, x, y + 0.015, w, h, text, size=size, color=color, bold=True,
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


def bullets(s, x, y, w, items, size=12.5, gap=8, color=TEXT, mark_color=AMBER, leading=1.22):
    paras = []
    for it in items:
        if isinstance(it, str):
            it = (it, {})
        paras.append([("▪  ", {"color": mark_color, "size": size, "bold": True}),
                      (it[0], dict({"size": size, "color": color}, **it[1]))])
    txt(s, x, y, w, 5, paras, leading=leading, space_after=gap)


def panel(s, x, y, w, h, title=None, title_color=AMBER):
    box(s, x, y, w, h, fill=PANEL, line=GRID, line_w=0.75, round_=True)
    if title:
        txt(s, x + 0.22, y + 0.14, w - 0.44, 0.3, title, size=11.5, color=title_color, bold=True)


def notes(s, text):
    s.notes_slide.notes_text_frame.text = text


def pic(s, path, x, y, w=None, h=None):
    if os.path.exists(path):
        kw = {}
        if w: kw["width"] = Inches(w)
        if h: kw["height"] = Inches(h)
        s.shapes.add_picture(path, Inches(x), Inches(y), **kw)
        return True
    return False


def shot(s, name, x, y, w, label):
    """Insert a console screenshot if captured, else an honest placeholder box."""
    path = f"{ASSETS}/{name}"
    if pic(s, path, x, y, w=w):
        box(s, x, y, w, 0.02, fill=AMBER)
        return
    box(s, x, y, w, w * 0.56, fill=PANEL2, line=GRID, round_=True)
    txt(s, x, y + w * 0.24, w, 0.6, f"【待补充】{label}", size=11, color=MUTED,
        align=PP_ALIGN.CENTER)


PAGE = 0
def new_page():
    global PAGE
    PAGE += 1
    return PAGE

# ================= 1 封面 =================
p = new_page(); s = slide()
box(s, 0, 0, 13.333, 0.06, fill=AMBER)
txt(s, 0.62, 2.35, 12, 1.4, "QuantOS", size=66, color=WHITE, bold=True)
box(s, 0.66, 3.55, 1.1, 0.035, fill=AMBER)
txt(s, 0.62, 3.8, 12, 0.5, "AI 辅助的数字资产多策略量化投资与风险管理平台", size=19, color=TEXT)
txt(s, 0.62, 4.4, 12, 0.4, "将量化交易思想持续转化为可验证、可执行、可审计的投资策略。",
    size=13, color=MUTED)
txt(s, 0.62, 6.35, 12, 0.3, "融资路演 · 2026 年 7 月 · 【待补充：公司名称】", size=10.5, color=MUTED)
footer(s, p)
notes(s, "开场 30 秒:QuantOS 不是交易机器人,也不是让大模型预测涨跌,而是一套已经开发完成的 AI 量化操作系统。"
          "今天只讲三件事:系统已建成、证据诚实、本轮融资直接转化为实盘证据。")

# ================= 2 执行摘要 =================
p = new_page(); s = slide()
kicker(s, "Executive Summary"); headline(s, "核心系统已建成,正在进入实盘证据阶段")
cols = [
    ("我们是谁", "技术型创始人主导的量化系统工程团队。已独立交付一套覆盖数据、研究、验证、组合、风控、执行、归因与策略生命周期的完整平台。"),
    ("解决什么问题", "回测到实盘的收益退化、过拟合、策略失效不可见、实验不可复现——量化行业最昂贵的四个问题。"),
    ("已完成什么", "8 层系统架构、约 4,700 项自动化测试、19 项 fail-closed 门禁、36 个研究控制台页面、10 条策略前向验证时钟。"),
    ("当前阶段", "小资金实盘验证入口。三条候选策略完成历史模拟与真实执行纸面验证;一条失败策略已被系统自动识别并隔离。"),
    ("本轮要什么", "计划融资:人民币【待确认】万元。用途单一而明确:把工程成果转化为 12 个月的实盘证据与机构合作能力。"),
]
yy = 1.85
for i, (t, body) in enumerate(cols):
    x = 0.62 + (i % 3) * 4.12
    y = yy + (i // 3) * 2.35
    w = 3.88 if i < 3 else 6.0
    panel(s, x, y, w, 2.1, t)
    txt(s, x + 0.22, y + 0.52, w - 0.44, 1.5, body, size=11, color=TEXT, leading=1.28)
footer(s, p)
notes(s, "一页讲完。强调'已完成'与'未夸大':失败策略隔离是治理能力展示,不是弱点。")

# ================= 3 行业痛点 =================
p = new_page(); s = slide()
kicker(s, "Problem"); headline(s, "量化行业最贵的断裂带:回测到实盘")
stages = [("交易想法", TEXT), ("回测漂亮", GREEN), ("成本失真", AMBER),
          ("实盘退化", RED), ("策略失效", RED)]
x = 0.62
for i, (t, c) in enumerate(stages):
    chip(s, x, 2.1, 2.1, t, c, h=0.5, size=12)
    if i < 4:
        txt(s, x + 2.1, 2.12, 0.42, 0.5, "→", size=16, color=MUTED, align=PP_ALIGN.CENTER)
    x += 2.52
bullets(s, 0.62, 3.0, 12.1, [
    ("只有策略、没有基础设施:大多数量化项目把 90% 精力花在信号上,却没有数据治理、验证、风控和执行的完整链路。", {}),
    ("回测失真:数据泄漏、过拟合、参数选择偏差,让'漂亮曲线'在样本外平均大幅退化。", {}),
    ("成本被低估:手续费、滑点、资金费、市场冲击叠加后,很多'回测收益'在实盘中消失。", {}),
    ("策略失效不可见:市场制度变化后,没有生命周期管理的系统会持续把资金投入已死的策略。", {}),
    ("实验不可复现:人工研究难以复算、审计和归因,投资人无法判断收益来自 Alpha、Beta 还是运气。", {}),
    ("24/7 市场:数字资产全年无休,对自动风控、持续监控和异常处置的要求远高于传统市场。", {}),
], size=12, gap=7)
txt(s, 0.62, 6.35, 12.1, 0.5, "QuantOS 的价值不是预测每一次涨跌,而是建立一个持续生产、验证、组合、执行和淘汰策略的收益生产体系。",
    size=12.5, color=AMBER, bold=True)
footer(s, p)
notes(s, "投资人大多见过'回测漂亮实盘亏损'。这页建立共鸣:问题不在策略,在体系缺失。")

# ================= 4 为什么是现在 =================
p = new_page(); s = slide()
kicker(s, "Why Now"); headline(s, "三个正在收敛的条件")
cards = [
    ("市场结构成熟", "数字资产衍生品市场持续发展、24/7 运行、结构复杂度高,对系统化研究和自动风控的需求从'可选'变为'必需'。", BLUE),
    ("数据与工具可得", "资金费、持仓量、期权波动率等结构化数据快速增长,使曾经只有顶级机构能做的研究工艺变得可工程化。", GREEN),
    ("AI 改变研发经济学", "AI 辅助研究、代码交叉审查和实验自动化,让小团队首次具备建设和运营机构级研究平台的能力。", AMBER),
]
for i, (t, b, c) in enumerate(cards):
    x = 0.62 + i * 4.12
    panel(s, x, 1.9, 3.88, 3.3)
    box(s, x + 0.22, 2.14, 0.42, 0.05, fill=c)
    txt(s, x + 0.22, 2.32, 3.4, 0.4, t, size=15, color=WHITE, bold=True)
    txt(s, x + 0.22, 2.86, 3.44, 2.2, b, size=11.5, color=TEXT, leading=1.35)
txt(s, 0.62, 5.7, 12.1, 0.9, "我们不引用无法核实的市场规模数字。我们相信的是:在 24/7 高波动市场里,"
    "'可验证的研究体系'比'更会预测的模型'更稀缺。", size=12.5, color=MUTED, leading=1.35)
footer(s, p)
notes(s, "不编造 TAM。把'为什么是现在'落在能力与需求的收敛上,而不是数字游戏。")

# ================= 5 解决方案架构 =================
p = new_page(); s = slide()
kicker(s, "Solution"); headline(s, "一条从数据到实盘的完整闭环")
flow = ["数据治理", "Alpha 研究", "科学验证", "组合配置", "风险控制", "交易执行", "收益归因", "策略进化"]
x = 0.62
for i, t in enumerate(flow):
    w = 1.38
    box(s, x, 2.2, w, 0.62, fill=PANEL2, line=AMBER if i in (2, 4) else GRID, line_w=1.2, round_=True)
    txt(s, x, 2.32, w, 0.4, t, size=10.5, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
    if i < len(flow) - 1:
        txt(s, x + w - 0.02, 2.3, 0.22, 0.4, "→", size=12, color=MUTED, align=PP_ALIGN.CENTER)
    x += w + 0.17
txt(s, 0.62, 3.1, 12.1, 0.35, "任何一笔实盘订单,都可以追溯到数据快照、特征版本、Alpha 版本、组合版本、风险决策和成交结果。",
    size=11.5, color=MUTED)
layers = [
    ("研究层", "Research OS · Alpha Factory · 发现引擎(基因树/NSGA-II)· 实验管理与复现"),
    ("验证层", "泄漏检测 · Walk-forward · BH-FDR / DSR / PBO · 成本/扰动/集中度压力门"),
    ("组合与风控层", "组合优化 · 簇风险预算 · 9 引擎研究风控门 · 25 项盘前检查 · Kill Switch · 回撤状态机"),
    ("执行与证据层", "交易边界 · 不可变账本 · Paper/Shadow · 实盘损耗对照 · 生命周期与退役"),
]
for i, (t, b) in enumerate(layers):
    y = 3.7 + i * 0.82
    box(s, 0.62, y, 2.0, 0.66, fill=PANEL, line=GRID, round_=True)
    txt(s, 0.62, y + 0.14, 2.0, 0.4, t, size=11.5, color=AMBER, bold=True, align=PP_ALIGN.CENTER)
    txt(s, 2.82, y + 0.1, 9.9, 0.5, b, size=11, color=TEXT, leading=1.2)
footer(s, p)
notes(s, "架构只讲四层八环节。技术细节留给尽调数据室,这里只要投资人记住'闭环'和'可追溯'。")

# ================= 6 已完成能力 =================
p = new_page(); s = slide()
kicker(s, "Status"); headline(s, "已建成 · 验证中 · 融资后完善")
cols = [
    ("已建成", GREEN, [
        "8 层系统架构全部落地并有测试",
        "约 4,700 项自动化测试 · 19 项 fail-closed 门禁",
        "36 个研究控制台页面(全部真实数据)",
        "2,488 次试验记账(DSR 多重检验纪律)",
        "不可变哈希链账本 + 事件溯源",
        "实盘损耗对照引擎(Live vs Replay)",
    ]),
    ("验证中", AMBER, [
        "3 条候选策略:历史模拟 + 729 天真实执行纸面",
        "10 条策略的前向证据时钟(每日自动累计)",
        "Binance 主网执行链路(testnet 已联通)",
        "回撤控制器已接入实盘驱动(缺证据自动半配)",
        "投资人证据快照(账本直接生成)",
    ]),
    ("融资后完善", BLUE, [
        "首个小资金真实闭环(需验证资金)",
        "3–5 个低相关策略簇扩充",
        "期权/波动率数据类(已在积累)",
        "机构只读看板与尽调数据室",
        "核心团队最低配置",
    ]),
]
for i, (t, c, items) in enumerate(cols):
    x = 0.62 + i * 4.12
    panel(s, x, 1.85, 3.88, 4.9)
    chip(s, x + 0.22, 2.05, 1.5, t, c, h=0.36, size=11)
    bullets(s, x + 0.22, 2.6, 3.5, items, size=10, gap=6, mark_color=c)
footer(s, p)
notes(s, "这页回答'到底做了多少'。左侧全是可以现场演示的;中间是正在进行时;右侧就是融资用途。")

# ================= 7 AI 的角色 =================
p = new_page(); s = slide()
kicker(s, "AI in the Loop"); headline(s, "AI 加速研究,确定性风控拥有最终决策权")
bullets(s, 0.62, 1.9, 5.9, [
    ("AI 生成研究假设与实验计划", {}),
    ("AI 执行实验、读取结果、总结失败", {}),
    ("多模型交叉审查代码与研究结论", {}),
    ("研究预算合约:试验数/维度/数据源机器限额", {}),
    ("失败实验全部留痕,不可删除", {}),
], size=12.5, gap=9)
panel(s, 6.9, 1.85, 5.8, 4.3, "明确的边界(机器强制,非口头承诺)", RED)
bullets(s, 7.12, 2.35, 5.4, [
    ("AI 不绕过风控直接控制资金", {"color": WHITE, "bold": True}),
    ("AI 不能批准策略上线(人工多签门)", {}),
    ("AI 不能接触券商凭证", {}),
    ("AI 不能修改锁定的外样本区间", {}),
    ("AI 下单路径在代码中物理不存在", {}),
    ("非人 actor 结构性无法到达 APPROVED 状态", {}),
], size=11.5, gap=8, mark_color=RED)
footer(s, p)
notes(s, "机构最担心 AI 失控。我们的回答不是'请放心',而是'边界在代码里'——治理测试机器钉死这些约束。")

# ================= 8 Alpha 生命周期 =================
p = new_page(); s = slide()
kicker(s, "Alpha Lifecycle"); headline(s, "策略是会被淘汰的产品,不是一劳永逸的答案")
stages = ["研究想法", "原型", "回测", "验证门", "Paper", "Shadow", "小额实盘", "生产", "衰减监控", "退役"]
x = 0.62
for i, t in enumerate(stages):
    c = GREEN if i < 4 else (AMBER if i < 7 else (BLUE if i < 8 else MUTED))
    w = 1.1
    box(s, x, 2.0, w, 0.56, fill=PANEL2, line=c, line_w=1.0, round_=True)
    txt(s, x, 2.11, w, 0.36, t, size=9, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
    if i < len(stages) - 1:
        txt(s, x + w - 0.03, 2.08, 0.14, 0.4, "→", size=10, color=MUTED)
    x += w + 0.14
bullets(s, 0.62, 3.0, 12.1, [
    ("每一阶段都有机器化门禁:进入下一阶段需要上一阶段的证据,不是靠说服。", {}),
    ("真实案例:top_contra 在 730 天真实执行纸面中回撤越线,被非人维护机器自动标记 PAUSED——"
     "系统能识别并隔离失败策略,这是治理能力的一部分。", {"color": AMBER}),
    ("晋升三重门(新增):成本压力(1x/1.5x/2x)、参数扰动、集中度——全部可复用、可审计。", {}),
    ("衰减信号持续监控:滚动净 Alpha、实盘保留率、成本占比、残差相关性、容量。", {}),
], size=12, gap=9)
footer(s, p)
notes(s, "这页是全场的信任支点:主动讲失败策略被隔离的故事。诚实披露失败 = 可信度优势。")

# ================= 9 历史验证结果 =================
p = new_page(); s = slide()
kicker(s, "Track Record (Simulation)"); headline(s, "历史模拟:两套独立窗口口径一致为正")
rows = [
    ("carry_oi_majors", "+8.04%", "1.410", "4.18%", "24 / 263", BLUE),
    ("crowd_contra", "+2.76%", "0.518", "9.04%", "26 / 264", AMBER),
    ("top_contra(已隔离)", "-1.74%", "-0.260", "9.44%", "26 / 257", MUTED),
    ("等权组合", "+3.21%", "0.981", "3.82%", "26 / 342", GREEN),
]
hdr = ["策略", "净收益", "Sharpe", "最大回撤", "调仓/交易"]
xs = [0.62, 4.3, 5.7, 6.9, 8.3]
for x, h in zip(xs, hdr):
    txt(s, x, 1.72, 1.6, 0.3, h, size=10, color=MUTED, bold=True)
for i, r in enumerate(rows):
    y = 2.06 + i * 0.42
    box(s, 0.62, y - 0.04, 9.0, 0.38, fill=PANEL if i % 2 == 0 else PANEL2, line=None)
    for x, v in zip(xs, r[:5]):
        txt(s, x, y, 1.7, 0.3, v, size=10.5, color=r[5] if x != xs[0] else TEXT,
            bold=(x != xs[0]))
txt(s, 0.62, 3.9, 9.0, 0.3, "180 天独立窗口 · 2026-01-25 → 07-23 · Binance USDT-M 永续", size=9.5, color=MUTED)
txt(s, 0.62, 4.22, 9.0, 0.6, "口径:PIT 数据 · 4bps taker · 合约步长/最小名义 · 半价差 · 资金费 · Almgren–Chriss 冲击 · 连续周度真实调仓 · 追加式幂等账本",
    size=9.5, color=MUTED, leading=1.3)
panel(s, 9.9, 1.66, 2.8, 3.3, "729 天连续运行(同一引擎)")
bullets(s, 10.1, 2.14, 2.45, [
    ("carry  +23.8% · SR 0.78", {"size": 9.5}),
    ("crowd  +39.5% · SR 1.12", {"size": 9.5}),
    ("top  +21.8% · SR 0.68", {"size": 9.5}),
    ("组合  +29.2% · SR 1.36", {"size": 9.5}),
], size=9.5, gap=5)
pic(s, f"{ASSETS}/equity_729d.png", 0.62, 4.85, w=8.4)
txt(s, 9.9, 5.15, 2.8, 0.9, "历史模拟结果,\n不代表未来表现。", size=13, color=RED, bold=True, leading=1.3)
shot(s, "shot_console.png", 9.9, 6.0, 2.8, "系统截图：研究控制台")
footer(s, p)
notes(s, "主动念出红字。两套窗口数字来自同一引擎不同运行,口径已注明;top_contra 灰显并标注已隔离。")

# ================= 10 为什么值得继续验证 =================
p = new_page(); s = slide()
kicker(s, "Evidence Quality"); headline(s, "这些数字为什么值得进入实盘验证")
bullets(s, 0.62, 1.9, 5.9, [
    ("真实摩擦:费用、步长、最小名义、价差、资金费、冲击全部计入", {}),
    ("连续真实调仓:不是一次性静态回测,是 24–26 周连续再平衡", {}),
    ("不可变账本:每次调仓追加记录、哈希链、幂等可重放", {}),
    ("对账恒等式:盈亏 reconciled 残差 ≤ 1e-6", {}),
    ("失败不藏:负收益策略同页展示并进入隔离流程", {}),
], size=12, gap=9)
panel(s, 6.9, 1.85, 5.8, 4.5, "同样重要的诚实边界", AMBER)
bullets(s, 7.12, 2.35, 5.4, [
    ("180 天样本很短,729 天窗口也仍属单一市场制度", {}),
    ("当前资金费环境处于历史压缩期(仅为全历史均值约 37%)", {}),
    ("历史模拟不是实盘:冲击与流动性假设仍需真实成交校准", {}),
    ("这些结果是小资金实盘的入场券,不是大规模募资的依据", {"color": WHITE, "bold": True}),
    ("项目当前的核心指标:回测→实盘收益保留率 ≥ 0.70(稳定期目标)", {}),
], size=11.5, gap=8)
footer(s, p)
notes(s, "左页讲证据质量,右页讲边界。投资人尽调时最欣赏主动暴露样本缺陷的团队。")

# ================= 11 风险治理 =================
p = new_page(); s = slide()
kicker(s, "Risk Governance"); headline(s, "风控不是功能模块,是系统的底层协议")
grid_items = [
    ("数据风险", "PIT 纪律 · 准入门 · 谱系回填 · 质量 fail-closed"),
    ("模型风险", "多重检验 · DSR/PBO · 参数扰动 · 模型版本"),
    ("策略风险", "生命周期 · 衰减监控 · 自动降权/隔离/退役"),
    ("组合风险", "簇风险预算 · 共同因子拆解 · 集中度门 · 相关性冲击"),
    ("流动性/容量", "AC 冲击 · 参与率上限 · 容量安全带 · 部署≤安全容量/3"),
    ("交易所风险", "凭证权限硬门 · IP 白名单 · 提现已关核验 · 对账硬停"),
    ("执行风险", "25 项盘前检查 · 幂等防重 · 僵尸单清理 · 崩溃恢复"),
    ("操作风险", "人工多签门 · Kill Switch · 回撤状态机 · 事故台账"),
]
for i, (t, b) in enumerate(grid_items):
    x = 0.62 + (i % 2) * 6.14
    y = 1.85 + (i // 2) * 1.22
    panel(s, x, y, 5.9, 1.06)
    txt(s, x + 0.2, y + 0.12, 1.9, 0.4, t, size=11.5, color=AMBER, bold=True)
    txt(s, x + 2.1, y + 0.12, 3.65, 0.8, b, size=10, color=TEXT, leading=1.25)
footer(s, p)
notes(s, "每一格都能对应到代码与测试。强调'缺证据不放满配'的默认姿态:部署资金在证据不足时自动减半。")

# ================= 12 竞争壁垒 =================
p = new_page(); s = slide()
kicker(s, "Moat"); headline(s, "五层护城河:可以被模仿的是策略,难以复制的是体系")
moats = [
    ("研究工艺", "标准化实验 · 防过拟合 · 多重检验 · 晋升与退役纪律", AMBER),
    ("数据资产", "清洗后历史数据 · 资金费/OI · 实验数据库 · 版本与血缘", BLUE),
    ("实盘反馈", "真实滑点/成交/冲击 · 回测-实盘偏差 · 退化测量", GREEN),
    ("系统工程", "数据到实盘完整闭环 · 可回放/可复现/可审计 · 4,700+ 测试", PURPLE),
    ("组织学习", "AI 辅助研究 · 多模型交叉审查 · 失败实验知识库", RED),
]
for i, (t, b, c) in enumerate(moats):
    y = 1.85 + i * 0.94
    box(s, 0.62, y, 2.2, 0.78, fill=PANEL2, line=c, line_w=1.2, round_=True)
    txt(s, 0.62, y + 0.22, 2.2, 0.4, t, size=13, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
    txt(s, 3.05, y + 0.15, 9.6, 0.55, b, size=11.5, color=TEXT, leading=1.25)
txt(s, 0.62, 6.6, 12.1, 0.4, "竞争对手可以模仿某一条策略,但很难复制持续发现、验证、部署、监控和淘汰策略的完整生产体系。",
    size=12.5, color=AMBER, bold=True)
footer(s, p)
notes(s, "壁垒排序有意为之:实盘反馈与系统工程最难复制,因为它们只能用时间和纪律换。")

# ================= 13 商业模式 =================
p = new_page(); s = slide()
kicker(s, "Business Model"); headline(s, "三段式:证据先行,合规先行")
stages_bm = [
    ("第一阶段 · 自营策略验证", "现在 → 12 个月", [
        "自有/合作验证资金的小资金实盘",
        "验证风险、执行与收益保留率",
        "形成可审计的真实实盘记录",
    ], AMBER),
    ("第二阶段 · 机构合作与策略管理", "以合规为前提 · 里程碑触发", [
        "专户策略合作 · 机构联合产品",
        "策略管理 · 技术服务 · 业绩分成",
        "不承诺公开募资或代客理财",
    ], BLUE),
    ("第三阶段 · 基础设施输出", "长期", [
        "研究平台私有化部署",
        "风控引擎 / Alpha 实验平台",
        "数据与信号服务 · 机构技术合作",
    ], GREEN),
]
for i, (t, tag, items, c) in enumerate(stages_bm):
    x = 0.62 + i * 4.12
    panel(s, x, 1.9, 3.88, 4.3)
    box(s, x + 0.22, 2.12, 0.42, 0.05, fill=c)
    txt(s, x + 0.22, 2.28, 3.4, 0.4, t, size=13, color=WHITE, bold=True)
    txt(s, x + 0.22, 2.72, 3.4, 0.3, tag, size=9.5, color=c, bold=True)
    bullets(s, x + 0.22, 3.15, 3.45, items, size=10.5, gap=7, mark_color=c)
txt(s, 0.62, 6.5, 12.1, 0.4, "短期用自营实盘建立证据,中期通过合作扩大收益,长期通过技术平台提高收入稳定性与估值上限。",
    size=11.5, color=MUTED)
footer(s, p)
notes(s, "强调阶段门:第二阶段只有在第一阶段证据成立且合规结构清晰后才启动。")

# ================= 14 融资用途 =================
p = new_page(); s = slide()
kicker(s, "Use of Funds"); headline(s, "计划融资:人民币【待确认】万元 · 建议分配")
pic(s, f"{ASSETS}/funding_alloc.png", 0.62, 1.8, w=6.4)
panel(s, 7.5, 1.85, 5.2, 4.6, "资金纪律(建议方案,可修改)")
bullets(s, 7.72, 2.35, 4.8, [
    ("不投豪华办公室、非核心行政、大规模硬件", {}),
    ("维持约 12 个月合理运营周期", {}),
    ("每笔支出对应可验收里程碑", {}),
    ("分阶段到账:达到实盘/回撤/策略数里程碑后支付后续资金", {}),
    ("独立账户 · 定期披露 · 资金用途限制 · 明确停止条件", {}),
], size=11.5, gap=9)
txt(s, 0.62, 6.55, 12.1, 0.35, "具体交易结构(分期/里程碑/可转股安排)由双方及专业法律、财务顾问进一步设计。",
    size=10.5, color=MUTED)
footer(s, p)
notes(s, "金额留白是刻意的:先对齐估值逻辑,再谈数字。投资人关心的是钱怎么变成证据。")

# ================= 15 路线图 =================
p = new_page(); s = slide()
kicker(s, "Roadmap"); headline(s, "未来 12 个月:每个里程碑都可验收")
road = [
    ("0–3 个月", "小资金真实闭环", [
        "真实订单/成交/持仓/资金/PnL 对账",
        "回测·影子·实盘三线对照上线",
        "每日 NAV 与周度报告",
        "核心策略参数与风险预算冻结",
    ], AMBER),
    ("3–6 个月", "证据积累", [
        "连续实盘记录 · 实盘退化分析",
        "扩充 3–5 个低相关策略簇",
        "容量与压力测试 · Alpha 归因",
        "投资人只读看板 · 尽调数据室",
    ], BLUE),
    ("6–12 个月", "商业验证", [
        "多策略实盘组合运行",
        "首个正式机构合作或验证资金",
        "月度投资人报告 · 核心团队到位",
        "按实盘结果决定是否扩大规模",
    ], GREEN),
]
for i, (t, st, items, c) in enumerate(road):
    x = 0.62 + i * 4.12
    chip(s, x, 1.85, 1.6, t, c, h=0.4, size=11.5)
    panel(s, x, 2.45, 3.88, 3.9)
    txt(s, x + 0.22, 2.62, 3.4, 0.4, st, size=14, color=WHITE, bold=True)
    bullets(s, x + 0.22, 3.15, 3.45, items, size=10.5, gap=8, mark_color=c)
txt(s, 0.62, 6.55, 12.1, 0.35, "不设未经证明的规模目标。任何阶段未通过验收门,不因展示或融资压力跳过。",
    size=11, color=MUTED)
footer(s, p)
notes(s, "路线图的每个动词都对应系统里已存在的度量,不是愿望清单。")

# ================= 16 团队 =================
p = new_page(); s = slide()
kicker(s, "Team"); headline(s, "技术型创始人 · 核心团队组建中")
panel(s, 0.62, 1.85, 5.9, 4.6, "创始人 · 联合创始人 & CTO")
bullets(s, 0.84, 2.35, 5.5, [
    ("计算机/网络工程/软件开发/AI 应用/量化系统", {}),
    ("Python · C++ · FastAPI · PyTorch · React/TS · 数据工程 · 分布式与交易系统", {}),
    ("独立完成 QuantOS 主体研发:从数据治理到实盘执行栈", {}),
    ("多 AI 模型交叉审查的研发流程管理", {}),
    ("核心信条:防过拟合、风险治理、实盘偏差优先于预测能力", {"color": AMBER}),
], size=11.5, gap=9)
panel(s, 6.9, 1.85, 5.8, 4.6, "融资后优先岗位", BLUE)
bullets(s, 7.12, 2.35, 5.4, [
    ("量化研究员(Alpha 方向)", {}),
    ("交易系统工程师(执行/低延迟)", {}),
    ("数据工程师(数据治理/管道)", {}),
    ("风险与合规顾问(外部)", {}),
    ("【待补充:创始人姓名/简介/联系方式】", {"color": MUTED}),
], size=11.5, gap=9, mark_color=BLUE)
footer(s, p)
notes(s, "不虚构团队。创始人最大的证据就是这套已经跑起来的系统本身。")

# ================= 17 为什么现在投资 =================
p = new_page(); s = slide()
kicker(s, "Why Invest Now"); headline(s, "跨过概念阶段,进入证据兑现期")
bullets(s, 0.62, 1.95, 6.7, [
    ("核心系统已完成:不是 PPT 项目,今天就可以现场演示完整闭环", {}),
    ("初步历史证据已就位,团队诚实披露其边界与失败案例", {}),
    ("本轮资金直接转化为三样东西:实盘证据、策略扩展、机构合作能力", {}),
    ("风险可通过分阶段投资与里程碑付款管理:每一笔钱对应可验收结果", {}),
    ("下一阶段里程碑明确:真实闭环 → 连续记录 → 机构合作,全部可度量", {}),
], size=13, gap=11)
shot(s, "shot_golive.png", 7.6, 1.95, 5.1, "系统截图：上线向导")
txt(s, 7.6, 4.9, 5.1, 0.3, "系统实貌:上线向导(真实控制台,只读)", size=9, color=MUTED)
box(s, 0.62, 5.4, 12.1, 0.012, fill=GRID)
txt(s, 0.62, 5.6, 12.1, 0.5, "建议安排:45 分钟系统现场演示 + 尽调数据室开放。",
    size=13, color=AMBER, bold=True)
footer(s, p)
notes(s, "收尾号召要具体:演示和尽调,而不是'请相信我们'。")

# ================= 18 结束页 =================
p = new_page(); s = slide()
box(s, 0, 7.44, 13.333, 0.06, fill=AMBER)
txt(s, 0.62, 2.5, 12.1, 1.0, "Build Evidence. Control Risk. Compound Intelligence.",
    size=30, color=WHITE, bold=True)
box(s, 0.66, 3.6, 1.1, 0.03, fill=AMBER)
txt(s, 0.62, 3.85, 12, 0.5, "用证据建立信任,用风控保护资本,用系统实现长期进化。", size=16, color=TEXT)
txt(s, 0.62, 5.6, 12, 1.2, "【待补充:创始人姓名】\n【待补充:电话 / 邮箱】\n【待补充:公司名称】",
    size=12, color=MUTED, leading=1.5)
footer(s, p)
notes(s, "结束页留白给真实联系方式。")

# ================= save =================
out = "docs/fundraising/QuantOS_融资路演_v1.pptx"
prs.save(out)
print(f"saved: {out} ({PAGE} pages)")
