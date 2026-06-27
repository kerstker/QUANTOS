"""The cs-alpha genome must MACHINE-GENERATE only VALID, cross-sectional, depth-bounded, PIT-safe,
DETERMINISTIC expressions — so a search can mass-produce alphas reproducibly without ever emitting a
broken or look-ahead tree. These tests pin all of that on the price+volume+FUNDING grammar."""
import services.research.alpha_expression as ax
from services.research import cs_genome as g

FIELDS = ["close", "volume", "funding"]
WINDOWS = [3, 5, 7, 14]


def _panel():
    T, N = 12, 3
    close = [[100.0 + i + j for j in range(N)] for i in range(T)]
    volume = [[1000.0 + 10 * i + j for j in range(N)] for i in range(T)]
    funding = [[0.0001 * (((i + j) % 5) - 2) for j in range(N)] for i in range(T)]
    return ax.FieldPanel(["A", "B", "C"], list(range(T)), {"close": close, "volume": volume, "funding": funding})


def test_seed_alphas_all_valid_and_evaluable():
    panel = _panel()
    seeds = g.seed_alphas(FIELDS, WINDOWS)
    assert len(seeds) >= 12
    for s in seeds:
        assert g.valid(s, FIELDS), s
        assert s[0] in g._CSO                       # cross-sectional root
        m = ax.evaluate(s, panel)                   # PIT-safe by construction; must not raise
        assert ax._dims(m) == (12, 3)


def test_mutate_is_deterministic_and_valid():
    seeds = g.seed_alphas(FIELDS, WINDOWS)
    panel = _panel()
    for s in seeds[:8]:
        for step in range(6):
            m1 = g.mutate(s, FIELDS, WINDOWS, step)
            m2 = g.mutate(s, FIELDS, WINDOWS, step)
            assert m1 == m2                          # deterministic: same (tree, step) -> same child
            assert g.valid(m1, FIELDS) and m1[0] in g._CSO
            assert g._depth(m1) <= g._MAX_DEPTH
            ax.evaluate(m1, panel)                   # evaluable, no crash


def test_crossover_is_deterministic_and_valid():
    seeds = g.seed_alphas(FIELDS, WINDOWS)
    panel = _panel()
    for a in seeds[:5]:
        for b in seeds[5:10]:
            c1 = g.crossover(a, b, FIELDS, WINDOWS, 0)
            c2 = g.crossover(a, b, FIELDS, WINDOWS, 0)
            assert c1 == c2
            assert g.valid(c1, FIELDS) and c1[0] in g._CSO and g._depth(c1) <= g._MAX_DEPTH
            ax.evaluate(c1, panel)


def test_iterated_evolution_stays_valid():
    # 50 generations of mutate+crossover never escape the valid cross-sectional, depth-bounded space
    pop = g.seed_alphas(FIELDS, WINDOWS)
    panel = _panel()
    cur = pop[:6]
    for gen in range(50):
        nxt = []
        for i, t in enumerate(cur):
            child = g.mutate(t, FIELDS, WINDOWS, gen)
            mate = cur[(i + 1) % len(cur)]
            child = g.crossover(child, mate, FIELDS, WINDOWS, gen)
            assert g.valid(child, FIELDS), child
            assert child[0] in g._CSO and g._depth(child) <= g._MAX_DEPTH
            ax.evaluate(child, panel)                # never produces a broken/look-ahead tree
            nxt.append(child)
        cur = nxt


def test_no_rng_or_wallclock_import():
    import inspect
    src = inspect.getsource(g)
    assert "import random" not in src and "import time" not in src and "Random" not in src


def test_mutation_can_introduce_funding_and_interactions():
    # over the seed population x several steps, the genome reaches funding leaves AND binary interactions
    seeds = g.seed_alphas(FIELDS, WINDOWS)
    fund_seen = inter_seen = False
    for s in seeds:
        for step in range(8):
            m = g.mutate(s, FIELDS, WINDOWS, step)
            sm = ax.serialize(m)
            if "field funding" in sm:
                fund_seen = True
            if any(("(" + op + " ") in sm for op in g._E2):
                inter_seen = True
    assert fund_seen and inter_seen
