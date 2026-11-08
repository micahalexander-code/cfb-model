"""Confidence score (0-100): how much we trust a pick to cover its TEASED line.

Two parts, 50 points each, both built from finished games (History):

  Team score  = 50 x the team's win rate on teased lines when we picked them.
                1-0 -> 50, 1-1 -> 25, 0-1 -> 0. Pushes don't count.
  Edge score  = 50 x the teaser win rate of the edge range the pick falls in
                (same ranges as the History tab: 0-1, 1-2, 2-3, 3+, Core Model
                picks only). 100% -> 50.

With no graded games yet (team never picked, or an empty edge range) that half
is a neutral 25 - a coin flip - until data comes in.
"""
from typing import Dict, List, Optional

POINTS = 50.0
NEUTRAL = POINTS / 2
BUCKETS = [(0, 1), (1, 2), (2, 3), (3, None)]


def bucket_label(lo, hi) -> str:
    return f"{lo}+" if hi is None else f"{lo}–{hi}"


def bucket_of(edge: float):
    e = abs(edge)
    for lo, hi in BUCKETS:
        if e >= lo and (hi is None or e < hi):
            return lo, hi
    return BUCKETS[-1]


def _record(rows: List[dict]) -> dict:
    w = sum(r.get("teaser_result") == "WIN" for r in rows)
    l = sum(r.get("teaser_result") == "LOSS" for r in rows)
    p = sum(r.get("teaser_result") == "PUSH" for r in rows)
    rate = w / (w + l) if w + l else None
    return {"w": w, "l": l, "p": p, "rate": rate,
            "points": round(POINTS * rate, 1) if rate is not None else NEUTRAL}


def is_core(r: dict) -> bool:
    return r["action"] == "SPREAD" and "DISAGREE" not in (r.get("flags") or [])


def edge_table(history: List[dict]) -> List[dict]:
    core = [r for r in history if is_core(r) and r.get("teaser_result")]
    out = []
    for lo, hi in BUCKETS:
        rows = [r for r in core if bucket_of(r["edge"]) == (lo, hi)]
        out.append(dict(_record(rows), label=bucket_label(lo, hi), lo=lo, hi=hi))
    return out


def team_table(history: List[dict]) -> Dict[str, dict]:
    """Teased-line record for every team we've picked (Core Model and Vegas
    Knows Something picks; both have a teased line)."""
    by_team: Dict[str, List[dict]] = {}
    for r in history:
        if r["action"] == "SPREAD" and r.get("teaser_result"):
            by_team.setdefault(r["side"], []).append(r)
    return {t: _record(rows) for t, rows in sorted(by_team.items())}


def score(side: str, edge: float, teams: Dict[str, dict], edges: List[dict]) -> dict:
    t = teams.get(side)
    lo, hi = bucket_of(edge)
    e = next(b for b in edges if (b["lo"], b["hi"]) == (lo, hi))
    team_pts = t["points"] if t else NEUTRAL
    return {"team_pts": team_pts, "team_record": t, "edge_range": e["label"],
            "edge_pts": e["points"], "confidence": round(team_pts + e["points"], 1)}
