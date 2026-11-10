"""Confidence score (0-100): how much we trust a pick to cover its TEASED line.

Two parts, 50 points each, both built from finished games (History):

  Team score  = 50 x the team's win rate against our teased line in EVERY
                game it played with one: when we picked the team it's our
                teaser result; when we picked its opponent it's the reverse
                (our teaser lost -> the team gets a W). 1-0 -> 50, 1-1 -> 25,
                0-1 -> 0. Pushes don't count.
  Edge score  = 50 x the teaser win rate of the edge range of the team's next
                game (or its most recent one if it doesn't play that day)
                (same ranges as the History tab: 0-1, 1-2, 2-3, 3+, Core Model
                picks only). 100% -> 50.

A team needs MIN_GAMES (5) games against our teased line (pushes count)
before it gets a score at all. An edge range with no graded games counts as a
neutral 25 - a coin flip - until data comes in.
"""
from typing import Dict, List, Optional

POINTS = 50.0
NEUTRAL = POINTS / 2
MIN_GAMES = 5  # a team needs this many games against our teased line to get a score
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


FLIP = {"WIN": "LOSS", "LOSS": "WIN", "PUSH": "PUSH"}


def team_games(history: List[dict]) -> Dict[str, List[dict]]:
    """Every teased game per team, from that team's point of view."""
    by_team: Dict[str, List[dict]] = {}
    for r in history:
        if r["action"] != "SPREAD" or not r.get("teaser_result"):
            continue
        opp = r["home"] if r["side"] == r["away"] else r["away"]
        by_team.setdefault(r["side"], []).append(dict(r, team=r["side"], picked=True))
        by_team.setdefault(opp, []).append(dict(r, team=opp, picked=False,
                                                teaser_result=FLIP[r["teaser_result"]]))
    for rows in by_team.values():
        rows.sort(key=lambda r: (r.get("kickoff") or "", r["game_id"]))
    return by_team


def team_table(history: List[dict]) -> Dict[str, dict]:
    return {t: _record(rows) for t, rows in sorted(team_games(history).items())}


def edge_points(edge: Optional[float], edges: List[dict]) -> dict:
    if edge is None:
        return {"edge_range": None, "edge_pts": NEUTRAL}
    lo, hi = bucket_of(edge)
    e = next(b for b in edges if (b["lo"], b["hi"]) == (lo, hi))
    return {"edge_range": e["label"], "edge_pts": e["points"]}
