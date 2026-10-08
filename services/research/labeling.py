"""Triple-barrier labels + meta-labeling (label engineering for the research layer).

WHY this exists: "what you predict" matters as much as the factor itself. A fixed-horizon
return label ignores the path (a +10% that first drew down -8% is not the same trade). The
TRIPLE-BARRIER label (Lopez de Prado) instead asks which of three barriers is hit first:
  upper (profit target, +pt_mult x vol) -> +1
  lower (stop,         -sl_mult x vol) -> -1
  vertical (max_hold bars)             ->  0   (timed out, no clear outcome)
The barriers scale with the entry's TRAILING realized volatility, which is knowable at the
entry (no lookahead); only the outcome path is forward (that is the label, by definition).

META-LABELING: split DIRECTION from BET. The primary signal gives the direction (+1/-1);
the meta layer decides whether acting on it was right (1) or not (0) vs the realized
triple-barrier label. `meta_label_stats` precision = "how often acting on the primary was
correct" — the should-I-bet / how-big signal that lifts precision by filtering weak calls.

Pure-Python stdlib, deterministic, fail-closed (an entry whose volatility or forward path
cannot be honestly computed is skipped, never labelled on a fabricated barrier).
"""
from __future__ import annotations

import statistics
from typing import Optional, Sequence


def _trailing_vol(closes: Sequence[float], end: int, window: int) -> Optional[float]:
    """Realized volatility (pstdev of simple returns) over the `window` returns ending at
    index `end` — uses only closes[:end+1], so it is knowable at the entry. None if the
    history is too short or a zero denominator is hit (fail-closed)."""
    if window < 2 or end < window:
        return None
    rets = []
    for i in range(end - window + 1, end + 1):
        if closes[i - 1] == 0:
            return None
        rets.append(closes[i] / closes[i - 1] - 1.0)
    return statistics.pstdev(rets) if len(rets) >= 2 else None


def triple_barrier_labels(closes: Sequence[float], *, pt_mult: float = 2.0, sl_mult: float = 2.0,
                          max_hold: int = 10, vol_window: int = 20) -> list:
    """Label each assessable entry by the FIRST barrier its forward path touches.
    Returns a list of dicts {entry, label(+1/-1/0), hit('upper'|'lower'|'vertical'),
    bars_held, vol, ret_at_exit} for entries that have both a computable entry-vol and a
    forward path. Deterministic; fail-closed (zero/None vol or no forward path -> skipped)."""
    if pt_mult <= 0 or sl_mult <= 0 or max_hold < 1:
        raise ValueError("pt_mult>0, sl_mult>0, max_hold>=1 required")
    n = len(closes)
    out = []
    for i in range(n - 1):                              # need at least one forward bar
        vol = _trailing_vol(closes, i, vol_window)
        entry = closes[i]
        if vol is None or vol <= 0 or entry <= 0:
            continue
        upper = entry * (1.0 + pt_mult * vol)
        lower = entry * (1.0 - sl_mult * vol)
        vert = min(i + max_hold, n - 1)
        label, hit, bars = 0, "vertical", vert - i
        for j in range(i + 1, vert + 1):
            if closes[j] >= upper:
                label, hit, bars = 1, "upper", j - i
                break
            if closes[j] <= lower:
                label, hit, bars = -1, "lower", j - i
                break
        out.append({
            "entry": i, "label": label, "hit": hit, "bars_held": bars,
            "vol": round(vol, 6), "ret_at_exit": round(closes[i + bars] / entry - 1.0, 6),
        })
    return out


def meta_labels(primary_dirs: Sequence[int], tb_labels: Sequence[int]) -> list:
    """Meta-label per entry: 1 if the primary took a side (dir != 0) AND it matched the
    realized triple-barrier label (a correct, profitable bet), else 0. This is the target a
    secondary 'should I bet?' model would learn."""
    return [1 if (d != 0 and d == lab) else 0 for d, lab in zip(primary_dirs, tb_labels)]


def meta_label_stats(primary_dirs: Sequence[int], tb_labels: Sequence[int]) -> dict:
    """Precision = fraction of TAKEN bets that were correct (the should-I-bet signal);
    coverage = fraction of entries the primary acted on. precision is None when no bet was
    taken (fail-closed — never a fabricated hit rate)."""
    acted = [(d, lab) for d, lab in zip(primary_dirs, tb_labels) if d != 0]
    n = len(list(primary_dirs))
    if not acted:
        return {"n": n, "n_acted": 0, "precision": None, "coverage": 0.0}
    wins = sum(1 for d, lab in acted if d == lab)
    return {"n": n, "n_acted": len(acted), "precision": round(wins / len(acted), 4),
            "coverage": round(len(acted) / n, 4) if n else 0.0}
